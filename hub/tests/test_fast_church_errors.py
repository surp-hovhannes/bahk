"""Legacy fast church errors must survive JSON and browsable API rendering."""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from hub.models import Church, Day, Fast, Profile


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
class FastChurchErrorTests(TestCase):
    paths = ("/api/fast/", "/api/fasts/")
    accepts = ("application/json", "text/html")

    def setUp(self):
        Church.objects.all().delete()
        self.church = Church.objects.create(name="Only church")
        self.fast = Fast.objects.create(name="Visible fast", church=self.church)
        Day.objects.create(date=timezone.localdate(), church=self.church, fast=self.fast)
        self.client = APIClient()

    def check_response(self, path, params, accept, expected, payload=None):
        response = self.client.get(path, params, HTTP_ACCEPT=accept)
        self.assertEqual(response.status_code, expected)
        self.assertTrue(response.is_rendered)
        self.assertTrue(response.content)
        self.assertTrue(response["Content-Type"].startswith(accept))
        if payload is not None:
            self.assertEqual(response.data, payload)
            if accept == "application/json":
                self.assertEqual(response.json(), payload)
            else:
                for message in payload.values() if isinstance(payload, dict) else payload:
                    self.assertIn(message.encode(), response.content)
        return response

    def test_anonymous_church_errors_render(self):
        cases = (
            ({}, "church_id query parameter is required."),
            ({"church_id": ""}, "church_id query parameter is required."),
            ({"church_id": "abc"}, "church_id must be a valid integer."),
            ({"church_id": "1.5"}, "church_id must be a valid integer."),
            ({"church_id": "9" * 100}, "Church with the provided ID does not exist."),
            ({"church_id": "0"}, "Church with the provided ID does not exist."),
            ({"church_id": "-1"}, "Church with the provided ID does not exist."),
            ({"church_id": str(self.church.pk + 1000)}, "Church with the provided ID does not exist."),
        )
        for path in self.paths:
            for accept in self.accepts:
                for params, message in cases:
                    with self.subTest(path=path, accept=accept, params=params):
                        self.check_response(path, params, accept, 400, [message])

    def test_anonymous_explicit_church_renders_the_requested_fast(self):
        other = Church.objects.create(name="Other church")
        other_fast = Fast.objects.create(name="Other fast", church=other)
        Day.objects.create(date=timezone.localdate(), church=other, fast=other_fast)
        for path in self.paths:
            for accept in self.accepts:
                with self.subTest(path=path, accept=accept):
                    response = self.check_response(path, {"church_id": other.pk}, accept, 200)
                    items = response.data if path.endswith("fasts/") else [response.data]
                    self.assertEqual([item["id"] for item in items], [other_fast.pk])

    def test_authenticated_incomplete_profile_uses_only_church(self):
        user = User.objects.create_user(username="incomplete@example.test")
        profile = Profile.objects.create(user=user)
        self.client.force_authenticate(user)
        for path in self.paths:
            for accept in self.accepts:
                with self.subTest(path=path, accept=accept):
                    Profile.objects.filter(pk=profile.pk).update(church=None)
                    user = User.objects.get(pk=user.pk)
                    self.client.force_authenticate(user)
                    params = {"church_id": "invalid"} if path.endswith("fasts/") else {}
                    self.check_response(path, params, accept, 200)
                    profile.refresh_from_db()
                    self.assertEqual(profile.church_id, self.church.pk)

    def test_authenticated_multiple_churches_require_selection(self):
        Church.objects.create(name="Other church")
        user = User.objects.create_user(username="unselected@example.test")
        profile = Profile.objects.create(user=user)
        self.client.force_authenticate(user)
        for path in self.paths:
            for accept in self.accepts:
                with self.subTest(path=path, accept=accept):
                    params = {"church_id": self.church.pk} if path.endswith("fasts/") else {}
                    self.check_response(path, params, accept, 400, {
                        "code": "church_selection_required", "detail": "Select a church before continuing.",
                    })
                    profile.refresh_from_db()
                    self.assertIsNone(profile.church_id)

    def test_authenticated_missing_profile_requires_selection_even_with_only_church(self):
        user = User.objects.create_user(username="no-profile@example.test")
        self.assertFalse(Profile.objects.filter(user=user).exists())
        self.client.force_authenticate(user)
        for path in self.paths:
            for accept in self.accepts:
                with self.subTest(path=path, accept=accept):
                    self.check_response(path, {}, accept, 400, {
                        "code": "church_selection_required", "detail": "Select a church before continuing.",
                    })
        self.assertFalse(Profile.objects.filter(user=user).exists())

    def test_authenticated_explicit_id_preserves_each_endpoint_contract(self):
        other = Church.objects.create(name="Other church")
        other_fast = Fast.objects.create(name="Other fast", church=other)
        Day.objects.create(date=timezone.localdate(), church=other, fast=other_fast)
        user = User.objects.create_user(username="selected@example.test")
        profile = Profile.objects.create(user=user, church=self.church)
        self.client.force_authenticate(user)
        for path in self.paths:
            for accept in self.accepts:
                with self.subTest(path=path, accept=accept):
                    response = self.check_response(path, {"church_id": other.pk}, accept, 200)
                    items = response.data if path.endswith("fasts/") else [response.data]
                    expected_fast = self.fast if path.endswith("fasts/") else other_fast
                    self.assertEqual([item["id"] for item in items], [expected_fast.pk])
                    selected = self.check_response(path, {}, accept, 200)
                    selected_items = selected.data if path.endswith("fasts/") else [selected.data]
                    self.assertEqual([item["id"] for item in selected_items], [self.fast.pk])
        profile.refresh_from_db()
        self.assertEqual(profile.church_id, self.church.pk)
