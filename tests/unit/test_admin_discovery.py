"""Tests for admin search, filter, and task-oriented navigation contracts."""

from types import SimpleNamespace

from django.contrib.admin.sites import AdminSite
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, SimpleTestCase

from bahk.admin_site import FastAndPrayAdminSite
from hub.admin import FastAdmin, FeastAdmin, LLMPromptAdmin, ProfileAdmin
from hub.models import Fast, Feast, LLMPrompt, Profile
from icons.admin import IconAdmin
from icons.models import Icon
from learning_resources.admin import BookmarkAdmin, BookmarkContentTypeFilter, VideoAdmin
from learning_resources.models import Bookmark, Video


class AdminDiscoveryTests(SimpleTestCase):
    def test_content_models_search_the_fields_operators_recognize(self):
        site = AdminSite()

        self.assertIn("church__name", FastAdmin(Fast, site).search_fields)
        self.assertIn("user__email", ProfileAdmin(Profile, site).search_fields)
        self.assertIn("church__name", FeastAdmin(Feast, site).search_fields)
        self.assertIn("model", LLMPromptAdmin(LLMPrompt, site).search_fields)
        self.assertIn("church__name", IconAdmin(Icon, site).search_fields)

    def test_video_language_is_visible_and_filterable(self):
        video_admin = VideoAdmin(Video, AdminSite())

        self.assertIn("language_code", video_admin.list_display)
        self.assertIn("language_code", video_admin.list_filter)

    def test_bookmark_uses_a_focused_content_type_filter(self):
        bookmark_admin = BookmarkAdmin(Bookmark, AdminSite())

        self.assertIn(BookmarkContentTypeFilter, bookmark_admin.list_filter)
        self.assertNotIn(ContentType, bookmark_admin.list_filter)

    def test_sidebar_sections_are_built_from_permission_filtered_apps(self):
        site = FastAndPrayAdminSite(name="test-admin")
        available_apps = [
            {
                "app_label": "hub",
                "name": "Hub",
                "app_url": "/admin/hub/",
                "models": [],
            }
        ]
        site.get_app_list = lambda request: available_apps
        request = RequestFactory().get("/admin/hub/")
        request.user = SimpleNamespace(is_active=True, is_staff=True)

        context = site.each_context(request)

        self.assertEqual(context["available_apps"], available_apps)
        self.assertEqual(context["admin_sections"][0]["name"], "Content & Calendar")
