"""Reuse the admin shell without granting or advertising admin access."""

from django.contrib import admin
from django.urls import reverse

from moderation.access import allowed


def shell_context(request):
    can_general = allowed(request.user, "general")
    can_crisis = allowed(request.user, "crisis")
    if request.user.is_active and request.user.is_staff:
        context = admin.site.each_context(request)
    else:
        context = {
            "site_title": admin.site.site_title,
            "site_header": admin.site.site_header,
            "has_permission": request.user.is_authenticated and request.user.is_active,
            "available_apps": [],
            "is_nav_sidebar_enabled": request.user.is_authenticated,
            "admin_sections": [],
        }
        if can_general or can_crisis:
            url = reverse("moderation-dashboard")
            context["admin_sections"] = [
                {
                    "slug": "moderation",
                    "name": "Moderation",
                    "icon": "shield",
                    "apps": [
                        {
                            "app_label": "moderation_review",
                            "name": "Review",
                            "app_url": url,
                            "models": [
                                {"name": "Dashboard", "object_name": "Dashboard", "admin_url": url, "view_only": True}
                            ],
                        }
                    ],
                }
            ]
    return {**context, "can_general": can_general, "can_crisis": can_crisis}
