"""URL configuration for public API version 1."""

from django.urls import path, re_path

from bahk.public_api.v1.views import PublicApiRootView, public_api_not_found


app_name = "public_api_v1"

urlpatterns = [
    path("", PublicApiRootView.as_view(), name="root"),
    # Keep last so future resource routes take precedence, including nested paths.
    re_path(r"^", public_api_not_found, name="not-found"),
]
