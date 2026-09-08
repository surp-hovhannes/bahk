"""URL configuration for the versioned public API."""

from django.urls import include, path


urlpatterns = [
    path(
        "v1/",
        include(("bahk.public_api.v1.urls", "public_api_v1"), namespace="public_api_v1"),
    ),
]
