from django.conf import settings
from django.views.generic import TemplateView


class LandingPageView(TemplateView):
    """Public gateway for the Fast & Pray API service."""

    template_name = "api_landing.html"


class ApiDocsView(TemplateView):
    """Public reference, published only with resource registration."""

    template_name = "api_docs.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["public_api_published"] = settings.PUBLIC_API_RESOURCES_ENABLED
        if context["public_api_published"]:
            from bahk.public_api.reference import reference_context

            context.update(reference_context())
        return context
