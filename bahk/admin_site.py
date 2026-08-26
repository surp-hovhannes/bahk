"""Fast & Pray's branded, permission-aware Django admin site."""

from collections.abc import Iterable

from django.contrib.admin import AdminSite
from django.template.response import TemplateResponse
from django.urls import reverse


class FastAndPrayAdminSite(AdminSite):
    """Provide a branded shell and task-oriented admin landing page."""

    site_header = "Fast & Pray Admin"
    site_title = "Fast & Pray Admin"
    index_title = "Operations"
    index_template = "admin/index.html"

    section_definitions = (
        (
            "content-calendar",
            "Content & Calendar",
            "calendar",
            ("hub", "prayers", "learning_resources", "icons"),
        ),
        (
            "engagement-messaging",
            "Engagement & Messaging",
            "message",
            ("events", "notifications"),
        ),
        ("operations", "Operations", "sliders", ("app_management",)),
        (
            "administration",
            "Administration",
            "shield",
            ("auth", "django_celery_beat", "taggit"),
        ),
    )

    app_icons = {
        "hub": "home",
        "prayers": "heart",
        "learning_resources": "book",
        "icons": "image",
        "events": "activity",
        "notifications": "bell",
        "app_management": "sliders",
        "auth": "users",
        "django_celery_beat": "clock",
        "taggit": "tag",
    }

    @classmethod
    def decorate_app(cls, app: dict) -> dict:
        """Add a presentation-only icon name without mutating Django's app data."""
        return {
            **app,
            "icon": cls.app_icons.get(app["app_label"], "grid"),
        }

    @classmethod
    def group_app_list(cls, app_list: Iterable[dict]) -> list[dict]:
        """Group Django's already permission-filtered app list for the dashboard."""
        apps_by_label = {app["app_label"]: app for app in app_list}
        sections = []

        for slug, name, icon, app_labels in cls.section_definitions:
            apps = [
                cls.decorate_app(apps_by_label.pop(label))
                for label in app_labels
                if label in apps_by_label
            ]
            if apps:
                sections.append(
                    {"slug": slug, "name": name, "icon": icon, "apps": apps}
                )

        if apps_by_label:
            sections.append(
                {
                    "slug": "other",
                    "name": "Other",
                    "icon": "grid",
                    "apps": [
                        cls.decorate_app(app)
                        for app in sorted(
                            apps_by_label.values(),
                            key=lambda app: str(app["name"]).lower(),
                        )
                    ],
                }
            )

        return sections

    def get_quick_actions(self, app_list: Iterable[dict]) -> list[dict]:
        """Return shortcuts only when their backing model is visible to the user."""
        visible_models = {
            (app["app_label"], model["object_name"]): model
            for app in app_list
            for model in app["models"]
        }
        actions = []

        prayer_set = visible_models.get(("prayers", "PrayerSet"))
        if prayer_set and prayer_set.get("add_url"):
            actions.append(
                {
                    "name": "Import Prayer Sets",
                    "description": "Upload and preview prayer-set JSON before creating records.",
                    "icon": "upload",
                    "url": reverse("admin:prayers_import", current_app=self.name),
                }
            )

        event = visible_models.get(("events", "Event"))
        if event and event.get("admin_url"):
            actions.extend(
                (
                    {
                        "name": "User Engagement",
                        "description": "Review activity, participation, and feature-use trends.",
                        "icon": "users",
                        "url": reverse("admin:events_analytics", current_app=self.name),
                    },
                    {
                        "name": "App Analytics",
                        "description": "Review screen views, platforms, sessions, and app opens.",
                        "icon": "chart",
                        "url": reverse(
                            "admin:events_app_analytics", current_app=self.name
                        ),
                    },
                )
            )

        return actions

    def each_context(self, request):
        """Add task-oriented navigation without bypassing Django permissions."""
        context = super().each_context(request)
        context["admin_sections"] = self.group_app_list(context["available_apps"])
        return context

    def index(self, request, extra_context=None):
        """Render the dashboard without rebuilding Django's permission logic."""
        site_context = self.each_context(request)
        app_list = site_context["available_apps"]
        context = {
            **site_context,
            "title": self.index_title,
            "subtitle": None,
            "app_list": app_list,
            "admin_quick_actions": self.get_quick_actions(app_list),
            **(extra_context or {}),
        }
        request.current_app = self.name
        return TemplateResponse(request, self.index_template, context)
