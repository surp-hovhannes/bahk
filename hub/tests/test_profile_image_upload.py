import io
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import resolve, reverse
from rest_framework import status
from rest_framework.test import APIClient

from hub.models import Church, Profile
from hub.views.profile import (
    ProfileImageConfirmView,
    ProfileImagePresignView,
    ProfileImageUploadView,
)


class ProfileImageUploadRouteTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="profile-upload@example.com",
            email="profile-upload@example.com",
            password="testpass123",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            church=Church.objects.get(pk=Church.get_default_pk()),
        )
        self.client = APIClient()
        self.presign_url = reverse("profile-image-upload-presign")
        self.confirm_url = reverse("profile-image-upload-confirm")
        self.url = reverse("profile-image-upload")

    def _test_image(self, name="profile.gif"):
        image_content = (
            b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x21\xf9\x04"
            b"\x01\x0a\x00\x01\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02"
            b"\x02\x4c\x01\x00\x3b"
        )
        return SimpleUploadedFile(
            name=name,
            content=image_content,
            content_type="image/gif",
        )

    def test_mounted_url_resolves_to_profile_image_upload_view(self):
        match = resolve("/api/profile/image-upload/")

        self.assertEqual(self.url, "/api/profile/image-upload/")
        self.assertEqual(match.func.view_class, ProfileImageUploadView)
        self.assertEqual(match.url_name, "profile-image-upload")

    def test_mounted_direct_upload_urls_resolve_to_expected_views(self):
        presign = resolve("/api/profile/image-upload/presign/")
        confirm = resolve("/api/profile/image-upload/confirm/")

        self.assertEqual(self.presign_url, "/api/profile/image-upload/presign/")
        self.assertEqual(presign.func.view_class, ProfileImagePresignView)
        self.assertEqual(confirm.func.view_class, ProfileImageConfirmView)

    def test_profile_image_presign_requires_authentication(self):
        response = self.client.post(
            self.presign_url,
            {"file_name": "profile.gif", "content_type": "image/gif", "file_size": 43},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("hub.views.profile.ProfileImageUploadStorage")
    def test_authenticated_user_can_presign_profile_image_upload(self, storage_class):
        storage = storage_class.return_value
        storage.presign_put.return_value = "https://s3.invalid/presigned"
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.presign_url,
            {"file_name": "profile.gif", "content_type": "image/gif", "file_size": 43},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["upload_url"], "https://s3.invalid/presigned")
        self.assertEqual(
            response.data["key"],
            f"profile_images/originals/{self.user.pk}/" + response.data["key"].rsplit("/", 1)[1],
        )
        self.assertEqual(response.data["headers"], {"Content-Type": "image/gif"})
        storage.presign_put.assert_called_once_with(
            key=response.data["key"], content_type="image/gif", file_size=43
        )

    def test_profile_image_presign_rejects_unsupported_image_type(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.presign_url,
            {"file_name": "profile.exe", "content_type": "application/octet-stream", "file_size": 43},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("content_type", response.data)

    def test_profile_image_confirm_rejects_another_users_key(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.confirm_url,
            {"key": "profile_images/originals/999/profile.gif"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("key", response.data)

    @patch("hub.views.profile.ProfileImageUploadStorage")
    def test_confirmed_direct_upload_updates_profile_image(self, storage_class):
        storage = storage_class.return_value
        storage.head_object.return_value = {"ContentLength": 43, "ContentType": "image/gif"}
        storage.open.return_value = io.BytesIO(self._test_image().read())
        self.client.force_authenticate(user=self.user)
        key = f"profile_images/originals/{self.user.pk}/direct.gif"

        response = self.client.post(self.confirm_url, {"key": key}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.profile_image.name, key)
        self.assertIn("profile_image", response.data)

    @patch("hub.views.profile.ProfileImageUploadStorage")
    def test_confirm_rejects_invalid_uploaded_image(self, storage_class):
        storage = storage_class.return_value
        storage.head_object.return_value = {"ContentLength": 3, "ContentType": "image/gif"}
        storage.open.return_value = io.BytesIO(b"bad")
        self.client.force_authenticate(user=self.user)
        key = f"profile_images/originals/{self.user.pk}/invalid.gif"

        response = self.client.post(self.confirm_url, {"key": key}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        storage.delete.assert_called_once_with(key)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.profile_image)

    @patch("hub.views.profile.ProfileImageUploadStorage")
    def test_confirm_rejects_oversized_uploaded_image(self, storage_class):
        storage = storage_class.return_value
        storage.head_object.return_value = {
            "ContentLength": 5 * 1024 * 1024 + 1,
            "ContentType": "image/gif",
        }
        self.client.force_authenticate(user=self.user)
        key = f"profile_images/originals/{self.user.pk}/oversized.gif"

        response = self.client.post(self.confirm_url, {"key": key}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        storage.delete.assert_called_once_with(key)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.profile_image)

    def test_profile_image_upload_requires_authentication(self):
        response = self.client.patch(
            self.url,
            data={"profile_image": self._test_image()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.profile_image)

    def test_authenticated_user_can_upload_profile_image(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.patch(
            self.url,
            data={"profile_image": self._test_image()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.profile_image)
        self.assertTrue(
            self.profile.profile_image.name.startswith("profile_images/originals/")
        )
        self.assertIn("profile_image", response.json())

    def test_profile_image_upload_rejects_missing_file(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.put(self.url, data={}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("profile_image", response.json())
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.profile_image)
