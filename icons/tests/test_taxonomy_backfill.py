"""Offline invariants for ingestion, evidence, budgets and commands."""

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
            raise AssertionError("unexpected catalogue provider stage")
        return value, "fixture-luna", {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}


@override_settings(
    STORAGES=STORAGES,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
    ICON_TAXONOMY_DISPATCH_ENABLED=False,
    ICON_TAXONOMY_BUDGET="offline",
    ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS=100,
)
class TaxonomyBackfillTests(TransactionTestCase):
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

    def test_tag_edit_invalidates_before_new_job(self):
        icon = self.icon()
        self.analyze(icon)
        revision = IconTaxonomyWork.objects.get(icon=icon).revision
        icon.tags.add("new tag")
        self.assertGreater(IconTaxonomyWork.objects.get(icon=icon).revision, revision)
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())

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
        path = Path(icon.image.path)
        path.write_bytes(upload(color=(250, 20, 10)).read())
        icon.save(update_fields=["image"])
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())
        self.analyze(icon)
        prior = icon.image_revision
        path.write_bytes(upload(color=(251, 21, 11)).read())
        refresh_content(icon)
        self.assertGreater(icon.image_revision, prior)
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())

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
        self.assertFalse(IconTaxonomyProjection.objects.filter(icon=icon).exists())
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
        self.assertTrue(IconTaxonomyProjection.objects.filter(icon=icon, analysis=old).exists())
        schedule(icon.pk, force=True)
        self.analyze(icon, p)
        self.assertEqual(len(p.calls), 2)

    def test_used_meaning_alias_collision_and_version_invalidate(self):
        icon = self.icon()
        self.analyze(icon)
        subject = resolve(BAPTIST)
        subject.definition = {"qualified": False}
        subject.save()
        self.assertFalse(dependencies_current(icon.taxonomic_analyses.get(state="complete").dependencies))
        self.assertEqual(reconcile_versions(), [icon.pk])
        subject.definition = {"qualified": True}
        subject.save()
        self.analyze(icon)
        alias(resolve(EVANGELIST), BAPTIST, source={"fixture": "deliberate collision"})
        self.assertIn(icon.pk, reconcile_versions())
        with override_settings(ICON_TAXONOMY_MODEL="different-version"):
            from icons.services.taxonomy_inputs import versions

            self.assertNotEqual(icon.taxonomic_analyses.latest("created_at").versions, versions())

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

    def test_vision_sdk_contract_is_mocked_no_retries_or_public_url(self):
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
                    next(iter(value["assertions"].values()))["observation_ids"] = ["filename-is-proof"]
                return value, model, usage

        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=InventingProvider()), "complete")
        analysis = icon.taxonomic_analyses.get(state="complete")
        self.assertEqual(analysis.comparison["diagnostics"][0]["code"], "unknown_comparison_observation")
        self.assertTrue(IconTaxonomyProjection.objects.exists())

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

    def test_unfamiliar_scene_descriptors_block_but_search_tags_do_not(self):
        for tag in ("episode: a traveler at an uncharted gate", "a figure wading through luminous reeds"):
            icon = self.icon(tags=["holy-portrait", tag])
            provider = self.analyze(icon)
            source = next(s for s in provider.calls[1][1]["sources"] if s["text"] == tag)
            self.assertTrue(source["parsed"]["unresolved"])
            self.assertTrue(source["scene_hint"])
            self.assertEqual(
                icon.taxonomic_analyses.get(state="complete").assertions.get(attribute="portrait").status, "unknown"
            )
        harmless = self.icon(tags=["holy-portrait", "collection-17", "blue background"])
        self.analyze(harmless)
        self.assertEqual(
            harmless.taxonomic_analyses.get(state="complete").assertions.get(attribute="portrait").status, "supported"
        )

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
            self.assertIn(icon.pk, reconcile_versions())

    def test_image_only_evidence_and_identity_conflict_preserve_themes(self):
        icon = self.icon(title="generic")
        self.analyze(icon)
        assertion = icon.taxonomic_analyses.get(state="complete").assertions.get(concept=resolve(BAPTIST))
        self.assertEqual((assertion.status, assertion.evidence_level), ("supported", "observed"))
        scene = self.icon(title="generic scene")
        self.analyze(scene, FixtureProvider(names=(), depiction="scene", activities=["shared_supper", "bread_and_cup"]))
        assertions = scene.taxonomic_analyses.get(state="complete").assertions
        self.assertTrue(assertions.filter(attribute="event", status="supported", evidence_level="observed").exists())
        self.assertTrue(assertions.filter(concept=resolve("gratitude"), status="supported").exists())
        conflict = self.icon(name="John-the-Baptist.png")
        self.analyze(conflict, FixtureProvider(names=(EVANGELIST,), activities=["praying"]))
        assertions = conflict.taxonomic_analyses.get(state="complete").assertions
        self.assertEqual(assertions.get(concept=resolve(BAPTIST)).status, "contradicted")
        self.assertEqual(assertions.get(concept=resolve("prayer")).status, "supported")
        metadata_only = self.icon()
        self.analyze(metadata_only, FixtureProvider(names=()))
        assertion = metadata_only.taxonomic_analyses.get(state="complete").assertions.get(concept=resolve(BAPTIST))
        self.assertEqual(assertion.evidence_level, "metadata")

    def test_help_loads_without_provider_configuration(self):
        from contextlib import redirect_stdout
        from django.core.management import load_command_class

        with (
            override_settings(OPENAI_API_KEY="", ICON_TAXONOMY_BUDGET=""),
            patch.object(VisionProvider, "call") as provider,
        ):
            for name in ("backfill_icon_taxonomy", "dispatch_icon_taxonomy"):
                out = StringIO()
                with redirect_stdout(out), self.assertRaises(SystemExit) as stopped:
                    load_command_class("icons", name).run_from_argv(["manage.py", name, "--help"])
                self.assertEqual(stopped.exception.code, 0)
                self.assertIn("usage:", out.getvalue())
            provider.assert_not_called()

    def test_dry_run_dispatch_is_read_only_and_checkpoint_is_exclusive(self):
        from django.apps import apps
        from django.test.utils import CaptureQueriesContext

        first = self.icon()
        second = self.icon(church=self.other)
        models = list(apps.get_app_config("icons").get_models())
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        with (
            patch.object(VisionProvider, "call") as provider,
            patch("icons.services.ingestion.wake_dispatcher") as wake,
        ):
            with CaptureQueriesContext(connection) as queries:
                out = StringIO()
                call_command(
                    "backfill_icon_taxonomy",
                    dry_run=True,
                    dispatch=True,
                    budget="never-created",
                    max_calls=10,
                    max_tokens=1000000,
                    max_spend=10,
                    limit=1,
                    stdout=out,
                )
            self.assertFalse(
                any(q["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"} for q in queries)
            )
            self.assertEqual(json.loads(out.getvalue())["next_after_id"], first.pk)
            out = StringIO()
            call_command("backfill_icon_taxonomy", dry_run=True, after_id=first.pk, limit=1, stdout=out)
            self.assertEqual([r["id"] for r in json.loads(out.getvalue())["rows"]], [second.pk])
            out = StringIO()
            call_command("backfill_icon_taxonomy", dry_run=True, after_id=second.pk, stdout=out)
            empty = json.loads(out.getvalue())
            self.assertEqual(empty["rows"], [])
            self.assertEqual(empty["next_after_id"], second.pk)
            self.assertEqual(empty["summary"], {})
            self.assertIsNone(empty["budget_reservations"])
            provider.assert_not_called()
            wake.assert_not_called()
        self.assertEqual(before, {model: list(model.objects.order_by("pk").values()) for model in models})

    def test_enqueue_wakes_enabled_workers_and_dispatch_queues_without_inline(self):
        icon = self.icon()
        IconTaxonomyWork.objects.filter(icon=icon).delete()
        with override_settings(ICON_TAXONOMY_DISPATCH_ENABLED=True), patch.object(VisionProvider, "call") as provider:
            with patch("icons.tasks.dispatch_icon_taxonomy.apply_async") as wake:
                call_command("backfill_icon_taxonomy", stdout=StringIO())
                wake.assert_called_once()
            self.due(icon)
            with patch("icons.tasks.analyze_icon_taxonomy.delay") as task:
                call_command("dispatch_icon_taxonomy", budget="offline", stdout=StringIO())
                task.assert_called_once_with(icon.pk, budget_name="offline")
            provider.assert_not_called()

    def test_resume_blocked_preserves_cumulative_budget_and_reuses_observation(self):
        icon = self.icon()
        self.budget.max_calls = 1
        self.budget.save()
        provider = FixtureProvider()
        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=provider), "budget_blocked")
        self.budget.refresh_from_db()
        counters = (self.budget.calls, self.budget.tokens, self.budget.microdollars)
        call_command("backfill_icon_taxonomy", resume_budget_blocked=True, stdout=StringIO())
        self.budget.refresh_from_db()
        self.assertEqual((self.budget.calls, self.budget.tokens, self.budget.microdollars), counters)
        self.assertEqual(IconTaxonomyWork.objects.get(icon=icon).attempts, 0)
        self.due(icon)
        self.assertEqual(process_icon(icon.pk, provider=provider), "budget_blocked")
        self.assertEqual(len(provider.calls), 1)
        TaxonomyBudget.objects.create(
            name="next-authorized", enabled=True, max_calls=1, max_tokens=1000000, max_microdollars=1000000000
        )
        with (
            override_settings(ICON_TAXONOMY_DISPATCH_ENABLED=True),
            patch("icons.services.taxonomy_pipeline.VisionProvider", return_value=provider),
        ):
            call_command(
                "backfill_icon_taxonomy",
                resume_budget_blocked=True,
                dispatch=True,
                budget="next-authorized",
                stdout=StringIO(),
            )
        self.assertEqual([c[0] for c in provider.calls], ["observe", "compare"])

    def test_pair_members_and_sourced_open_group_keep_complete_evidence(self):
        pair = self.icon(title=PAIR, tags=[BAPTIST, EVANGELIST])
        self.analyze(pair, FixtureProvider(names=(BAPTIST, EVANGELIST)))
        assertions = pair.taxonomic_analyses.get(state="complete").assertions
        for name in (BAPTIST, EVANGELIST):
            assertion = assertions.get(concept=resolve(name))
            self.assertEqual((assertion.status, assertion.evidence_level), ("supported", "corroborated"))
        group = concept(
            "group",
            "Martyrs of the Valley and their Companions",
            {"members": [], "complete": False},
            source={"fixture": "sourced exact group"},
        )
        icon = self.icon(title=group.label)
        self.analyze(icon, FixtureProvider(names=(group.label,)))
        assertion = icon.taxonomic_analyses.get(state="complete").assertions.get(concept=group)
        self.assertEqual((assertion.status, assertion.evidence_level), ("supported", "corroborated"))
        group.refresh_from_db()
        self.assertEqual(group.definition["members"], [])

    def test_exact_multilingual_alias_and_visible_nonfestal_themes(self):
        alias(resolve(BAPTIST), "Հովհաննես Մկրտիչ", "hy", {"fixture": "exact bilingual source"})
        icon = self.icon(title="Հովհաննես Մկրտիչ")
        self.analyze(icon, FixtureProvider(names=("Հովհաննես Մկրտիչ",)))
        assertion = icon.taxonomic_analyses.get(state="complete").assertions.get(concept=resolve(BAPTIST))
        self.assertEqual(assertion.evidence_level, "corroborated")
        self.assertIsNone(resolve("Hovannes Mkrtich"))
        thematic = self.icon(title="generic")
        self.analyze(
            thematic,
            FixtureProvider(
                names=(),
                depiction="scene",
                activities=["shared_supper", "bread_and_cup", "sharing_food", "comforting", "washing_feet"],
            ),
        )
        assertions = thematic.taxonomic_analyses.get(state="complete").assertions
        for theme in ("mercy", "service", "gratitude", "humility"):
            self.assertEqual(assertions.get(concept=resolve(theme)).status, "supported")

    def test_budget_provisioning_requires_all_caps_and_cannot_reset_existing(self):
        from django.core.management.base import CommandError
        from icons.management.commands.dispatch_icon_taxonomy import configure_budget

        with self.assertRaises(CommandError):
            configure_budget({"budget": "new", "max_calls": 1})
        self.assertFalse(TaxonomyBudget.objects.filter(name="new").exists())
        reserve("compare", {})
        self.budget.refresh_from_db()
        before = (self.budget.calls, self.budget.tokens, self.budget.microdollars)
        caps = {
            "budget": "offline",
            "max_calls": self.budget.max_calls,
            "max_tokens": self.budget.max_tokens,
            "max_spend": self.budget.max_microdollars // 1000000,
        }
        self.assertEqual(configure_budget(caps), "offline")
        with self.assertRaises(CommandError):
            configure_budget({**caps, "max_calls": self.budget.max_calls + 1})
        self.budget.refresh_from_db()
        self.assertEqual((self.budget.calls, self.budget.tokens, self.budget.microdollars), before)
