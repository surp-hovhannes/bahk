"""Views for the versioned public API."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.response import Response

from bahk.public_api.v1.validation import PublicApiView


class PublicApiRootView(PublicApiView):
    """Return the stable service descriptor for the public v1 API."""

    http_method_names = ["get", "head", "options"]

    def get(self, request, *args, **kwargs):
        return Response(
            {
                "service": "fast-and-pray",
                "version": "v1",
                "base_path": "/api/v1/",
                "status": "pre-release",
            }
        )


@csrf_exempt
def public_api_not_found(request):
    """Return a JSON 404 for unmatched v1 paths, regardless of method or Accept."""
    return JsonResponse(
        {"code": "not_found", "message": "The requested route does not exist.", "details": {}},
        status=404,
    )
