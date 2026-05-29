from django.test import SimpleTestCase
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
