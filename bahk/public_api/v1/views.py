"""Views for the versioned public API."""

from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from bahk.public_api.v1.validation import PublicApiView


class PublicApiRootView(PublicApiView):
    """Return the stable service descriptor for the public v1 API."""

    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]
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
