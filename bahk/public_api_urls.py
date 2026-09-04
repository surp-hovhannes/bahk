"""URL configuration for the public versioned API.

Do not include internal URLconfs here. Public resource routes are added only
when their serializer, validation, traffic controls, and contract tests are
ready.
"""

from django.urls import path

from bahk.public_api_views import PublicApiRootView


app_name = "public_api"

urlpatterns = [
    path("", PublicApiRootView.as_view(), name="root"),
]
