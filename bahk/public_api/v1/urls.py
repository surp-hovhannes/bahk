"""URL configuration for public API version 1."""

from django.urls import path

from bahk.public_api.v1.resource_views import (
    ChurchListView,
    FastByDateView,
    FastByFeastDateView,
    FastDetailView,
    FastListView,
    FeastByDateView,
    IconListView,
    ReadingByDateView,
)
from bahk.public_api.v1.views import PublicApiRootView


app_name = "public_api_v1"

urlpatterns = [
    path("", PublicApiRootView.as_view(), name="root"),
    path("churches/", ChurchListView.as_view(), name="church-list"),
    path("icons/", IconListView.as_view(), name="icon-list"),
    path("fasts/", FastListView.as_view(), name="fast-list"),
    path("fasts/by-date/", FastByDateView.as_view(), name="fast-by-date"),
    path("fasts/by-feast-date/", FastByFeastDateView.as_view(), name="fast-by-feast-date"),
    path("fasts/<int:pk>/", FastDetailView.as_view(), name="fast-detail"),
    path("readings/", ReadingByDateView.as_view(), name="reading-by-date"),
    path("feasts/", FeastByDateView.as_view(), name="feast-by-date"),
]
