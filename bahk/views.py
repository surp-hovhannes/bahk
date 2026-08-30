from django.views.generic import TemplateView


class LandingPageView(TemplateView):
    """Public gateway for the Fast & Pray API service."""

    template_name = "api_landing.html"


class ApiDocsView(TemplateView):
    """Public gateway for the forthcoming API reference."""

    template_name = "api_docs.html"
