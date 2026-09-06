"""Offline matching, assignment, request-adapter and evaluation invariants.

Pure ingestion/provider/budget tests live in test_taxonomy_backfill. This module
keeps its own lightweight fixture setup to avoid collecting that TestCase twice."""

import json
import tempfile
import time
from copy import deepcopy
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from hub.models import Church
from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_match_service import IconMatchOutcome, MatchLimits
from hub.services.icon_taxonomy_matching import match_icons, interpret, assignment_current
from icons.models import (
    Icon,
    IconObservation,
    IconTaxonomyProjection,
    IconTaxonomyWork,
    TaxonomyBudget,
    TaxonomyConcept,
)
from icons.services.ingestion import ingest_icon, schedule, refresh_content, reconcile_versions
from icons.services.taxonomy_inputs import dependencies_current
from icons.services.taxonomy_pipeline import process_icon
from icons.services.taxonomy_vocabulary import alias, catalogue_claims, concept, resolve, seed_vocabulary
from icons.services.vision_provider import VisionProvider

BAPTIST = "John the Baptist"
EVANGELIST = "John the Evangelist"
PAIR = BAPTIST + " and " + EVANGELIST
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def upload(name="upload.png", color=(30, 90, 120)):
    output = BytesIO()
    Image.new("RGB", (40, 40), color).save(output, "PNG")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


class FixtureProvider:
    def __init__(self, names=(BAPTIST,), depiction="portrait", activities=(), mutate=None, fail_stage=None):
        self.names, self.depiction, self.activities = names, depiction, activities
        self.mutate, self.fail_stage = mutate, fail_stage
        self.calls = []

    def call(self, stage, payload, schema, *, image=None, timeout=None):
        assert not connection.in_atomic_block, "Provider work must not hold a transaction"
        self.calls.append((stage, deepcopy(payload), timeout))
        if self.mutate:
            self.mutate(stage, payload)
        if stage == self.fail_stage:
            raise TimeoutError("synthetic timeout")
        if stage == "observe":
            assert payload == {} and image, "Image-only stage leaked metadata or omitted image"
            from icons.tests.taxonomy_fixtures import fresh_observations

            observations = fresh_observations(self.names, self.activities)
            value = {"depiction": self.depiction, "figures": len(self.names) or 1, "observations": observations}
        elif stage == "compare":
            assert image is None
            value = {
                "assertions": {
                    str(pk): dict(agrees=True, conflict=False, observation_ids=[], inference="metadata agrees")
                    for pk in sorted({c["concept"] for c in payload["claims"]})
                }
            }
        else:
            value = {"concepts": [payload["concepts"][0]["id"]], "unresolved": []}
        return value, "fixture-luna", {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}


@override_settings(
    STORAGES=STORAGES,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
    ICON_TAXONOMY_DISPATCH_ENABLED=False,
    ICON_TAXONOMY_BUDGET="offline",
    ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS=100,
)
class TaxonomyTests(TransactionTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.temp.name)
        self.media.enable()
        self.addCleanup(self.media.disable)
        self.church = Church.objects.create(name="Taxonomy church")
        self.other = Church.objects.create(name="Other church")
        self.budget = TaxonomyBudget.objects.create(
            name="offline", enabled=True, max_calls=100, max_tokens=100_000_000, max_microdollars=10_000_000_000
        )
        # Seed all test identities before analyzing: this is source metadata, not a calendar.
        seed_vocabulary()
        for text in (BAPTIST, EVANGELIST, PAIR):
            catalogue_claims({"title": text, "tags": [], "filename": ""})
        self.serial = 0

    def icon(self, title=BAPTIST, *, tags=(), church=None, name="upload.png"):
        self.serial += 1
        return ingest_icon(
            title=title, church=church or self.church, image=upload(name, (self.serial, 90, 120)), tags=list(tags)
        )

    def due(self, icon):
        IconTaxonomyWork.objects.filter(icon=icon).update(available_at=timezone.now() - timedelta(seconds=1))

    def analyze(self, icon, provider=None):
        self.due(icon)
        provider = provider or FixtureProvider()
        state = process_icon(icon.pk, provider=provider)
        self.assertEqual(state, "complete", list(IconTaxonomyWork.objects.filter(icon=icon).values("state", "error")))
        return provider

    def matches(self, text, *, icons=None, kind="feast"):
        return match_icons(
            list(icons if icons is not None else Icon.objects.filter(church=self.church)),
            IconMatchRequest(kind=kind, primary_text=text, auto_assign_policy="feast_strict", max_results=10),
            church_id=self.church.pk,
        )

    def test_tag_edit_invalidates_before_new_job_and_assignment(self):
        icon = self.icon()
        self.analyze(icon)
        match = self.matches(BAPTIST).matches[0]
        self.assertTrue(match["auto_assignable"])
        icon.tags.add("new tag")
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())
        self.assertFalse(self.matches(BAPTIST).matches)
        self.assertFalse(assignment_current(icon, match))

    def test_same_path_save_content_generation_and_background_reconciliation(self):
        icon = self.icon()
        self.analyze(icon)
        match = self.matches(BAPTIST).matches[0]
        path = Path(icon.image.path)
        path.write_bytes(upload(color=(250, 20, 10)).read())
        icon.save(update_fields=["image"])
        self.assertFalse(assignment_current(icon, match))
        self.assertFalse(self.matches(BAPTIST).matches)
        self.analyze(icon)
        prior = icon.image_revision
        path.write_bytes(upload(color=(251, 21, 11)).read())
        refresh_content(icon)
        self.assertGreater(icon.image_revision, prior)
        self.assertFalse(self.matches(BAPTIST).matches)

    def test_matching_never_opens_image_storage(self):
        icon = self.icon()
        self.analyze(icon)
        with patch.object(icon.image.storage, "open", side_effect=AssertionError("request storage read")):
            match = self.matches(BAPTIST).matches[0]
            self.assertTrue(match["auto_assignable"])
            self.assertTrue(assignment_current(icon, match))

    def test_church_move_and_deletion_during_stage(self):
        icon = self.icon()
        self.analyze(icon)
        icon.church = self.other
        icon.save(update_fields=["church"])
        self.assertFalse(self.matches(BAPTIST).matches)
        self.analyze(icon)
        self.assertEqual(IconObservation.objects.count(), 2)
        other_icon = self.icon()
        self.due(other_icon)
        p = FixtureProvider(mutate=lambda stage, payload: other_icon.delete() if stage == "observe" else None)
        self.assertIn(process_icon(other_icon.pk, provider=p), {"deleted", "superseded"})
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon_id=other_icon.pk).exists())

    def test_unrelated_addition_preserves_dependency_and_no_paid_reanalysis(self):
        icon = self.icon()
        p = self.analyze(icon)
        old = icon.taxonomic_analyses.get(state="complete")
        catalogue_claims({"title": "Saint Gregory of Narek", "tags": [], "filename": ""})
        self.assertTrue(dependencies_current(old.dependencies))
        self.assertEqual(reconcile_versions(), [])
        self.assertTrue(self.matches(BAPTIST).matches[0]["auto_assignable"])
        schedule(icon.pk, force=True)
        self.analyze(icon, p)
        self.assertEqual(len(p.calls), 2)

    def test_used_meaning_alias_collision_and_version_invalidate(self):
        icon = self.icon()
        self.analyze(icon)
        subject = resolve(BAPTIST)
        subject.definition = {"qualified": False}
        subject.save()
        self.assertFalse(self.matches(BAPTIST).matches)
        self.assertEqual(reconcile_versions(), [icon.pk])
        subject.definition = {"qualified": True}
        subject.save()
        self.analyze(icon)
        alias(resolve(EVANGELIST), BAPTIST, source={"fixture": "deliberate collision"})
        self.assertFalse(self.matches(BAPTIST).matches)
        with override_settings(ICON_TAXONOMY_MODEL="different-version"):
            self.assertFalse(self.matches(EVANGELIST).matches)

    def test_absent_event_still_has_fallback_without_persisted_event(self):
        icon = self.icon()
        self.analyze(icon)
        for action in ("Beheading", "Assumption", "Birth"):
            text = action + " of Saint " + BAPTIST
            parsed = interpret(IconMatchRequest(kind="feast", primary_text=text))
            self.assertTrue(parsed["event_intent"])
            self.assertIsNone(parsed["event"])
            self.assertFalse(parsed["unresolved"])
            result = self.matches(text).matches[0]
            self.assertEqual(result["relation"], "subject_portrait")
            self.assertTrue(result["auto_assignable"])

    def test_unclassified_catalogue_blocks_missing_event_auto_fallback(self):
        icon = self.icon()
        self.analyze(icon)
        self.icon(title="unclassified", church=self.church)
        result = self.matches("Beheading of " + BAPTIST)
        self.assertFalse(result.catalogue_complete)
        self.assertTrue(result.matches)
        self.assertFalse(result.matches[0]["auto_assignable"])

    def test_pair_member_tags_do_not_conflict_partial_is_suggestion(self):
        pair = self.icon(title=PAIR, tags=[BAPTIST, EVANGELIST])
        single = self.icon()
        self.analyze(pair, FixtureProvider(names=(BAPTIST, EVANGELIST)))
        self.analyze(single)
        results = self.matches(PAIR).matches
        self.assertEqual(results[0]["id"], pair.pk)
        self.assertTrue(results[0]["auto_assignable"])
        self.assertEqual(results[1]["id"], single.pk)
        self.assertFalse(results[1]["auto_assignable"])

    def test_exact_open_group_without_invented_members(self):
        group = concept(
            "group",
            "Martyrs of the Valley and their Companions",
            {"members": [], "complete": False},
            source={"fixture": "sourced exact group"},
        )
        icon = self.icon(title=group.label)
        self.analyze(icon, FixtureProvider(names=(group.label,)))
        result = self.matches(group.label).matches[0]
        self.assertTrue(result["auto_assignable"])
        self.assertEqual(group.definition["members"], [])
        self.assertEqual(result["coverage"]["required"], 1)

    def test_unknown_specific_identity_cannot_become_theme(self):
        icon = self.icon(title="generic")
        self.analyze(icon, FixtureProvider(names=(), depiction="scene", activities=["giving_alms"]))
        for text in (
            "Saint Charity of the Coast",
            "A prayer for Saint Charity of the Coast",
            "A reflection about Charity of the Coast",
        ):
            self.assertFalse(self.matches(text, kind="content").matches, text)
        self.assertTrue(self.matches("charity", kind="content").matches)

    def test_thematic_prose_not_identity_and_broader_theme_coverage(self):
        icon = self.icon(title="generic")
        self.analyze(
            icon,
            FixtureProvider(
                names=(),
                depiction="scene",
                activities=["shared_supper", "bread_and_cup", "sharing_food", "comforting", "washing_feet"],
            ),
        )
        for text in (
            "A prayer of gratitude and sharing a meal with others",
            "A reflection on the meaning of compassion",
        ):
            parsed = interpret(IconMatchRequest(kind="content", primary_text=text))
            self.assertFalse(parsed["identity_constraint"], text)
            self.assertTrue(self.matches(text, kind="content").matches, text)
        for theme in ("mercy", "service", "gratitude", "humility"):
            self.assertTrue(self.matches(theme, kind="content").matches)

    def test_image_only_known_identity_and_scene_mapping(self):
        icon = self.icon(title="generic")
        self.analyze(icon)
        match = self.matches(BAPTIST).matches[0]
        self.assertFalse(match["auto_assignable"])
        self.assertEqual(match["evidence_level"], "observed")
        scene = self.icon(title="another generic")
        self.analyze(scene, FixtureProvider(names=(), depiction="scene", activities=["shared_supper", "bread_and_cup"]))
        result = self.matches("Last Supper").matches
        self.assertEqual(result[0]["id"], scene.pk)
        self.assertFalse(result[0]["auto_assignable"])
        self.assertTrue(self.matches("gratitude", kind="content").matches)

    def test_filename_echo_not_visual_proof_and_conflict_preserves_theme(self):
        icon = self.icon(name="John-the-Baptist.png")
        self.analyze(icon, FixtureProvider(names=(EVANGELIST,), depiction="portrait", activities=["praying"]))
        self.assertFalse(self.matches(BAPTIST).matches)
        self.assertTrue(self.matches("prayer", kind="content").matches)
        icon2 = self.icon()
        self.analyze(icon2, FixtureProvider(names=()))
        self.assertFalse(self.matches(BAPTIST, icons=[icon2]).matches[0]["auto_assignable"])

    def test_exact_event_precedence_and_different_scene_blocks_portrait(self):
        title = "Baptism of " + BAPTIST
        event = self.icon(title=title)
        portrait = self.icon()
        self.analyze(event, FixtureProvider(names=(title, BAPTIST), depiction="scene"))
        self.analyze(portrait)
        results = self.matches(title).matches
        self.assertEqual(results[0]["id"], event.pk)
        self.assertTrue(results[0]["auto_assignable"])
        self.assertFalse(results[1]["auto_assignable"])
        self.assertFalse(self.matches("Beheading of " + BAPTIST, icons=[event]).matches[0]["auto_assignable"])

    def test_multilingual_exact_alias_and_unresolved_unknown_alias(self):
        alias(resolve(BAPTIST), "Հովհաննես Մկրտիչ", "hy", {"fixture": "exact bilingual source"})
        icon = self.icon(title="Հովհաննես Մկրտիչ")
        self.analyze(icon, FixtureProvider(names=("Հովհաննես Մկրտիչ",)))
        self.assertTrue(self.matches(BAPTIST).matches[0]["auto_assignable"])
        self.assertFalse(self.matches("Hovannes Mkrtich").matches)

    def test_scope_cannot_expand_allowed_catalogue(self):
        icon = self.icon(church=self.other)
        self.analyze(icon)
        self.assertFalse(self.matches(BAPTIST, icons=[icon]).matches)
        self.assertFalse(match_icons([icon], IconMatchRequest(kind="feast", primary_text=BAPTIST)).matches)
        self.assertTrue(match_icons([icon], IconMatchRequest(kind="content", primary_text=BAPTIST)).matches)

    def test_eval_reports_all_requests_without_calendar_ingestion_or_api(self):
        icon = self.icon(title="generic")
        self.analyze(icon, FixtureProvider(names=(), depiction="scene", activities=["shared_supper", "bread_and_cup"]))
        out = StringIO()
        with patch.object(VisionProvider, "call", side_effect=AssertionError("live call")):
            call_command(
                "evaluate_icon_taxonomy", church=self.church.pk, request=["gratitude", "Unknown of Nowhere"], stdout=out
            )
        report = json.loads(out.getvalue())
        self.assertEqual(len(report["requests"]), 2)
        self.assertTrue(report["requests"][0]["candidate_count"])
        self.assertFalse(report["requests"][1]["resolved"])
        self.assertFalse(report["calendar_artifact_only"])

    def test_request_adapter_deadline_ids_cache_and_identity_guard(self):
        from icons.services.request_adapter import adapt_request

        p = FixtureProvider()
        request = IconMatchRequest(kind="content", primary_text="a reflective passage")
        value = adapt_request(request, church_id=self.church.pk, provider=p, deadline=time.monotonic() + 0.5)
        self.assertTrue(value["concepts"])
        self.assertLessEqual(p.calls[0][2], 0.5)
        self.assertEqual(adapt_request(request, church_id=self.church.pk, provider=p), value)
        self.assertEqual(len(p.calls), 1)
        adapt_request(
            IconMatchRequest(kind="content", primary_text="different request"),
            provider=p,
            deadline=time.monotonic() - 1,
        )
        self.assertEqual(len(p.calls), 1)
        interpret(IconMatchRequest(kind="content", primary_text="Saint Charity of the Coast"), adapter=p)
        self.assertEqual(len(p.calls), 1)

    def test_router_baseline_suggestions_and_shadow_deadline(self):
        from hub.services.icon_match_router import match_icons as route

        request = IconMatchRequest(kind="content", primary_text=BAPTIST)
        with patch(
            "hub.services.icon_match_router.baseline_match_icons", return_value=IconMatchOutcome(status="complete")
        ) as baseline:
            route([], request)
            baseline.assert_called_once()
        with (
            override_settings(ICON_MATCH_ROUTER_MODE="shadow"),
            patch(
                "hub.services.icon_match_router.baseline_match_icons", return_value=IconMatchOutcome(status="complete")
            ),
            patch("hub.services.icon_taxonomy_matching.match_icons") as taxonomy,
        ):
            result = route([], request, limits=MatchLimits(total_seconds=20))
            taxonomy.assert_not_called()
            self.assertEqual(result.status, "complete")
        icon = self.icon()
        self.analyze(icon)
        with override_settings(ICON_MATCH_ROUTER_MODE="taxonomy_suggestions"):
            result = route(
                [icon],
                IconMatchRequest(kind="feast", primary_text=BAPTIST, auto_assign_policy="feast_strict"),
                church_id=self.church.pk,
            )
            self.assertFalse(result.matches[0]["auto_assignable"])

    def test_calendar_eval_uses_only_engine_pairs_and_does_not_write_registry(self):
        icon = self.icon()
        self.analyze(icon)
        before = TaxonomyConcept.objects.count()

        def engine(day, language="en"):
            return {"Liturgical Day": BAPTIST if language == "en" else ("Հովհաննես" if day.day == 1 else "Այլ անուն")}

        output = StringIO()
        with (
            patch("armenian_lectionary.MIN_YEAR", 2025),
            patch("armenian_lectionary.MAX_YEAR", 2025),
            patch("armenian_lectionary.compute_armenian_lectionary", side_effect=engine, create=True),
            patch.object(VisionProvider, "call", side_effect=AssertionError("live call")),
        ):
            call_command("evaluate_icon_taxonomy", calendar=True, church=self.church.pk, stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(len(report["requests"]), 2)
        self.assertTrue(report["calendar_artifact_only"])
        self.assertEqual(TaxonomyConcept.objects.count(), before)

    def test_engine_bilingual_pair_is_request_local(self):
        request = IconMatchRequest(kind="feast", primary_text="Unknown Armenian alias")
        before = TaxonomyConcept.objects.count()
        parsed = interpret(request, commemoration={"name_en": BAPTIST, "name_hy": request.primary_text})
        self.assertFalse(parsed["unresolved"])
        self.assertEqual(parsed["subjects"], [resolve(BAPTIST).pk])
        self.assertEqual(TaxonomyConcept.objects.count(), before)
        self.assertTrue(interpret(request)["unresolved"])

    def test_adapter_rejects_unknown_ids_and_preserves_lexical_suggestions(self):
        from icons.services.request_adapter import adapt_request

        class UnknownID(FixtureProvider):
            def call(self, *args, **kwargs):
                return {"concepts": [99999999], "unresolved": []}, "fixture", {}

        request = IconMatchRequest(kind="content", primary_text="A reflection on compassion")
        result = adapt_request(request, provider=UnknownID())
        self.assertEqual(result["concepts"], [])
        parsed = interpret(request, adapter=UnknownID())
        self.assertIn(resolve("mercy").pk, parsed["themes"])
        self.assertTrue(parsed["unresolved"])

    def test_unresolved_scene_tag_retains_original_sources_and_blocks_portrait(self):
        icon = self.icon(tags=["holy-portrait", "ascension"], name="Սուրբ_John-the-Baptist.png")
        provider = self.analyze(icon)
        comparison = provider.calls[1][1]
        self.assertEqual(comparison["metadata"]["title"], BAPTIST)
        self.assertEqual(comparison["metadata"]["filename"], icon.original_filename)
        self.assertEqual(comparison["metadata"]["tags"], ["ascension", "holy-portrait"])
        source = next(s for s in comparison["sources"] if s["text"] == "ascension")
        self.assertTrue(source["parsed"]["unresolved"])
        self.assertTrue(source["scene_hint"])
        analysis = icon.taxonomic_analyses.get(state="complete")
        portrait = analysis.assertions.get(attribute="portrait")
        self.assertEqual(portrait.status, "unknown")
        self.assertIn(source, portrait.evidence)
        self.assertEqual(analysis.claims["sources"], comparison["sources"])
        result = self.matches("Beheading of " + BAPTIST).matches
        self.assertTrue(result, "Supported identity suggestions should survive the scene ambiguity")
        self.assertFalse(any(m["auto_assignable"] for m in result))
        self.assertFalse(any(m["generic_portrait"] for m in result))

    def test_unfamiliar_scene_descriptors_block_but_search_tags_do_not(self):
        for tag in ("episode: a traveler at an uncharted gate", "a figure wading through luminous reeds"):
            icon = self.icon(tags=["holy-portrait", tag])
            provider = self.analyze(icon)
            source = next(s for s in provider.calls[1][1]["sources"] if s["text"] == tag)
            self.assertTrue(source["parsed"]["unresolved"])
            self.assertTrue(source["scene_hint"])
            matches = self.matches("Beheading of " + BAPTIST, icons=[icon]).matches
            self.assertTrue(matches)
            self.assertFalse(any(m["auto_assignable"] for m in matches), tag)
        harmless = self.icon(tags=["holy-portrait", "collection-17", "blue background"])
        self.analyze(harmless)
        self.assertTrue(self.matches("Beheading of " + BAPTIST, icons=[harmless]).matches[0]["auto_assignable"])

    def test_unresolved_filename_stem_and_component_aliases_invalidate_locally(self):
        other = concept("subject", "Nerses of Lambron", {"qualified": True}, source={"fixture": "qualified source"})
        for index, name in enumerate(("mystery_label.png", "John_the_Baptist-and-hidden_label.png")):
            icon = self.icon(name=name)
            self.analyze(icon)
            analysis = icon.taxonomic_analyses.get(state="complete")
            term = "mystery label" if index == 0 else "hidden label"
            self.assertIn(term, analysis.dependencies["terms"])
            alias(other, "unrelated label " + str(index), source={"fixture": "unrelated addition"})
            self.assertTrue(dependencies_current(analysis.dependencies))
            alias(other, term, source={"fixture": "new exact alias"})
            self.assertFalse(dependencies_current(analysis.dependencies))
            self.assertFalse(self.matches(BAPTIST, icons=[icon]).matches)
            self.assertIn(icon.pk, reconcile_versions())

    def test_scene_only_event_ranks_below_complete_participant_portrait(self):
        scene = self.icon(title="generic")
        self.analyze(scene, FixtureProvider(names=(), depiction="scene", activities=["shared_supper", "bread_and_cup"]))
        portrait = self.icon(title="Jesus Christ and Twelve Apostles")
        self.analyze(portrait, FixtureProvider(names=("Jesus Christ", "Twelve Apostles")))
        results = self.matches("Last Supper", icons=[scene, portrait]).matches
        self.assertEqual([m["id"] for m in results], [portrait.pk, scene.pk])
        self.assertEqual(results[0]["relation"], "subject_portrait")
        self.assertTrue(results[0]["auto_assignable"])
        self.assertEqual(results[1]["relation"], "related_specific")
        self.assertEqual(results[1]["coverage"], {"required": 2, "covered": 0})
        self.assertFalse(results[1]["auto_assignable"])

    def test_supported_event_without_required_participants_remains_exact_suggestion(self):
        scene = self.icon(title="generic")
        self.analyze(
            scene, FixtureProvider(names=(), depiction="scene", activities=["returning_son", "father_embracing_son"])
        )
        result = self.matches("Return of the Prodigal Son", icons=[scene]).matches[0]
        self.assertEqual(result["relation"], "exact_event")
        self.assertEqual(result["coverage"]["required"], 0)
        self.assertFalse(result["auto_assignable"])
