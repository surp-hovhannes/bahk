"""Regression tests for the Fast & Pray Django admin shell."""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from bahk.admin_site import FastAndPrayAdminSite


User = get_user_model()


class FastAndPrayAdminSiteTests(TestCase):
    """Verify branding, grouping, and permission-aware dashboard links."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="brand-admin",
            email="brand-admin@example.com",
            password="adminpass123",
        )
        cls.staff_user = User.objects.create_user(
            username="brand-staff",
            email="brand-staff@example.com",
            password="staffpass123",
            is_staff=True,
        )

    def test_default_site_uses_fast_and_pray_admin(self):
        self.assertIsInstance(admin.site, FastAndPrayAdminSite)
        self.assertEqual(admin.site.site_header, "Fast & Pray Admin")
        self.assertEqual(admin.site.site_title, "Fast & Pray Admin")
        self.assertEqual(admin.site.index_title, "Operations")

    def test_brand_static_files_are_discoverable(self):
        paths = (
            "admin/brand/app-icon.png",
            "admin/brand/wordmark.png",
            "admin/brand/favicon.svg",
            "admin/brand/admin-icons.svg",
            "admin/css/fastandpray-admin.css",
            "admin/css/fastandpray-analytics.css",
        )

        for path in paths:
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path))

    def test_login_uses_brand_shell_and_wordmark(self):
        response = self.client.get(reverse("admin:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fast &amp; Pray Admin")
        self.assertContains(response, "admin/css/fastandpray-admin.css")
        self.assertContains(response, "admin/brand/app-icon.png")
        self.assertContains(response, "admin/brand/wordmark.png")
        self.assertContains(response, "admin/brand/favicon.svg")

    def test_superuser_dashboard_has_sections_and_quick_actions(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Content &amp; Calendar")
        self.assertContains(response, "Engagement &amp; Messaging")
        self.assertContains(response, "Operations")
        self.assertContains(response, "Administration")
        self.assertContains(response, "Import Prayer Sets")
        self.assertContains(response, "User Engagement")
        self.assertContains(response, "App Analytics")
        self.assertContains(response, "admin/brand/admin-icons.svg")
        self.assertContains(response, "fp-icon-calendar")
        self.assertContains(response, "fp-icon-model")

        section_names = [section["name"] for section in response.context["admin_sections"]]
        self.assertEqual(
            section_names[:4],
            [
                "Content & Calendar",
                "Engagement & Messaging",
                "Operations",
                "Administration",
            ],
        )

    def test_grouped_navigation_is_available_on_model_pages(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("admin:prayers_prayerrequest_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="fp-nav-section"')
        self.assertContains(response, "Content &amp; Calendar")
        self.assertContains(response, "Prayer Requests")
        self.assertContains(response, 'aria-current="page"')

    def test_staff_without_model_permissions_has_no_quick_actions(self):
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["admin_sections"], [])
        self.assertEqual(response.context["admin_quick_actions"], [])
        self.assertNotContains(response, reverse("admin:events_analytics"))
        self.assertNotContains(response, reverse("admin:prayers_import"))

    def test_quick_actions_follow_model_permissions(self):
        event_permission = Permission.objects.get(
            content_type__app_label="events",
            codename="view_event",
        )
        self.staff_user.user_permissions.add(event_permission)
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("admin:index"))

        self.assertContains(response, reverse("admin:events_analytics"))
        self.assertContains(response, reverse("admin:events_app_analytics"))
        self.assertNotContains(response, reverse("admin:prayers_import"))

    def test_unknown_apps_are_kept_in_sorted_other_section(self):
        app_list = [
            {"app_label": "zeta", "name": "Zeta", "models": []},
            {"app_label": "hub", "name": "Hub", "models": []},
            {"app_label": "alpha", "name": "Alpha", "models": []},
        ]

        sections = FastAndPrayAdminSite.group_app_list(app_list)

        self.assertEqual([section["name"] for section in sections], ["Content & Calendar", "Other"])
        self.assertEqual(
            [app["app_label"] for app in sections[-1]["apps"]],
            ["alpha", "zeta"],
        )

    def test_empty_sections_are_omitted(self):
        sections = FastAndPrayAdminSite.group_app_list(
            [{"app_label": "events", "name": "Events", "models": []}]
        )

        self.assertEqual(
            sections,
            [
                {
                    "slug": "engagement-messaging",
                    "name": "Engagement & Messaging",
                    "icon": "message",
                    "apps": [
                        {
                            "app_label": "events",
                            "name": "Events",
                            "models": [],
                            "icon": "activity",
                        }
                    ],
                }
            ],
        )
