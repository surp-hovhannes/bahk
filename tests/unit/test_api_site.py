from django.test import SimpleTestCase
from django.test.client import Client
from django.urls import NoReverseMatch, resolve, reverse

from bahk.public_api.v1.urls import urlpatterns as public_api_urlpatterns

from bahk.views import ApiDocsView, LandingPageView


class LandingPageTests(SimpleTestCase):
    def test_root_renders_branded_api_gateway(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "api_landing.html")
        self.assertContains(response, "Fast &amp; Pray API", html=False)
        self.assertContains(response, "The secure service behind Fast &amp; Pray.")
        self.assertNotContains(response, "Documentation coming soon")
        self.assertContains(response, 'name="robots" content="noindex,nofollow"')

    def test_root_exposes_expected_service_links(self):
        response = self.client.get("/")

        self.assertContains(response, 'href="https://fastandpray.app"')
        self.assertContains(response, 'href="https://web.fastandpray.app"')
        self.assertContains(response, f'href="{reverse("api-docs")}"')
        self.assertContains(response, f'href="{reverse("admin:index")}"')
        self.assertContains(response, f'href="{reverse("health_check")}"')

    def test_root_resolves_to_landing_page(self):
        match = resolve("/")

        self.assertEqual(match.url_name, "api-home")
        self.assertIs(match.func.view_class, LandingPageView)

    def test_existing_hub_api_root_remains_available(self):
        response = self.client.get("/hub/", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)


class ApiDocsTests(SimpleTestCase):
    def test_docs_route_renders_coming_soon_state(self):
        response = self.client.get("/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "api_docs.html")
        self.assertContains(response, "API Docs")
        self.assertContains(response, "Coming soon")
        self.assertContains(response, "preparing the API for public use")
        self.assertNotContains(response, "/api/readings/")
        self.assertNotContains(response, "/api/fasts/")
        self.assertNotContains(response, "/api/feasts/")

    def test_docs_route_resolves_to_docs_view(self):
        match = resolve("/docs/")

        self.assertEqual(match.url_name, "api-docs")
        self.assertIs(match.func.view_class, ApiDocsView)

    def test_docs_are_linked_from_the_shared_header(self):
        response = self.client.get("/docs/")

        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, f'href="{reverse("api-docs")}"')


class PublicApiV1Tests(SimpleTestCase):
    def test_root_exposes_only_service_level_contract_metadata(self):
        response = self.client.get("/api/v1/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")
        self.assertEqual(
            response.json(),
            {
                "service": "fast-and-pray",
                "version": "v1",
                "base_path": "/api/v1/",
                "status": "pre-release",
            },
        )

    def test_root_uses_an_isolated_public_api_namespace(self):
        match = resolve("/api/v1/")

        self.assertEqual(match.namespace, "public_api_v1")
        self.assertEqual(match.url_name, "root")
        self.assertEqual(reverse("public_api_v1:root"), "/api/v1/")
        with self.assertRaises(NoReverseMatch):
            reverse("public_api_v1:fast-list")

    def test_root_is_the_only_v1_route_until_resources_are_ready(self):
        self.assertEqual(
            [(pattern.name, str(pattern.pattern)) for pattern in public_api_urlpatterns],
            [("root", "")],
        )

    def test_unsupported_method_returns_api_appropriate_405(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/api/v1/")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET, HEAD, OPTIONS")
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")
        self.assertEqual(
            response.json(),
            {
                "code": "method_not_allowed",
                "message": 'Method "POST" not allowed.',
                "details": {},
            },
        )

    def test_root_remains_anonymous_with_stale_authorization(self):
        response = self.client.get(
            "/api/v1/", HTTP_AUTHORIZATION="Bearer definitely-not-a-token"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "fast-and-pray")

    def test_root_uses_json_for_html_accept(self):
        response = self.client.get("/api/v1/", HTTP_ACCEPT="text/html")

        self.assertEqual(response.status_code, 406)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")
        self.assertNotIn("text/html", response["Content-Type"])
        self.assertEqual(
            response.json(),
            {
                "code": "not_acceptable",
                "message": "Could not satisfy the request Accept header.",
                "details": {},
            },
        )

    def test_public_v1_does_not_expose_unready_or_internal_routes(self):
        for path in (
            "/api/v1/fasts/",
            "/api/v1/churches/",
            "/api/v1/readings/",
            "/api/v1/feasts/",
            "/api/v1/user/fasts/",
            "/api/v1/profile/",
            "/api/v1/s3-upload/upload-initialize/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_existing_legacy_routes_keep_their_reverse_values(self):
        self.assertEqual(reverse("fast_on_date"), "/api/user/fasts/")
        self.assertEqual(reverse("fast_on_date_without_user"), "/api/fast/")

    def test_docs_remain_coming_soon_without_v1_resource_links(self):
        response = self.client.get("/docs/")

        self.assertContains(response, "Coming soon")
        for path in (
            "/api/v1/churches/",
            "/api/v1/readings/",
            "/api/v1/fasts/",
            "/api/v1/feasts/",
        ):
            with self.subTest(path=path):
                self.assertNotContains(response, path)
