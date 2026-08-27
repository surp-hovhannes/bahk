from django.test import SimpleTestCase
from django.urls import resolve, reverse

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
