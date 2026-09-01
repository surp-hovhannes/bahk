"""Views for the versioned public API.

The v1 root is intentionally limited to service-level contract metadata until
individual resources satisfy their serializer, validation, and traffic-control
readiness gates.
"""

from django.http import JsonResponse
from django.views import View


class PublicApiRootView(View):
    """Return the stable service descriptor for the public v1 API."""

    http_method_names = ["get", "head", "options"]

    def get(self, request, *args, **kwargs):
        return JsonResponse(
            {
                "service": "fast-and-pray",
                "version": "v1",
                "base_url": "/api/v1/",
                "contract": "/docs/public-api-contract.md",
                "status": "planned",
            }
        )
