"""Stable pagination for public v1 collection endpoints."""

from rest_framework.pagination import LimitOffsetPagination

from bahk.public_api.v1.validation import PublicApiError


class PublicApiPagination(LimitOffsetPagination):
    """Use one documented envelope for every public collection."""

    default_limit = 25
    max_limit = 100

    def _integer_parameter(self, request, parameter, *, minimum, maximum=None):
        value = request.query_params.get(parameter)
        if not value or not value.isascii() or not value.isdecimal():
            raise PublicApiError(
                "invalid_pagination",
                f"{parameter} must be a whole number.",
                details={"parameter": parameter, "value": value},
            )
        integer = int(value)
        if integer < minimum or (maximum is not None and integer > maximum):
            range_description = (
                f"from {minimum} through {maximum}"
                if maximum is not None
                else f"at least {minimum}"
            )
            raise PublicApiError(
                "invalid_pagination",
                f"{parameter} must be {range_description}.",
                details={"parameter": parameter, "value": value},
            )
        return integer

    def get_limit(self, request):
        if self.limit_query_param not in request.query_params:
            return self.default_limit
        return self._integer_parameter(
            request,
            self.limit_query_param,
            minimum=1,
            maximum=self.max_limit,
        )

    def get_offset(self, request):
        if self.offset_query_param not in request.query_params:
            return 0
        return self._integer_parameter(
            request,
            self.offset_query_param,
            minimum=0,
        )

