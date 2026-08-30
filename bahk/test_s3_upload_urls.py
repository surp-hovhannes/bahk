from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import TestCase
from django.urls import resolve, reverse
from s3_file_field.widgets import S3FileInput, get_base_url


class ProtectedS3FileFieldURLsTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.staff = users.objects.create_user("legacy-upload-staff", is_staff=True)
        self.user = users.objects.create_user("legacy-upload-user")

    def test_paths_names_and_widget_base_url_are_preserved(self):
        expected = {
            "upload-initialize": "/api/s3-upload/upload-initialize/",
            "upload-complete": "/api/s3-upload/upload-complete/",
            "finalize": "/api/s3-upload/finalize/",
        }
        for name, path in expected.items():
            self.assertEqual(reverse(f"s3_file_field:{name}"), path)
            self.assertEqual(resolve(path).url_name, name)
        get_base_url.cache_clear()
        self.assertEqual(get_base_url(), "/api/s3-upload")
        context = S3FileInput().get_context("video", None, {})
        self.assertEqual(context["widget"]["attrs"]["data-s3fileinput"], "/api/s3-upload")

    @patch("s3_file_field.views.upload_initialize")
    def test_anonymous_and_non_staff_are_denied_before_delegation(self, package_view):
        url = reverse("s3_file_field:upload-initialize")
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url).status_code, 302)
        package_view.assert_not_called()

    def test_staff_requests_delegate_to_each_installed_package_view(self):
        self.client.force_login(self.staff)
        for name in ("upload_initialize", "upload_complete", "finalize"):
            url_name = name.replace("_", "-")
            with patch(
                f"s3_file_field.views.{name}",
                return_value=JsonResponse({"delegated": name}),
            ) as package_view:
                response = self.client.post(reverse(f"s3_file_field:{url_name}"))
            self.assertEqual(response.status_code, 200)
            package_view.assert_called_once()
