"""Enabled resource routes and monitoring for isolated traffic tests."""

from django.urls import include, path, re_path

from bahk.public_api.traffic import metrics_view
from bahk.public_api.v1.urls import resource_urlpatterns
from bahk.public_api.v1.views import PublicApiRootView, public_api_not_found

urlpatterns = [
    path(
        "api/v1/",
        include(
            (
                [
                    path("", PublicApiRootView.as_view(), name="root"),
                    *resource_urlpatterns,
                    re_path(r"^", public_api_not_found, name="not-found"),
                ],
                "public_api_v1",
            )
        ),
    ),
    path("internal/public-api-metrics/", metrics_view),
]
