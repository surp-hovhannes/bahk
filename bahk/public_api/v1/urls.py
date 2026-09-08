"""URL configuration for public API version 1."""

from django.urls import path

from bahk.public_api.v1.views import PublicApiRootView


app_name = "public_api_v1"

urlpatterns = [
    path("", PublicApiRootView.as_view(), name="root"),
]
