from contextlib import nullcontext
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from hub.models import Church, Day, Devotional
from learning_resources.models import Video


@override_settings(MODELTRANS_AVAILABLE_LANGUAGES=["en", "hy"])
class DevotionalWriteAPITests(APITestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Write Test Church")
        self.day = Day.objects.create(date=date(2026, 8, 8), church=self.church)
        self.other_day = Day.objects.create(date=date(2026, 8, 9), church=self.church)
        self.video = Video.objects.create(
            title="Write test video",
            description="Video fallback",
            category="general",
            language_code="en",
        )
        self.other_video = Video.objects.create(
            title="Other video",
            category="general",
            language_code="en",
        )
        users = get_user_model()
        self.staff = users.objects.create_user(username="staff", is_staff=True)
        self.user = users.objects.create_user(username="ordinary")
        self.create_payload = {
            "day": self.day.pk,
            "video": self.video.pk,
            "language_code": "en",
            "description": "New devotional",
            "order": 1,
        }

    def test_anonymous_and_non_staff_create_are_rejected_without_mutation(self):
        anonymous = self.client.post(
            "/api/devotionals/", self.create_payload, format="json"
        )
        self.client.force_login(self.user)
        non_staff = self.client.post("/api/devotionals/", self.create_payload, format="json")

        self.assertIn(
            anonymous.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )
        self.assertEqual(non_staff.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Devotional.objects.exists())

    def test_staff_create_returns_public_representation_and_updates_video_category(self):
        self.client.force_login(self.staff)
        response = self.client.post("/api/devotionals/", self.create_payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(response.data),
            {
                "id",
                "title",
                "description",
                "thumbnail",
                "thumbnail_small",
                "video",
                "date",
                "fast_id",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(response.data["title"], "Write test video")
        self.assertEqual(response.data["description"], "New devotional")
        self.video.refresh_from_db()
        self.assertEqual(self.video.category, "devotional")

    def test_staff_patch_is_partial_and_returns_public_representation(self):
        devotional = Devotional.objects.create(
            day=self.day,
            video=self.video,
            language_code="en",
            description="Before",
            order=1,
        )
        self.client.force_login(self.staff)

        response = self.client.patch(
            f"/api/devotionals/{devotional.pk}/",
            {"description": "After"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["description"], "After")
        devotional.refresh_from_db()
        self.assertEqual(devotional.day, self.day)
        self.assertEqual(devotional.video, self.video)
        self.assertEqual(devotional.order, 1)
        self.assertEqual(devotional.language_code, "en")

    def test_anonymous_and_non_staff_patch_are_rejected_without_mutation(self):
        devotional = Devotional.objects.create(
            day=self.day, video=self.video, language_code="en", description="Before", order=1
        )
        url = f"/api/devotionals/{devotional.pk}/"
        anonymous = self.client.patch(url, {"description": "Nope"}, format="json")
        self.client.force_login(self.user)
        non_staff = self.client.patch(url, {"description": "Nope"}, format="json")

        self.assertIn(
            anonymous.status_code,
            {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN},
        )
        self.assertEqual(non_staff.status_code, status.HTTP_403_FORBIDDEN)
        devotional.refresh_from_db()
        self.assertEqual(devotional.description, "Before")

    def test_create_requires_relationships_language_and_order(self):
        self.client.force_login(self.staff)
        response = self.client.post("/api/devotionals/", {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.data), {"day", "video", "language_code", "order"})

    def test_create_rejects_omitted_and_null_order(self):
        self.client.force_login(self.staff)
        without_order = {
            key: value
            for key, value in self.create_payload.items()
            if key != "order"
        }

        omitted = self.client.post("/api/devotionals/", without_order, format="json")
        explicit_null = self.client.post(
            "/api/devotionals/", {**self.create_payload, "order": None}, format="json"
        )

        self.assertEqual(omitted.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(explicit_null.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("order", omitted.data)
        self.assertIn("order", explicit_null.data)
        self.assertFalse(Devotional.objects.exists())

    def test_create_rejects_unknown_fields_without_mutation(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            "/api/devotionals/",
            {**self.create_payload, "id": 9, "video_url": "https://invalid"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.data), {"id", "video_url"})
        self.assertFalse(Devotional.objects.exists())

    def test_patch_rejects_unknown_fields_without_mutation(self):
        devotional = Devotional.objects.create(
            day=self.day, video=self.video, language_code="en", description="Before", order=1
        )
        self.client.force_login(self.staff)
        response = self.client.patch(
            f"/api/devotionals/{devotional.pk}/",
            {"description": "After", "created_at": "2026-01-01", "category": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.data), {"category", "created_at"})
        devotional.refresh_from_db()
        self.assertEqual(devotional.description, "Before")

    def test_invalid_relationships_language_and_negative_order_are_rejected(self):
        self.client.force_login(self.staff)
        invalid_payloads = [
            {**self.create_payload, "day": 999999},
            {**self.create_payload, "video": 999999},
            {**self.create_payload, "language_code": "fr"},
            {**self.create_payload, "order": -1},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/api/devotionals/", payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Devotional.objects.exists())

    def test_duplicate_day_order_language_is_a_validation_error(self):
        Devotional.objects.create(
            day=self.day, video=self.other_video, language_code="en", order=1
        )
        self.client.force_login(self.staff)

        response = self.client.post("/api/devotionals/", self.create_payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Devotional.objects.count(), 1)

    def test_empty_patch_invalid_fields_and_missing_target_do_not_mutate(self):
        devotional = Devotional.objects.create(
            day=self.day, video=self.video, language_code="en", description="Before", order=1
        )
        self.client.force_login(self.staff)
        url = f"/api/devotionals/{devotional.pk}/"

        responses = [
            self.client.patch(url, {}, format="json"),
            self.client.patch(url, {"day": 999999}, format="json"),
            self.client.patch(url, {"video": 999999}, format="json"),
            self.client.patch(url, {"language_code": "fr"}, format="json"),
            self.client.patch(url, {"order": -1}, format="json"),
        ]

        self.assertTrue(
            all(
                response.status_code == status.HTTP_400_BAD_REQUEST
                for response in responses
            )
        )
        missing = self.client.patch(
            "/api/devotionals/999999/", {"description": "x"}, format="json"
        )
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)
        devotional.refresh_from_db()
        self.assertEqual(devotional.description, "Before")

    def test_patch_rejects_duplicate_day_order_language(self):
        Devotional.objects.create(
            day=self.day, video=self.video, language_code="en", order=1
        )
        target = Devotional.objects.create(
            day=self.other_day, video=self.other_video, language_code="en", order=1
        )
        self.client.force_login(self.staff)

        response = self.client.patch(
            f"/api/devotionals/{target.pk}/", {"day": self.day.pk}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        target.refresh_from_db()
        self.assertEqual(target.day, self.other_day)
        self.assertEqual(Devotional.objects.count(), 2)

    def test_patch_rejects_explicit_null_order_but_may_omit_it(self):
        devotional = Devotional.objects.create(
            day=self.day, video=self.video, language_code="en", order=1
        )
        self.client.force_login(self.staff)
        url = f"/api/devotionals/{devotional.pk}/"

        response = self.client.patch(url, {"order": None}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        devotional.refresh_from_db()
        self.assertEqual(devotional.order, 1)

    def test_create_matching_duplicate_integrity_error_returns_validation_error(self):
        self.client.force_login(self.staff)

        def create_conflict_then_raise(*_args, **_kwargs):
            Devotional.objects.create(
                day=self.day,
                video=self.other_video,
                language_code="en",
                order=1,
            )
            raise IntegrityError("database constraint details")

        with patch(
            "hub.views.devotionals.DevotionalWriteSerializer.save",
            side_effect=create_conflict_then_raise,
        ), patch(
            "hub.views.devotionals.transaction.atomic", return_value=nullcontext()
        ):
            response = self.client.post(
                "/api/devotionals/", self.create_payload, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                "non_field_errors": [
                    "A devotional with this day, order, and language code already exists."
                ]
            },
        )
        self.assertNotIn("database constraint details", str(response.data))

    def test_create_nonmatching_integrity_error_is_propagated(self):
        self.client.force_login(self.staff)
        error = IntegrityError("unrelated database constraint")

        with patch(
            "hub.views.devotionals.DevotionalWriteSerializer.save",
            side_effect=error,
        ):
            with self.assertRaises(IntegrityError) as raised:
                self.client.post("/api/devotionals/", self.create_payload, format="json")

        self.assertIs(raised.exception, error)

    def test_patch_matching_duplicate_integrity_error_returns_validation_error(self):
        devotional = Devotional.objects.create(
            day=self.other_day, video=self.video, language_code="en", order=1
        )
        self.client.force_login(self.staff)

        def create_conflict_then_raise(*_args, **_kwargs):
            Devotional.objects.create(
                day=self.day,
                video=self.other_video,
                language_code=devotional.language_code,
                order=devotional.order,
            )
            raise IntegrityError("database constraint details")

        with patch(
            "hub.views.devotionals.DevotionalWriteSerializer.save",
            side_effect=create_conflict_then_raise,
        ), patch(
            "hub.views.devotionals.transaction.atomic", return_value=nullcontext()
        ):
            response = self.client.patch(
                f"/api/devotionals/{devotional.pk}/",
                {"day": self.day.pk, "description": "After"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {
                "non_field_errors": [
                    "A devotional with this day, order, and language code already exists."
                ]
            },
        )
        devotional.refresh_from_db()
        self.assertNotEqual(devotional.description, "After")

    def test_patch_nonmatching_integrity_error_is_propagated(self):
        devotional = Devotional.objects.create(
            day=self.day, video=self.video, language_code="en", order=1
        )
        self.client.force_login(self.staff)
        error = IntegrityError("unrelated database constraint")

        with patch(
            "hub.views.devotionals.DevotionalWriteSerializer.save",
            side_effect=error,
        ):
            with self.assertRaises(IntegrityError) as raised:
                self.client.patch(
                    f"/api/devotionals/{devotional.pk}/",
                    {"description": "After"},
                    format="json",
                )

        self.assertIs(raised.exception, error)

    def test_put_and_delete_remain_unsupported(self):
        devotional = Devotional.objects.create(
            day=self.day, video=self.video, language_code="en", order=1
        )
        self.client.force_login(self.staff)
        url = f"/api/devotionals/{devotional.pk}/"

        self.assertEqual(
            self.client.put(url, self.create_payload, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
