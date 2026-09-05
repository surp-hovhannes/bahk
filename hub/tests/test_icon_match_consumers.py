"""End-to-end consumers using the real orchestration with an injected provider."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from hub.models import Church, Feast
from hub.services.icon_match_service import IconMatchOutcome
from hub.tasks.icon_tasks import match_icon_to_feast_task
from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate
from icons.models import Icon
from prayers.models import Prayer
from prayers.tasks import match_icons_for_imported_prayers_task


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "semantic-consumers"}}
)
class SemanticConsumerTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Semantic fixture church")
        self.other = Church.objects.create(name="Other fixture church")
        self.icon = Icon.objects.create(title="Saint Narek of the Lake", church=self.church, image="fixture.jpg")
        self.foreign = Icon.objects.create(title="Foreign portrait", church=self.other, image="foreign.jpg")
        self.client = APIClient()
        self.provider = FixtureProvider(
            analysis_for("Saint Narek of the Lake"), [candidate(self.icon.id, self.icon.title)]
        )
        self.provider_patch = patch("hub.services.icon_match_service.OpenAIIconProvider", return_value=self.provider)
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)
        self.sleep_patch = patch("prayers.tasks.time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        cache.clear()

    def post(self, **kwargs):
        return self.client.post(
            "/api/icons/match/",
            {"prompt": self.icon.title, "church_id": self.church.id, "return_format": "id", **kwargs},
            format="json",
        )

    def test_api_uses_all_scoped_records_and_returns_related_reasons(self):
        self.icon.title = "Prodigal Son"
        self.icon.save(update_fields=["title"])
        for i in range(35):
            Icon.objects.create(title=f"repentance literal {i}", church=self.church, image="x.jpg")
        self.provider.analysis = analysis_for(None)
        self.provider.analysis["context"] = [{"ref": "request:primary", "quote": "repentance"}]
        self.provider.matches = [
            candidate(
                self.icon.id,
                self.icon.title,
                "thematic",
                covered_subjects=[],
                identity_qualified=False,
                evidence=[{"ref": f"icon:{self.icon.id}:title", "quote": self.icon.title, "role": "topic"}],
                reason="The return of the prodigal son relates to repentance.",
            )
        ]
        response = self.post(prompt="repentance")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "complete")
        self.assertEqual(response.data["assessed_count"], 36)
        match = response.data["matches"][0]
        self.assertEqual(match["icon_id"], self.icon.id)
        self.assertEqual(match["relation"], "thematic")
        self.assertTrue(match["reason"])
        self.assertFalse(match["auto_assignable"])
        supplied = [
            r["id"] for stage, payload, _ in self.provider.calls if stage == "assess" for r in payload["catalogue"]
        ]
        self.assertEqual(len(supplied), 36)
        self.assertNotIn(self.foreign.id, supplied)

    def test_complete_cache_and_invalidation(self):
        first = self.post()
        self.assertEqual(first.status_code, 200)
        self.post()
        self.assertEqual(len(self.provider.calls), 3)
        self.icon.tags.add("qualified identity")
        response = self.post(return_format="full")
        self.assertEqual(response.data["matches"][0]["icon"]["id"], self.icon.id)
        self.post()
        self.assertEqual(len(self.provider.calls), 9)

    def test_partial_and_unavailable_are_never_cached(self):
        for status, expected in [("partial", 200), ("unavailable", 503)]:
            with patch("icons.views.match_icons", return_value=IconMatchOutcome(status=status)) as matcher:
                self.assertEqual(self.post().status_code, expected)
                self.assertEqual(self.post().status_code, expected)
                self.assertEqual(matcher.call_count, 2)

    def test_out_of_scope_provider_id_cannot_escape_api(self):
        def mutate(stage, payload, result):
            if stage == "assess":
                result["matches"][0]["id"] = self.foreign.id

        self.provider.mutate = mutate
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["matches"], [])

    def test_prayer_exact_assignment_and_existing_selection_preserved(self):
        prayer = Prayer.objects.create(title=self.icon.title, text="A prayer", church=self.church)
        match_icons_for_imported_prayers_task([prayer.id], self.church.id)
        prayer.refresh_from_db()
        self.assertEqual(prayer.icon_id, self.icon.id)
        self.provider.calls.clear()
        match_icons_for_imported_prayers_task([prayer.id], self.church.id)
        self.assertEqual(self.provider.calls, [])

    def test_prayer_related_match_is_not_persisted(self):
        self.provider.matches[0]["relation"] = "related_specific"
        prayer = Prayer.objects.create(title=self.icon.title, text="A prayer", church=self.church)
        match_icons_for_imported_prayers_task([prayer.id], self.church.id)
        prayer.refresh_from_db()
        self.assertIsNone(prayer.icon_id)

    def test_prayer_scope_and_race_guard(self):
        prayer = Prayer.objects.create(title=self.icon.title, text="A prayer", church=self.church)
        choice = Icon.objects.create(title="User selection", church=self.church, image="choice.jpg")

        def mutate(stage, payload, result):
            if stage == "verify":
                Prayer.objects.filter(pk=prayer.pk).update(icon=choice)

        self.provider.mutate = mutate
        match_icons_for_imported_prayers_task([prayer.id], self.other.id)
        self.assertEqual(self.provider.calls, [])
        match_icons_for_imported_prayers_task([prayer.id], self.church.id)
        prayer.refresh_from_db()
        self.assertEqual(prayer.icon_id, choice.id)

    def test_feast_race_guard_preserves_selection(self):
        with patch("hub.signals.match_icon_to_feast_task.delay"):
            feast = Feast.objects.create(name=self.icon.title, church=self.church)
        choice = Icon.objects.create(title="User selection", church=self.church, image="choice.jpg")

        def mutate(stage, payload, result):
            if stage == "verify":
                Feast.objects.filter(pk=feast.pk).update(icon=choice)

        self.provider.mutate = mutate
        match_icon_to_feast_task(feast.pk)
        feast.refresh_from_db()
        self.assertEqual(feast.icon_id, choice.id)

    def test_api_preserves_useful_partial_results_without_caching(self):
        def mutate(stage, payload, result):
            if stage == "verify":
                raise RuntimeError("provider unavailable")

        self.provider.mutate = mutate
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "partial")
        self.assertFalse(response.data["matches"][0]["auto_assignable"])
        self.post()
        self.assertEqual(len(self.provider.calls), 6)

    def test_complete_no_match_is_cached(self):
        self.provider.matches = []
        response = self.post()
        self.assertEqual(response.data["status"], "complete")
        self.assertEqual(response.data["matches"], [])
        self.post()
        self.assertEqual(len(self.provider.calls), 2)

    def test_contradictory_positive_event_summary_is_not_cached(self):
        self.provider.matches = []

        def mutate(stage, payload, result):
            if stage == "assess":
                result["exact_event_exists"] = True

        self.provider.mutate = mutate
        for _ in range(2):
            response = self.post()
            self.assertEqual(response.data["status"], "partial")
            self.assertEqual(response.data["matches"], [])
        self.assertEqual(len(self.provider.calls), 4)

    def test_synchronous_endpoint_supplies_conservative_limits(self):
        with patch("icons.views.match_icons", return_value=IconMatchOutcome()) as matcher:
            self.post()
        limits = matcher.call_args.kwargs["limits"]
        self.assertEqual(limits.total_seconds, 20)
        self.assertEqual(limits.call_seconds, 20)
        from hub.services.icon_match_service import MatchLimits
        self.assertEqual(MatchLimits().total_seconds, 180)
