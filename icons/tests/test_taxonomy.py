"""Offline invariants for ingestion, evidence, matching, budgets and commands."""

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection, transaction, close_old_connections, OperationalError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from hub.models import Church
from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_match_service import IconMatchOutcome, MatchLimits
from hub.services.icon_taxonomy_matching import match_icons, interpret, assignment_current
from icons.admin import IconAdmin
from icons.models import (
    Icon,
    IconAnalysis,
    IconObservation,
    IconTaxonomyProjection,
    IconTaxonomyWork,
    TaxonomyBudget,
    TaxonomyCall,
    TaxonomyConcept,
)
from icons.serializers import IconSerializer
from icons.services.ingestion import ingest_icon, schedule, refresh_content, reconcile_versions
from icons.services.taxonomy_budget import BudgetExhausted, reserve
from icons.services.taxonomy_inputs import filename, recover_filename, dependencies_current
from icons.services.taxonomy_pipeline import claim, process_icon
from icons.services.taxonomy_vocabulary import alias, catalogue_claims, concept, parse, resolve, seed_vocabulary
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
            observations = [
                dict(id=f"n{i}", kind="inscription", text=name, region="upper inscription", readable=True)
                for i, name in enumerate(self.names)
            ]
            observations += [
                dict(id=f"a{i}", kind="activity", text=value, region="center action", readable=False)
                for i, value in enumerate(self.activities)
            ]
            value = {"depiction": self.depiction, "figures": len(self.names) or 1, "observations": observations}
        elif stage == "compare":
            assert image is None
            value = {
                "assertions": [
                    dict(concept=pk, agrees=True, conflict=False, observation_ids=[], inference="metadata agrees")
                    for pk in sorted({c["concept"] for c in payload["claims"]})
                ]
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

    def test_original_filename_unicode_all_write_boundaries(self):
        icon = self.icon(name="Սուրբ Գրիգոր.png")
        self.assertEqual(icon.original_filename, "Սուրբ Գրիգոր.png")
        self.assertEqual(icon.filename_provenance, "uploaded")
        self.assertEqual(filename(r"C:\folder\Սուրբ\Սուրբ Գրիգոր\u0000.png"), "u0000.png")
        self.assertEqual(filename("../Սուրբ\x00.png"), "Սուրբ.png")
        replacement = upload("Ավետարանիչ.png", (90, 20, 30))
        icon.image = replacement
        icon.save(False, False, None, ["image"])
        icon.refresh_from_db()
        self.assertEqual(icon.original_filename, "Ավետարանիչ.png")
        icon.image = upload("not-saved.png")
        icon.title = "Updated title"
        icon.save(False, False, None, ["title"])
        icon.refresh_from_db()
        self.assertEqual(icon.original_filename, "Ավետարանիչ.png")
        self.assertNotIn("not-saved", icon.image.name)

    def test_serializer_final_tags_update_clear_and_admin(self):
        serializer = IconSerializer(
            data={"title": BAPTIST, "church": self.church.pk, "image": upload(), "tags": "portrait, prayer"}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        icon = serializer.save()
        from icons.services.taxonomy_inputs import fingerprint

        self.assertEqual(icon.taxonomy_work.fingerprint, fingerprint(icon))
        self.assertEqual(icon.original_filename, "upload.png")
        serializer = IconSerializer(icon, data={"tags": ""}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        icon = serializer.save()
        self.assertFalse(icon.tags.exists())
        admin = IconAdmin(Icon, AdminSite())
        with patch("django.contrib.admin.ModelAdmin.save_related") as saved:
            form = type("Form", (), {"instance": icon})()
            admin.save_related(None, form, [], True)
            saved.assert_called_once()
        self.assertEqual(IconTaxonomyWork.objects.get(icon=icon).fingerprint, fingerprint(icon))

    def test_transaction_rollback_discards_outbox_and_wake(self):
        with patch("icons.services.ingestion.wake_dispatcher") as wake:
            with self.assertRaises(RuntimeError), transaction.atomic():
                self.icon(tags=["portrait"])
                raise RuntimeError("rollback")
            self.assertFalse(IconTaxonomyWork.objects.exists())
            wake.assert_not_called()

    def test_broker_failure_and_delayed_wake_are_durable(self):
        with (
            override_settings(ICON_TAXONOMY_DISPATCH_ENABLED=True),
            patch("icons.tasks.dispatch_icon_taxonomy.apply_async", side_effect=ConnectionError) as wake,
        ):
            icon = self.icon()
        self.assertEqual(icon.taxonomy_work.state, "pending")
        self.assertTrue(wake.called)
        self.assertEqual(wake.call_args.kwargs["countdown"], 3)
        self.analyze(icon)

    def test_disabled_dispatch_never_provisions_or_calls(self):
        icon = self.icon()
        with patch.object(VisionProvider, "call", side_effect=AssertionError("live call")):
            self.assertEqual(process_icon(icon.pk), "disabled")
            output = StringIO()
            call_command("dispatch_icon_taxonomy", stdout=output)
        self.assertIn("disabled", output.getvalue())
        self.assertEqual(TaxonomyCall.objects.count(), 0)

    def test_complete_pipeline_idempotency_and_observation_reuse(self):
        icon = self.icon()
        p = self.analyze(icon)
        self.assertEqual([c[0] for c in p.calls], ["observe", "compare"])
        self.assertEqual(process_icon(icon.pk, provider=p), "not_due")
        schedule(icon.pk, force=True)
        self.analyze(icon, p)
        self.assertEqual(len(p.calls), 2)
        icon.title = "Saint " + BAPTIST
        icon.save(update_fields=["title"])
        self.analyze(icon, p)
        self.assertEqual([c[0] for c in p.calls], ["observe", "compare", "compare"])
        self.assertEqual(IconObservation.objects.count(), 1)
        self.assertEqual(IconAnalysis.objects.filter(state="complete").count(), 2)

    def test_tag_edit_invalidates_before_new_job_and_assignment(self):
        icon = self.icon()
        self.analyze(icon)
        match = self.matches(BAPTIST).matches[0]
        self.assertTrue(match["auto_assignable"])
        icon.tags.add("new tag")
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())
        self.assertFalse(self.matches(BAPTIST).matches)
        self.assertFalse(assignment_current(icon, match))

    def test_tag_rename_and_reverse_operations_invalidate(self):
        icon = self.icon(tags=["portrait"])
        self.analyze(icon)
        tag = icon.tags.get()
        tag.name = "scene"
        tag.save()
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())
        self.analyze(icon)
        from icons.signals import taxonomy_tags_changed

        taxonomy_tags_changed(Icon.tags.through, tag, "pre_clear", True, None)
        icon.tags.through.objects.filter(tag=tag).delete()
        taxonomy_tags_changed(Icon.tags.through, tag, "post_clear", True, None)
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())

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

    def test_stale_metadata_during_observe_stops_context_call(self):
        icon = self.icon()

        def change(stage, payload):
            if stage == "observe":
                icon.tags.add("changed during call")

        p = FixtureProvider(mutate=change)
        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=p), "superseded")
        self.assertEqual(len(p.calls), 1)
        self.assertFalse(IconTaxonomyProjection.objects.exists())
        self.analyze(icon, FixtureProvider())
        self.assertEqual(IconObservation.objects.count(), 1)

    def test_image_replacement_during_compare_cannot_publish(self):
        icon = self.icon()

        def change(stage, payload):
            if stage == "compare":
                icon.image = upload("new.png", (1, 2, 3))
                icon.save(update_fields=["image"])

        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=FixtureProvider(mutate=change)), "superseded")
        self.assertFalse(IconTaxonomyProjection.objects.exists())

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

    def test_arbitrary_phrases_and_bare_saints_are_not_people(self):
        for text in ("red robe", "Holy Ascension", "Last Supper", "Saint Peter", "A prayer of gratitude"):
            result = parse(text, create=True)
            self.assertFalse(
                any(TaxonomyConcept.objects.get(pk=pk).label == text.casefold() for pk in result["subjects"]), text
            )
        self.assertIsNone(resolve("red robe"))
        ordinary = parse("Saint Nerses of Lambron", create=True)
        self.assertEqual(len(ordinary["subjects"]), 1)
        self.assertFalse(ordinary["unresolved"])

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
                names=(), depiction="scene", activities=["giving_thanks", "sharing_food", "comforting", "washing_feet"]
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

    def test_retry_reserves_every_timeout_and_reuses_observe(self):
        icon = self.icon()
        p = FixtureProvider(fail_stage="compare")
        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=p), "retry")
        self.assertEqual(TaxonomyCall.objects.filter(state="reserved_unknown").count(), 1)
        p.fail_stage = None
        self.analyze(icon, p)
        self.assertEqual([c[0] for c in p.calls], ["observe", "compare", "compare"])
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.calls, 3)
        self.assertEqual(self.budget.tokens, sum(TaxonomyCall.objects.values_list("reserved_tokens", flat=True)))

    def test_retry_exhaustion_and_schema_error_are_terminal(self):
        icon = self.icon()
        p = FixtureProvider(fail_stage="observe")
        for state in ("retry", "retry", "unavailable"):
            self.due(icon)
            self.assertEqual(process_icon(icon.pk, provider=p), state)
        self.assertEqual(len(p.calls), 3)
        malformed = self.icon()

        class Bad(FixtureProvider):
            def call(self, *args, **kwargs):
                return {"confidence": 1}, "fixture", {}

        self.due(malformed)
        self.assertEqual(process_icon(malformed.pk, provider=Bad()), "unavailable")
        self.assertFalse(IconTaxonomyProjection.objects.exists())

    def test_budget_stops_before_context_and_resume_reuses_observation(self):
        self.budget.max_calls = 1
        self.budget.save()
        icon = self.icon()
        p = FixtureProvider()
        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=p), "budget_blocked")
        self.assertEqual(len(p.calls), 1)
        self.budget.max_calls = 2
        self.budget.save()
        IconTaxonomyWork.objects.filter(icon=icon).update(state="pending", attempts=0)
        self.analyze(icon, p)
        self.assertEqual(len(p.calls), 2)

    def test_token_and_spend_caps_are_independent_and_no_implicit_budget(self):
        self.budget.max_tokens = 1
        self.budget.save()
        with self.assertRaises(BudgetExhausted):
            reserve("observe", {}, image=True)
        self.budget.max_tokens, self.budget.max_microdollars = 100_000_000, 1
        self.budget.save()
        with self.assertRaises(BudgetExhausted):
            reserve("compare", {})
        with self.assertRaises(BudgetExhausted):
            reserve("compare", {}, budget_name="unconfigured")
        self.assertFalse(TaxonomyBudget.objects.filter(name="unconfigured").exists())

    def test_concurrent_reservations_never_exceed_cap(self):
        self.budget.max_calls = 1
        self.budget.save()

        def attempt(_):
            close_old_connections()
            try:
                for _ in range(20):
                    try:
                        reserve("compare", {})
                        return "reserved"
                    except BudgetExhausted:
                        return "blocked"
                    except OperationalError:
                        time.sleep(0.01)  # SQLite lock contention, no provider dispatch.
                return "db_locked"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(results.count("reserved"), 1)
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.calls, 1)

    def test_leases_block_duplicate_worker_and_expiry_recovers(self):
        icon = self.icon()
        self.due(icon)
        claimed = claim(icon.pk)
        self.assertIsNotNone(claimed)
        self.assertIsNone(claim(icon.pk))
        IconTaxonomyWork.objects.filter(icon=icon).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.analyze(icon)

    def test_backfill_dry_run_checkpoint_and_recovered_filename(self):
        icon = self.icon()
        Icon.objects.filter(pk=icon.pk).update(original_filename="", filename_provenance="unknown")
        self.assertEqual(recover_filename("20260905_120102_1234abcd_saint-name.png"), "saint-name")
        self.assertEqual(recover_filename("arbitrary_name.png"), "")
        before = IconTaxonomyWork.objects.get(icon=icon).revision
        out = StringIO()
        call_command("backfill_icon_taxonomy", dry_run=True, church=self.church.pk, limit=1, stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report["next_after_id"], icon.pk)
        self.assertEqual(IconTaxonomyWork.objects.get(icon=icon).revision, before)
        self.assertFalse(TaxonomyCall.objects.exists())

    def test_eval_reports_all_requests_without_calendar_ingestion_or_api(self):
        icon = self.icon(title="generic")
        self.analyze(icon, FixtureProvider(names=(), depiction="scene", activities=["giving_thanks"]))
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

    def test_real_adapter_contract_is_mocked_no_sdk_retries_or_public_url(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        response = SimpleNamespace(
            status="completed",
            output_text=json.dumps({"depiction": "unknown", "figures": 0, "observations": []}),
            model="fixture",
            usage=SimpleNamespace(model_dump=lambda **kwargs: {"total_tokens": 1}),
        )
        client = AsyncMock()
        client.responses.create.return_value = response
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        from icons.services.vision_provider import OBSERVATION_SCHEMA

        with (
            override_settings(ICON_TAXONOMY_DISPATCH_ENABLED=True, OPENAI_API_KEY="offline-fixture-key"),
            patch("openai.AsyncOpenAI", return_value=manager) as sdk,
        ):
            VisionProvider().call("observe", {}, OBSERVATION_SCHEMA, image=b"fixture", timeout=0.5)
        self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)
        args = client.responses.create.call_args.kwargs
        self.assertTrue(args["input"][0]["content"][0]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(args["text"]["format"]["strict"])

    def test_concurrent_worker_and_shared_observation_are_idempotent(self):
        from threading import Event

        first = self.icon()
        second = ingest_icon(title=BAPTIST, church=self.church, image=upload(color=(1, 90, 120)))
        self.due(first)
        self.due(second)
        entered, release_call = Event(), Event()

        def block(stage, payload):
            if stage == "observe":
                entered.set()
                if not release_call.wait(5):
                    raise TimeoutError("fixture did not release")

        provider = FixtureProvider(mutate=block)

        def run():
            close_old_connections()
            try:
                return process_icon(first.pk, provider=provider)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(process_icon(first.pk, provider=FixtureProvider()), "not_due")
                observer = FixtureProvider()
                self.assertEqual(process_icon(second.pk, provider=observer), "retry")
                self.assertEqual(observer.calls, [])
            finally:
                release_call.set()
            self.assertEqual(future.result(timeout=5), "complete")
        self.analyze(second, observer)
        self.assertEqual([c[0] for c in observer.calls], ["compare"])
        self.assertEqual(IconObservation.objects.filter(state="complete").count(), 1)
        self.assertEqual(TaxonomyCall.objects.count(), 3)

    def test_comparison_cannot_invent_ids_or_echo_filename_as_proof(self):
        icon = self.icon()

        class InventingProvider(FixtureProvider):
            def call(self, stage, *args, **kwargs):
                value, model, usage = super().call(stage, *args, **kwargs)
                if stage == "compare":
                    value["assertions"][0]["observation_ids"] = ["filename-is-proof"]
                return value, model, usage

        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=InventingProvider()), "unavailable")
        self.assertFalse(IconTaxonomyProjection.objects.exists())

    def test_dispatcher_and_backfill_commands_execute_complete_mocked_pipeline(self):
        icon = self.icon()
        self.due(icon)
        output = StringIO()
        provider = FixtureProvider()
        with (
            override_settings(ICON_TAXONOMY_DISPATCH_ENABLED=True),
            patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider),
        ):
            call_command("dispatch_icon_taxonomy", inline=True, budget="offline", limit=1, stdout=output)
        self.assertEqual(len(provider.calls), 2)
        self.assertTrue(IconTaxonomyProjection.objects.filter(icon=icon).exists())
        icon.tags.add("changed")
        output = StringIO()
        with (
            override_settings(ICON_TAXONOMY_DISPATCH_ENABLED=True),
            patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider),
        ):
            call_command(
                "backfill_icon_taxonomy", dispatch=True, budget="offline", church=self.church.pk, stdout=output
            )
        self.assertEqual(json.loads(output.getvalue())["states"], ["complete"])
        self.assertEqual([c[0] for c in provider.calls], ["observe", "compare", "compare"])

    def test_periodic_dispatch_recovers_and_freezes_at_cap(self):
        from icons.tasks import dispatch_icon_taxonomy

        icon = self.icon()
        self.due(icon)
        with (
            override_settings(ICON_TAXONOMY_DISPATCH_ENABLED=True),
            patch("icons.tasks.analyze_icon_taxonomy.delay") as task,
        ):
            self.assertEqual(dispatch_icon_taxonomy()["dispatched"], 1)
            task.assert_called_once_with(icon.pk)
            self.budget.calls = self.budget.max_calls
            self.budget.save()
            self.assertEqual(dispatch_icon_taxonomy()["status"], "budget_blocked")
            self.assertEqual(task.call_count, 1)

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

    def test_deleting_tagged_icon_does_not_recreate_durable_work(self):
        for mode in ("instance", "queryset", "church"):
            icon = self.icon(tags=["portrait"])
            self.analyze(icon)
            pk = icon.pk
            if mode == "instance":
                icon.delete()
            elif mode == "queryset":
                Icon.objects.filter(pk=pk).delete()
            else:
                self.church.delete()
            self.assertFalse(IconTaxonomyWork.objects.filter(icon_id=pk).exists())
            self.assertFalse(IconTaxonomyProjection.objects.filter(icon_id=pk).exists())
            self.assertFalse(IconAnalysis.objects.filter(icon_id=pk).exists())
