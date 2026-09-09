"""Isolated URLconf exercising the production registration gate when enabled."""

from runpy import run_path

from django.test import override_settings
from django.urls import include, path

from bahk.public_api.v1 import urls

with override_settings(PUBLIC_API_RESOURCES_ENABLED=True):
    enabled = run_path(urls.__file__)

urlpatterns = [
    path("api/v1/", include((enabled["urlpatterns"], "public_api_v1"))),
]
