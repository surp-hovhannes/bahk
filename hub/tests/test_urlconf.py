from django.test import SimpleTestCase
from django.urls import resolve, reverse
from django.urls.resolvers import URLPattern, URLResolver

from hub.urls import urlpatterns


def _url_routes(patterns):
    routes = []
    for pattern in patterns:
        if isinstance(pattern, URLPattern):
            routes.append((pattern.name, str(pattern.pattern)))
        elif isinstance(pattern, URLResolver):
            routes.extend(_url_routes(pattern.url_patterns))
    return routes


class HubURLConfTests(SimpleTestCase):
    def test_router_urls_are_included_once(self):
        routes = _url_routes(urlpatterns)

        self.assertEqual(routes.count(('user-list', '^users/$')), 1)
        self.assertEqual(routes.count(('group-list', '^groups/$')), 1)

    def test_legacy_fast_routes_have_distinct_names(self):
        self.assertEqual(reverse("fast_on_date"), "/api/user/fasts/")
        self.assertEqual(reverse("fast_on_date_without_user"), "/api/fast/")
        self.assertEqual(resolve("/api/user/fasts/").url_name, "fast_on_date")
        self.assertEqual(resolve("/api/fast/").url_name, "fast_on_date_without_user")

    def test_notification_routes_keep_both_prefixes_with_distinct_namespaces(self):
        self.assertEqual(
            reverse("notifications:unsubscribe"),
            "/hub/notifications/unsubscribe/",
        )
        self.assertEqual(
            reverse("api_notifications:unsubscribe"),
            "/api/notifications/unsubscribe/",
        )
        self.assertEqual(
            resolve("/hub/notifications/unsubscribe/").namespace,
            "notifications",
        )
        self.assertEqual(
            resolve("/api/notifications/unsubscribe/").namespace,
            "api_notifications",
        )
