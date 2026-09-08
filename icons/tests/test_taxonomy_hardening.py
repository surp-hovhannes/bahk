"""General synthetic counterexamples and end-to-end hardening contracts, offline only."""

from collections import Counter
from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hub.services.icon_taxonomy_matching import projection_current
from icons.models import IconAnalysis, IconObservation, IconTaxonomyProjection, IconTaxonomyWork, TaxonomyConcept
from icons.services.taxonomy_evidence import (
    inscription_spans,
    normalize_comparison,
    observation_codes,
    signature_support,
)
from icons.services.taxonomy_freshness import projection_diagnostics
from icons.services.taxonomy_inputs import dependencies_current, versions
from icons.services.taxonomy_pipeline import due_work
from icons.services.taxonomy_rules import validate_assertions
from icons.services.taxonomy_vocabulary import (
    alias,
    canonical_identity,
    catalogue_claims,
    catalogue_sources,
    concept,
    parse,
    resolve,
    source_class,
)
from icons.services.vision_provider import comparison_schema, VisionProvider
from icons.tests import test_taxonomy as fixtures
from icons.tests.taxonomy_fixtures import fresh_observations


def observation(*, names=(), activities=(), depiction="portrait", figures=1):
    return {"depiction": depiction, "figures": figures, "observations": fresh_observations(names, activities)}


def entry(pk, *, refs=(), agrees=True, conflict=False, inference="Context only"):
    return dict(concept=pk, agrees=agrees, conflict=conflict, observation_ids=list(refs), inference=inference)


class LiteralEvidenceTests(SimpleTestCase):
    def test_action_object_relationship_and_paraphrases(self):
        positives = {
            "washing_feet": [
                "A kneeling attendant washes a seated man's foot beside a basin.",
                "One person pours water over another figure's feet.",
                "The raised foot is being washed by a kneeling person.",
            ],
            "shared_supper": [
                "Several figures share a meal around a table.",
                "A group is seated together around a table with bread and food.",
            ],
            "bread_and_cup": ["Several loaves and a chalice are arranged on the tabletop."],
            "praying": [
                "Her hands are clasped together in prayer.",
                "The figure's raised open hands and upward-facing posture are visible.",
            ],
        }
        negatives = {
            "washing_feet": [
                "Washing hands beside another figure whose feet are visible.",
                "A figure washes a robe while another man's feet are visible.",
                "A foot rests beside a basin while a figure pours water onto his hands.",
                "A figure appears to wash another person's foot.",
                "No one is washing feet.",
                "washing_feet",
            ],
            "shared_supper": [
                "Several figures stand around an empty table.",
                "A single figure carries food past a table.",
                "shared_supper",
            ],
            "bread_and_cup": ["A cup stands beside an empty dish."],
            "praying": ["A bowed figure sits with arms extended.", "praying"],
        }
        for code, texts in positives.items():
            for text in texts:
                with self.subTest(code=code, text=text):
                    obs = dict(kind="activity", text=text, region="center", code=code, uncertain=False)
                    self.assertIn(code, observation_codes(obs))
                    self.assertEqual(observation_codes({**obs, "uncertain": True}), set())
        for code, texts in negatives.items():
            for text in texts:
                with self.subTest(code=code, text=text):
                    self.assertNotIn(
                        code, observation_codes(dict(kind="activity", text=text, region="center", code=code))
                    )

    def test_legacy_quotes_preserve_ambiguity_and_no_fuzzy_name(self):
        base = dict(kind="inscription", readable=True, region="top")
        self.assertEqual(
            inscription_spans({**base, "text": "The name reads «Basil of Caesarea» above the figure."}),
            ["Basil of Caesarea"],
        )
        self.assertEqual(inscription_spans({**base, "text": "Perhaps the name reads «Basil of Caesarea»."}), [])
        self.assertEqual(inscription_spans({**base, "text": "«Basil»", "uncertain": True}), [])
        self.assertEqual(inscription_spans({**base, "text": "«Basil»", "readable": False}), [])

    def test_closed_keyed_contract_and_legacy_entry_normalization(self):
        obs = observation(names=["Basil of Caesarea"])
        claims = [{"concept": 101}, {"concept": 202}]
        schema = comparison_schema(claims, obs)
        self.assertEqual(set(schema["properties"]["assertions"]["properties"]), {"101", "202"})
        self.assertFalse(schema["properties"]["assertions"]["additionalProperties"])
        fields = schema["properties"]["assertions"]["properties"]["101"]["properties"]
        self.assertEqual(fields["observation_ids"]["items"]["enum"], ["n0"])
        raw = {
            "assertions": [
                entry(101, refs=["n0"]),
                entry(101, refs=["n0"], inference="Different prose, same evidence"),
                entry(202),
                entry(202, agrees=False, conflict=True),
                entry(999),
                entry(101, refs=["absent"]),
                {"bad": True},
            ]
        }
        accepted, conflicted, diagnostics = normalize_comparison(claims, obs, raw)
        self.assertEqual(set(accepted), {101})
        self.assertEqual(conflicted, {202})
        self.assertEqual(
            Counter(d["code"] for d in diagnostics),
            Counter(
                {
                    "accepted_comparison_entry": 2,
                    "coalesced_comparison_duplicate": 1,
                    "conflicting_comparison_duplicate": 1,
                    "unknown_comparison_concept": 1,
                    "unknown_comparison_observation": 1,
                    "malformed_comparison_entry": 1,
                }
            ),
        )
        self.assertEqual(diagnostics[2]["disposition"], "claim_unknown")
        self.assertEqual(raw["assertions"][0], entry(101, refs=["n0"]))
        empty = comparison_schema([{"concept": 101}], observation())
        self.assertEqual(
            empty["properties"]["assertions"]["properties"]["101"]["properties"]["observation_ids"]["maxItems"], 0
        )
        with self.assertRaises(ValueError):
            normalize_comparison(claims, obs, {"assertions": [entry(101)] * 65})


@override_settings(
    STORAGES=fixtures.STORAGES,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
    ICON_TAXONOMY_DISPATCH_ENABLED=False,
    ICON_TAXONOMY_BUDGET="offline",
    ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS=100,
)
class HardeningTests(TransactionTestCase):
    setUp = fixtures.TaxonomyTests.setUp
    icon = fixtures.TaxonomyTests.icon
    due = fixtures.TaxonomyTests.due
    analyze = fixtures.TaxonomyTests.analyze
    matches = fixtures.TaxonomyTests.matches

    def test_single_certain_foot_washing_and_suggestive_controls(self):
        base = dict(
            id="a0",
            kind="activity",
            code="washing_feet",
            readable=False,
            uncertain=False,
            literal_spans=[],
            region="lower center",
            text="A kneeling attendant pours water from a pitcher over the bare foot of a seated figure into a basin.",
        )
        event = resolve("Washing of Feet")
        themes = {resolve("service").pk, resolve("humility").pk}
        cases = [
            ({}, True),
            ({"text": "Water is being poured over a seated figure's foot into a basin."}, True),
            ({"uncertain": True}, False),
            ({"text": "Water is possibly poured over a seated figure's foot into a basin."}, False),
            ({"text": "Hands are near the seated figure's feet beside a basin."}, False),
            ({"text": "A kneeling figure has a bowed head beside seated figures."}, False),
            ({"text": "Washing hands beside another figure whose feet are visible."}, False),
            ({"code": "unknown", "text": "A figure seems to wash a foot beside a basin."}, False),
        ]
        for changes, supported in cases:
            with self.subTest(changes=changes):
                result = validate_assertions(
                    [],
                    {"depiction": "scene", "figures": 2, "observations": [{**base, **changes}]},
                    {"assertions": []},
                )
                accepted = {a["concept"]: a for a in result if a["status"] == "supported"}
                self.assertEqual(event.pk in accepted, supported)
                self.assertEqual(themes <= accepted.keys(), supported)
                if supported:
                    self.assertEqual(accepted[event.pk]["evidence_level"], "observed")
                    self.assertEqual({accepted[pk]["evidence_level"] for pk in themes}, {"inferred"})

    def test_batch_growth_reconciles_retained_evidence_in_either_order(self):
        from icons.management.commands.dispatch_icon_taxonomy import run_inline
        from icons.services.taxonomy_reconciliation import reconcile_selected_projections

        for reverse, name in ((False, "Irene of the Valley"), (True, "Julian of the Mountain")):
            with self.subTest(reverse=reverse):
                outside = self.icon(title="Unlabelled portrait")
                self.analyze(outside, fixtures.FixtureProvider(names=(name,)))
                outside_before = IconTaxonomyProjection.objects.filter(icon=outside).values().get()
                first = self.icon(title="Unlabelled portrait")
                second = self.icon(title="Saint " + name)
                provider = fixtures.FixtureProvider(names=(name,))
                before_reconciliation = {}

                def ordered(ids, **kwargs):
                    order = list(reversed(ids)) if reverse else ids
                    outcomes = dict(zip(order, run_inline(order, **kwargs)))
                    return [outcomes[pk] for pk in ids]

                def reconcile(ids):
                    before_reconciliation["analyses"] = list(IconAnalysis.objects.values())
                    before_reconciliation["calls"] = len(provider.calls)
                    self.budget.refresh_from_db()
                    before_reconciliation["budget"] = (self.budget.calls, self.budget.tokens, self.budget.microdollars)
                    if not reverse:
                        self.assertFalse(
                            dependencies_current(IconTaxonomyProjection.objects.get(icon=first).analysis.dependencies)
                        )
                    result = reconcile_selected_projections(ids)
                    self.assertEqual(len(provider.calls), before_reconciliation["calls"])
                    self.budget.refresh_from_db()
                    self.assertEqual(
                        (self.budget.calls, self.budget.tokens, self.budget.microdollars),
                        before_reconciliation["budget"],
                    )
                    return result

                out = StringIO()
                with (
                    patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider),
                    patch("icons.management.commands.backfill_icon_taxonomy.run_inline", side_effect=ordered),
                    patch(
                        "icons.management.commands.backfill_icon_taxonomy.reconcile_selected_projections",
                        side_effect=reconcile,
                    ),
                ):
                    call_command(
                        "backfill_icon_taxonomy",
                        dispatch=True,
                        enable_inline=True,
                        budget="offline",
                        icon_ids=[first.pk, second.pk],
                        stdout=out,
                    )
                report = json.loads(out.getvalue())
                self.assertEqual(report["summary"], {"processed": 2, "fresh": 2})
                self.assertEqual([row["freshness"] for row in report["rows"]], [[], []])
                self.assertEqual([call[0] for call in provider.calls].count("observe"), 2)
                self.assertEqual([call[0] for call in provider.calls].count("compare"), 1)
                for row in before_reconciliation["analyses"]:
                    self.assertEqual(IconAnalysis.objects.filter(pk=row["id"]).values().get(), row)
                self.assertEqual(IconTaxonomyProjection.objects.filter(icon=outside).values().get(), outside_before)
                for icon in (first, second):
                    icon.refresh_from_db()
                    projection = IconTaxonomyProjection.objects.get(icon=icon)
                    self.assertEqual(projection_diagnostics(icon, projection), [])
                    self.assertTrue(
                        any(
                            a["concept"] == resolve(name).pk and a["status"] == "supported"
                            for a in projection.attributes
                        )
                    )

    def test_dependency_reconciliation_accepts_legacy_empty_comparison(self):
        from icons.services.taxonomy_reconciliation import reconcile_selected_projections

        icon = self.icon(title="Unlabelled portrait")
        self.analyze(icon, fixtures.FixtureProvider(names=("Irene of the Valley",)))
        projection = IconTaxonomyProjection.objects.get(icon=icon)
        IconAnalysis.objects.filter(pk=projection.analysis_id).update(comparison={})
        catalogue_claims({"title": "Saint Irene of the Valley", "tags": [], "filename": ""})
        projection.refresh_from_db()
        self.assertEqual(projection_diagnostics(icon, projection), ["dependency_changed"])

        self.assertEqual(reconcile_selected_projections([icon.pk]), [icon.pk])

        projection.refresh_from_db()
        self.assertEqual(projection_diagnostics(icon, projection), [])
        self.assertEqual(projection.analysis.comparison, {})

    def test_dependency_reconciliation_does_not_claim_active_or_incomplete_work(self):
        from icons.services.taxonomy_reconciliation import reconcile_selected_projections

        icon = self.icon(title="Unlabelled portrait")
        self.analyze(icon, fixtures.FixtureProvider(names=("Irene of the Valley",)))
        catalogue_claims({"title": "Saint Irene of the Valley", "tags": [], "filename": ""})
        before = IconTaxonomyProjection.objects.filter(icon=icon).values().get()
        for state in ("inline_running", "running", "inline_pending", "budget_blocked", "unavailable", "retry"):
            with self.subTest(state=state):
                IconTaxonomyWork.objects.filter(icon=icon).update(
                    state=state, lease_token="other-owner", lease_until=timezone.now() + timedelta(minutes=5)
                )
                work = IconTaxonomyWork.objects.filter(icon=icon).values().get()
                self.assertEqual(reconcile_selected_projections([icon.pk]), [])
                self.assertEqual(IconTaxonomyWork.objects.filter(icon=icon).values().get(), work)
                self.assertEqual(IconTaxonomyProjection.objects.filter(icon=icon).values().get(), before)
        self.assertEqual(icon.taxonomic_analyses.count(), 1)

    def test_qualified_places_roles_and_provenance_holdouts(self):
        for title in ("Saint Cyril of Jerusalem", "Saint Basil of Caesarea", "Saint Julian of the Cathedral"):
            claims = catalogue_claims({"title": title, "tags": [], "filename": ""})
            self.assertEqual(source_class(title), "identity")
            self.assertEqual(len(claims), 1)
            self.assertTrue(resolve(title).definition["qualified"])
        self.assertNotEqual(
            canonical_identity("John the Bishop of Antioch"), canonical_identity("John the Patriarch of Antioch")
        )
        self.assertEqual(canonical_identity("Bishop John of Antioch"), canonical_identity("John the Bishop of Antioch"))
        self.assertEqual(
            canonical_identity("Saint John of Bishop of Antioch"), canonical_identity("John the Bishop of Antioch")
        )
        self.assertNotEqual(canonical_identity("John II of Antioch"), canonical_identity("John III of Antioch"))
        for tag in (
            "Armenian Orthodox",
            "iconographic style",
            "heavenly vision",
            "St Julian TX",
            "St-Mark-UK",
            "Saint Mark Cathedral",
        ):
            parsed = catalogue_sources({"title": "generic", "tags": ["portrait", tag], "filename": ""}, create=True)[
                -1
            ]["parsed"]
            self.assertFalse(parsed["subjects"], tag)
        for text in (
            "Agony in the Garden of Olives",
            "Jesus Washing the Disciples' Feet",
            "A traveler wading through reeds",
            "Saint Basil maybe",
        ):
            self.assertFalse(parse(text, create=True)["subjects"], text)

    def test_alias_depiction_markers_filename_noise_and_unknown_qualifiers(self):
        for text in ("Saint Mary Mother of God", "Portrait of Theotokos", "Icon of Christ Pantocrator"):
            parsed = parse(text)
            self.assertEqual(len(parsed["subjects"]), 1)
            self.assertFalse(parsed["unresolved"])
        source = catalogue_sources(
            {
                "title": "Saint Basil of Caesarea",
                "tags": [],
                "filename": "saint-basil-of-caesarea-photopic293we_X7abC29.png",
            },
            create=True,
        )[-1]
        self.assertEqual(source["parsed"]["subjects"], parse("Saint Basil of Caesarea")["subjects"])
        self.assertEqual(source["transformations"], ["file_extension", "storage_random_suffix", "product_suffix"])
        before = TaxonomyConcept.objects.count()
        noisy = catalogue_sources(
            {"title": "generic", "tags": [], "filename": "saint-basil-of-distant-coast-203.png"}, create=True
        )[-1]
        self.assertTrue(noisy["parsed"]["unresolved"])
        self.assertIn("203", noisy["identity_text"])
        self.assertEqual(TaxonomyConcept.objects.count(), before)
        self.assertFalse(parse("Saint Basil of Unknown Province")["subjects"])
        self.assertTrue(parse("Saint Basil of Unknown Province")["unresolved"])

    def test_bare_marked_group_suggestions_never_identity_upgrade(self):
        icon = self.icon(title="Saint Marina and Saint Daria")
        provider = fixtures.FixtureProvider(names=("Marina", "Daria"))
        self.analyze(icon, provider)
        analysis = icon.taxonomic_analyses.get(state="complete")
        subjects = analysis.assertions.filter(attribute="subject")
        self.assertEqual(subjects.count(), 2)
        self.assertTrue(all(a.evidence_level == "metadata" for a in subjects))
        results = self.matches("Saint Marina and Saint Daria").matches
        self.assertTrue(results)
        self.assertFalse(any(m["auto_assignable"] for m in results))
        catalogue_claims({"title": "Saint Marina of Antioch", "tags": [], "filename": ""})
        self.assertNotEqual(resolve("Marina").pk, resolve("Marina of Antioch").pk)

    def test_missing_identity_and_context_conflict_do_not_disprove_participation(self):
        icon = self.icon(title="Saint Basil of Caesarea")

        class ConflictProvider(fixtures.FixtureProvider):
            def call(self, stage, *args, **kwargs):
                value, model, usage = super().call(stage, *args, **kwargs)
                if stage == "compare":
                    for assertion in value["assertions"].values():
                        assertion.update(
                            agrees=False, conflict=True, observation_ids=["a0"], inference="No identifying name"
                        )
                return value, model, usage

        self.analyze(icon, ConflictProvider(names=(), depiction="scene", activities=("washing_feet",)))
        assertions = icon.taxonomic_analyses.get(state="complete").assertions
        self.assertEqual(assertions.get(concept=resolve("Basil of Caesarea")).status, "supported")
        self.assertEqual(assertions.get(concept=resolve("service")).evidence_level, "inferred")
        self.assertFalse(assertions.filter(status="contradicted").exists())
        self.assertFalse(any(m["auto_assignable"] for m in self.matches("Basil of Caesarea").matches))

    def test_positive_inscription_unreadable_ambiguity_and_signature_collisions(self):
        claims = catalogue_claims({"title": "Saint Basil of Caesarea", "tags": [], "filename": ""})
        obs = observation(names=["Basil of Caesarea"])
        obs["observations"] += [
            dict(
                id="u",
                kind="inscription",
                text="Unreadable marks",
                region="edge",
                readable=False,
                uncertain=True,
                code="unknown",
                literal_spans=[],
            )
        ]
        assertions = validate_assertions(claims, obs, {"assertions": []})
        self.assertEqual(
            next(a for a in assertions if a["concept"] == claims[0]["concept"])["evidence_level"], "observed"
        )
        obs["observations"].pop()
        self.assertEqual(
            next(
                a for a in validate_assertions(claims, obs, {"assertions": []}) if a["concept"] == claims[0]["concept"]
            )["evidence_level"],
            "corroborated",
        )
        a = concept(
            "subject",
            "Synthetic Keeper of the Gate",
            {
                "qualified": True,
                "visual_signatures": [
                    {
                        "all": ["synthetic_shield", "synthetic_staff"],
                        "source": {"fixture": "synthetic signature collision"},
                    }
                ],
            },
        )
        b = concept("subject", "Synthetic Keeper of the Hill", deepcopy(a.definition))
        observations = {
            "s": dict(id="s", kind="object", text="A square shield has three blue dots.", uncertain=False),
            "t": dict(id="t", kind="object", text="A split amber staff is held in the left hand.", uncertain=False),
        }
        with patch(
            "icons.services.taxonomy_evidence.DISTINCTIVE_FEATURES",
            {"synthetic_shield": r"square shield has three blue dots", "synthetic_staff": r"split amber staff"},
        ):
            self.assertEqual(signature_support({a.pk: a, b.pk: b}, observations), {})
            self.assertEqual(set(signature_support({a.pk: a}, observations)), {a.pk})
        self.assertEqual(signature_support({a.pk: a}, observations), {})
        a.definition["visual_signatures"][0]["all"] = ["shared_supper", "bread_and_cup"]
        generic = {o["id"]: o for o in fresh_observations([], ["shared_supper", "bread_and_cup"])}
        self.assertEqual(signature_support({a.pk: a}, generic), {})

    def test_conflicting_duplicates_unknown_claim_but_observations_survive(self):
        icon = self.icon()

        class DuplicateProvider(fixtures.FixtureProvider):
            def call(self, stage, *args, **kwargs):
                value, model, usage = super().call(stage, *args, **kwargs)
                if stage == "compare":
                    pk = int(next(iter(value["assertions"])))
                    value = {"assertions": [entry(pk), entry(pk, agrees=False, conflict=True), entry(1000000)]}
                return value, model, usage

        self.analyze(icon, DuplicateProvider(activities=("washing_feet",)))
        analysis = icon.taxonomic_analyses.get(state="complete")
        self.assertEqual(analysis.assertions.get(concept=resolve(fixtures.BAPTIST)).status, "unknown")
        self.assertEqual(analysis.assertions.get(concept=resolve("service")).status, "supported")
        self.assertEqual(len(analysis.comparison["assertions"]), 3)
        self.assertFalse(self.matches(fixtures.BAPTIST).matches)

    def test_empty_claim_pipeline_skips_compare_and_preserves_nonfestal_theme(self):
        icon = self.icon(title="generic")
        provider = self.analyze(
            icon, fixtures.FixtureProvider(names=(), depiction="scene", activities=("washing_feet",))
        )
        self.assertEqual([call[0] for call in provider.calls], ["observe"])
        self.assertTrue(self.matches("service", kind="content").matches)
        self.assertFalse(self.matches("Saint Unknown of the Coast", kind="content").matches)

    def test_invalid_options_and_disabled_budget_fail_before_writes(self):
        icon = self.icon()
        before = list(IconTaxonomyWork.objects.values())
        cases = [
            dict(concurrency=0),
            dict(after_id=-1),
            dict(church=0),
            dict(limit=0),
            dict(icon_ids=[icon.pk, icon.pk]),
            dict(icon_ids=[icon.pk], after_id=1),
            dict(max_calls=2, max_tokens=100, max_spend="NaN"),
            dict(model="unverified"),
            dict(enable_inline=True),
            dict(recover_unavailable=True),
        ]
        with patch.object(VisionProvider, "call") as provider:
            for options in cases:
                with self.subTest(options=options), CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(CommandError):
                        call_command("backfill_icon_taxonomy", stdout=StringIO(), **options)
                    self.assertFalse(
                        any(q["sql"].lstrip().split()[0] in {"INSERT", "UPDATE", "DELETE"} for q in queries)
                    )
            self.budget.enabled = False
            self.budget.save()
            with self.assertRaises(CommandError):
                call_command(
                    "backfill_icon_taxonomy",
                    dispatch=True,
                    enable_inline=True,
                    budget="offline",
                    icon_ids=[icon.pk],
                    max_calls=self.budget.max_calls,
                    max_tokens=self.budget.max_tokens,
                    max_spend=self.budget.max_microdollars / 1000000,
                    stdout=StringIO(),
                )
            provider.assert_not_called()
        self.assertEqual(list(IconTaxonomyWork.objects.values()), before)

    def test_status_and_dry_run_no_writes_no_calls(self):
        icon = self.icon()
        for options in (
            dict(status=True),
            dict(
                dry_run=True,
                dispatch=True,
                enable_inline=True,
                budget="new",
                max_calls=4,
                max_tokens=1000000,
                max_spend=100,
            ),
        ):
            with patch.object(VisionProvider, "call") as provider, CaptureQueriesContext(connection) as queries:
                out = StringIO()
                call_command("backfill_icon_taxonomy", icon_ids=[icon.pk], stdout=out, **options)
                self.assertEqual(json.loads(out.getvalue())["rows"][0]["before_state"], "pending")
                self.assertFalse(any(q["sql"].lstrip().split()[0] in {"INSERT", "UPDATE", "DELETE"} for q in queries))
                provider.assert_not_called()
        with patch("icons.services.taxonomy_inputs.image_input", side_effect=AssertionError("status reads storage")):
            call_command("backfill_icon_taxonomy", status=True, icon_ids=[icon.pk], stdout=StringIO())

    def test_inline_exact_scope_no_background_wake_and_final_json(self):
        selected, unrelated = self.icon(), self.icon(church=self.other)
        IconTaxonomyWork.objects.filter(icon=selected).delete()
        before_other = IconTaxonomyWork.objects.filter(icon=unrelated).values().get()
        provider = fixtures.FixtureProvider()

        def check(stage, payload):
            self.assertNotIn(selected.pk, due_work().values_list("icon_id", flat=True))
            self.assertEqual(IconTaxonomyWork.objects.get(icon=selected).state, "inline_running")

        provider.mutate = check
        out = StringIO()
        with (
            patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider),
            patch("icons.services.ingestion.wake_dispatcher") as wake,
        ):
            call_command(
                "backfill_icon_taxonomy",
                dispatch=True,
                enable_inline=True,
                budget="offline",
                icon_ids=[selected.pk],
                church=self.church.pk,
                stdout=out,
            )
            wake.assert_not_called()
        report = json.loads(out.getvalue())
        row = report["rows"][0]
        self.assertEqual(
            (row["before_state"], row["after_state"], row["processing_result"]), ("missing", "complete", "complete")
        )
        self.assertEqual(row["freshness"], [])
        self.assertEqual(report["summary"], {"processed": 1, "fresh": 1})
        self.assertEqual(IconTaxonomyWork.objects.filter(icon=unrelated).values().get(), before_other)
        self.assertFalse(__import__("django.conf", fromlist=["settings"]).settings.ICON_TAXONOMY_DISPATCH_ENABLED)

    def test_inline_retry_and_crash_resume_never_wake_other_budget(self):
        icon = self.icon()
        provider = fixtures.FixtureProvider(fail_stage="compare")
        with patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider):
            out = StringIO()
            with self.assertRaises(CommandError):
                call_command(
                    "backfill_icon_taxonomy",
                    dispatch=True,
                    enable_inline=True,
                    budget="offline",
                    icon_ids=[icon.pk],
                    stdout=out,
                )
            self.assertEqual(json.loads(out.getvalue())["summary"], {"failed": 1, "stale": 1})
        self.assertEqual(IconTaxonomyWork.objects.get(icon=icon).state, "inline_retry")
        self.assertNotIn(icon.pk, due_work().values_list("icon_id", flat=True))
        provider.fail_stage = None
        IconTaxonomyWork.objects.filter(icon=icon).update(available_at=timezone.now() - timedelta(seconds=1))
        with patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider):
            call_command(
                "backfill_icon_taxonomy",
                dispatch=True,
                enable_inline=True,
                resume_inline=True,
                budget="offline",
                icon_ids=[icon.pk],
                stdout=StringIO(),
            )
        self.assertEqual([c[0] for c in provider.calls], ["observe", "compare", "compare"])
        self.assertEqual(IconTaxonomyWork.objects.get(icon=icon).state, "complete")

    def test_recovery_new_rules_preserves_audit_and_cached_observation(self):
        icon = self.icon()
        provider = self.analyze(icon)
        old = icon.taxonomic_analyses.get()
        old_snapshot = IconAnalysis.objects.filter(pk=old.pk).values().get()
        with (
            patch("icons.services.taxonomy_inputs.RULES", "synthetic-next-rule"),
            patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider),
        ):
            call_command(
                "backfill_icon_taxonomy",
                dispatch=True,
                enable_inline=True,
                recover_unavailable=True,
                budget="offline",
                icon_ids=[icon.pk],
                stdout=StringIO(),
            )
        self.assertEqual([c[0] for c in provider.calls], ["observe", "compare", "compare"])
        self.assertEqual(IconAnalysis.objects.filter(pk=old.pk).values().get(), old_snapshot)
        self.assertEqual(IconObservation.objects.count(), 1)
        self.assertEqual(IconAnalysis.objects.count(), 2)

    def test_freshness_shared_all_components_and_unrelated_growth(self):
        icon = self.icon()
        self.analyze(icon)
        icon.refresh_from_db()
        projection = IconTaxonomyProjection.objects.select_related("analysis").get(icon=icon)
        self.assertEqual(projection_diagnostics(icon, projection), [])
        self.assertTrue(projection_current(icon, projection))
        snapshot = deepcopy(projection.analysis.dependencies)
        catalogue_claims({"title": "Saint Irene of the Valley", "tags": [], "filename": ""})
        self.assertTrue(dependencies_current(snapshot))
        projection.analysis.versions = {**projection.analysis.versions, "rules": "old", "model": "old", "schema": "old"}
        projection.analysis.image_digest = "changed"
        projection.analysis.claims["metadata"]["image_revision"] = 999
        projection.analysis.dependencies = {}
        projection.revision += 1
        projection.fingerprint = "changed"
        codes = set(projection_diagnostics(icon, projection))
        self.assertTrue(
            {
                "version_rules_mismatch",
                "version_model_mismatch",
                "version_schema_mismatch",
                "image_digest_mismatch",
                "image_revision_mismatch",
                "dependency_changed",
                "work_revision_mismatch",
                "metadata_fingerprint_mismatch",
            }
            <= codes
        )
        self.assertFalse(projection_current(icon, projection))
        self.assertEqual(projection_diagnostics(icon, None), ["missing_projection"])

    def test_model_scoped_profiles_price_floor_and_no_cross_model_observation(self):
        from icons.services.taxonomy_budget import BudgetExhausted, estimate_reservation

        icon = self.icon()
        provider = self.analyze(icon)
        with override_settings(ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS=1.2):
            with self.assertRaises(CommandError):
                call_command(
                    "backfill_icon_taxonomy",
                    dispatch=True,
                    enable_inline=True,
                    budget="offline",
                    icon_ids=[icon.pk],
                    model="gpt-5.6-terra",
                    stdout=StringIO(),
                )
            with override_settings(ICON_TAXONOMY_MODEL="gpt-5.6-terra"), self.assertRaises(BudgetExhausted):
                estimate_reservation({})
        with patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider):
            call_command(
                "backfill_icon_taxonomy",
                dispatch=True,
                enable_inline=True,
                recover_unavailable=True,
                budget="offline",
                icon_ids=[icon.pk],
                model="gpt-5.6-terra",
                stdout=StringIO(),
            )
        self.assertEqual([c[0] for c in provider.calls], ["observe", "compare", "observe", "compare"])
        latest = icon.taxonomic_analyses.order_by("-created_at").first()
        self.assertEqual(latest.versions["profile"], "terra-none-v1")
        self.assertEqual(versions()["profile"], "luna-none-v1")
        self.assertEqual(IconObservation.objects.count(), 2)
        tokens, _ = estimate_reservation({}, schema={"large": "x" * 10000})
        self.assertGreater(tokens, 18000)

    def test_inline_resume_is_owned_during_next_row_reconciliation(self):
        from icons.services.ingestion import INLINE_OWNED, refresh_content

        for state, flag in (
            ("budget_blocked", "resume_budget_blocked"),
            ("inline_retry", "resume_inline"),
            ("pending", None),
        ):
            first, second, unrelated = self.icon(), self.icon(), self.icon(church=self.other)
            past = timezone.now() - timedelta(minutes=20)
            IconTaxonomyWork.objects.filter(icon=first).update(state=state, available_at=past, lease_until=past)
            IconTaxonomyWork.objects.filter(icon__in=[second, unrelated]).update(available_at=past)
            seen = []

            def reconcile(icon):
                seen.append(icon.pk)
                work = IconTaxonomyWork.objects.get(icon=first)
                self.assertEqual(work.state, "inline_pending")
                self.assertEqual(work.lease_token, INLINE_OWNED.get())
                due = set(due_work().values_list("icon_id", flat=True))
                self.assertFalse({first.pk, second.pk} & due)
                self.assertIn(unrelated.pk, due)
                return refresh_content(icon)

            options = {flag: True} if flag else {}
            with (
                patch("icons.management.commands.backfill_icon_taxonomy.refresh_content", side_effect=reconcile),
                patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=fixtures.FixtureProvider()),
            ):
                call_command(
                    "backfill_icon_taxonomy",
                    dispatch=True,
                    enable_inline=True,
                    budget="offline",
                    icon_ids=[first.pk, second.pk],
                    stdout=StringIO(),
                    **options,
                )
            self.assertEqual(seen, [first.pk, second.pk])
        blocked = self.icon()
        IconTaxonomyWork.objects.filter(icon=blocked).update(state="budget_blocked")
        call_command("backfill_icon_taxonomy", resume_budget_blocked=True, icon_ids=[blocked.pk], stdout=StringIO())
        self.assertIn(blocked.pk, due_work().values_list("icon_id", flat=True))
        self.assertEqual(IconTaxonomyWork.objects.get(icon=blocked).state, "pending")

    def test_malformed_neighbor_diagnostics_preserve_valid_pipeline_provenance(self):
        for malformed in ([], {}, [1, 2]):
            claims = [{"concept": 101}]
            raw = {"assertions": [entry(101), entry(malformed)]}
            saved = deepcopy(raw)
            accepted, conflicts, diagnostics = normalize_comparison(claims, observation(), raw)
            self.assertEqual(set(accepted), {101})
            self.assertEqual(conflicts, set())
            self.assertEqual(diagnostics[-1]["code"], "malformed_comparison_entry")
            self.assertEqual(raw, saved)
        icon = self.icon()

        class MalformedProvider(fixtures.FixtureProvider):
            def call(self, stage, *args, **kwargs):
                value, model, usage = super().call(stage, *args, **kwargs)
                if stage == "compare":
                    pk = next(iter(value["assertions"]))
                    value["assertions"]["999"] = {**value["assertions"][pk], "concept": int(pk)}
                return value, model, usage

        self.analyze(icon, MalformedProvider())
        analysis = icon.taxonomic_analyses.get(state="complete")
        self.assertIn("concept", analysis.comparison["assertions"]["999"])
        self.assertIn("malformed_comparison_entry", {d["code"] for d in analysis.comparison["diagnostics"]})
        self.assertTrue(analysis.assertions.filter(status="supported").exists())
        raw = {"assertions": {"101": {**entry(202)}}}
        accepted, _, diagnostics = normalize_comparison([{"concept": 101}], observation(), raw)
        self.assertFalse(accepted)
        self.assertEqual(diagnostics[0]["disposition"], "discarded")
        self.assertEqual(raw["assertions"]["101"]["concept"], 202)

    def test_sourced_role_alias_roundtrip_inscription_and_dependency(self):
        subject = concept("subject", "Basil of the Coast", {"qualified": True})
        literal = "Bishop Basil of the Coast"
        alias(subject, literal, source={"fixture": "explicit sourced title equivalence"})
        self.assertEqual(resolve(literal), subject)
        self.assertEqual(resolve("Bishop_Basil_of_the_Coast"), subject)
        self.assertEqual(subject.aliases.get(text=literal).normalized, canonical_identity(literal))
        self.assertNotEqual(canonical_identity(literal), canonical_identity("Patriarch Basil of the Coast"))
        icon = self.icon(title=literal)
        self.analyze(icon, fixtures.FixtureProvider(names=[literal]))
        analysis = icon.taxonomic_analyses.get(state="complete")
        self.assertEqual(analysis.assertions.get(concept=subject).evidence_level, "corroborated")
        self.assertEqual(analysis.observation.evidence["observations"][0]["literal_spans"], [literal])
        self.assertIn(canonical_identity(literal), analysis.dependencies["terms"])
        self.assertTrue(self.matches(literal).matches[0]["auto_assignable"])

    def test_unqualified_tag_strength_and_qualified_separator_holdouts(self):
        title_icon = self.icon(title="Saint Marina", tags=["st-julian"])
        scene = self.icon(title="Theophany", tags=["st-julian", "st-julian-tx", "portrait"])
        qualified_icon = self.icon(title="generic", tags=["st-cyril-the-philosopher", "Bishop_Mark_of_the_Coast"])
        for icon in (title_icon, scene, qualified_icon):
            self.analyze(icon, fixtures.FixtureProvider(names=(), depiction="portrait"))
        assertions = title_icon.taxonomic_analyses.get(state="complete").assertions
        self.assertEqual(assertions.get(concept=resolve("Marina")).status, "supported")
        self.assertEqual(assertions.get(concept=resolve("Julian")).status, "unknown")
        self.assertFalse(
            scene.taxonomic_analyses.get(state="complete")
            .assertions.filter(attribute="subject", status="supported")
            .exists()
        )
        # Independent qualified tags remain useful when there is no competing title.
        for label in ("Saint Cyril the Philosopher", "Bishop Mark of the Coast"):
            self.assertIsNotNone(resolve(label))
        for text in ("st-julian-TX", "Saint_Mark_Cathedral", "artist-Marcus-of-the-coast"):
            self.assertFalse(parse(text, create=True)["subjects"], text)
        lone = self.icon(title="generic", tags=["st-cyril-the-philosopher"])
        self.analyze(lone, fixtures.FixtureProvider(names=()))
        self.assertEqual(
            lone.taxonomic_analyses.get(state="complete")
            .assertions.get(concept=resolve("Cyril the Philosopher"))
            .status,
            "supported",
        )
        pair = self.icon(title="Saint Marina and Saint Julian", tags=["st-julian"])
        self.analyze(pair, fixtures.FixtureProvider(names=()))
        self.assertEqual(
            pair.taxonomic_analyses.get(state="complete").assertions.get(concept=resolve("Julian")).status, "supported"
        )
