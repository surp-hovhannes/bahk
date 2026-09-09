"""Stable pagination for public v1 collection endpoints."""

from rest_framework.pagination import LimitOffsetPagination


class PublicApiPagination(LimitOffsetPagination):
    """Use one documented envelope for every public collection."""

    default_limit = 25
    max_limit = 100

