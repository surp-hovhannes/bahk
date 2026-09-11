"""Shared query validation and error responses for public API v1 views."""

import re
from datetime import date, timedelta

import pytz
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, Throttled
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PublicApiError(APIException):
    """A stable, machine-readable public API error."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid_request"
    default_detail = "The request is invalid."

    def __init__(self, code=None, message=None, details=None, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        self.public_code = code or self.default_code
        self.public_message = message or self.default_detail
        self.public_details = details or {}
        super().__init__(detail=self.public_message, code=self.public_code)


def error_response(code, message, *, details=None, status_code=400):
    """Create the versioned public error envelope."""
    payload = {"code": code, "message": message, "details": details or {}}
    return Response(payload, status=status_code)


class PublicApiView(APIView):
    """Anonymous, JSON-only APIView with the public v1 error envelope."""

    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]

    def handle_exception(self, exc):
        if isinstance(exc, PublicApiError):
            response = error_response(
                exc.public_code,
                exc.public_message,
                details=exc.public_details,
                status_code=exc.status_code,
            )
            if "retry_after" in exc.public_details:
                response["Retry-After"] = str(exc.public_details["retry_after"])
            return response
        if isinstance(exc, APIException):
            details = {}
            if isinstance(exc, Throttled) and exc.wait is not None:
                details["retry_after"] = exc.wait
            response = error_response(
                exc.default_code,
                str(exc.detail),
                details=details,
                status_code=exc.status_code,
            )
            if details:
                response["Retry-After"] = str(details["retry_after"])
            return response
        return super().handle_exception(exc)


class PublicApiQuery:
    """Strict, reusable parsing for the v1 query parameters."""

    def __init__(self, query_params):
        self.query_params = query_params

    def date(self, name, *, required=False):
        value = self.query_params.get(name)
        if value is None:
            if required:
                raise PublicApiError(
                    "missing_parameter",
                    f"{name} is required.",
                    details={"parameter": name},
                )
            return None
        if not _DATE_RE.fullmatch(value):
            raise PublicApiError(
                "invalid_date",
                f"{name} must use ISO YYYY-MM-DD format.",
                details={"parameter": name, "value": value},
            )
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise PublicApiError(
                "invalid_date",
                f"{name} must be a valid calendar date.",
                details={"parameter": name, "value": value},
            )

    def date_range(self):
        start_date = self.date("start_date")
        end_date = self.date("end_date")
        if start_date and end_date and start_date > end_date:
            raise PublicApiError(
                "invalid_date_range",
                "start_date must not be after end_date.",
                details={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            )
        return start_date, end_date

    def effective_date_range(self):
        """Apply defaults before validating the work budget, including one-sided ranges."""
        if hasattr(self, "_effective_range"):
            return self._effective_range
        start_date, end_date = self.date_range()
        tz = self.timezone() or timezone.get_current_timezone()
        today = timezone.localdate(timezone=tz)
        try:
            start_date = start_date or today - timedelta(days=180)
            end_date = end_date or today + timedelta(days=180)
        except OverflowError:
            raise PublicApiError("invalid_date_range", "The effective date range is invalid.")
        maximum = settings.PUBLIC_API_MAX_RANGE_DAYS
        if start_date > end_date or (end_date - start_date).days + 1 > maximum:
            raise PublicApiError(
                "invalid_date_range",
                f"The effective range must cover 1 through {maximum} days.",
                details={"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "max_days": maximum},
            )
        self._effective_range = start_date, end_date
        return self._effective_range

    def language(self):
        value = self.query_params.get("lang")
        if value is None:
            return None
        supported = {code for code, _label in settings.LANGUAGES}
        if value not in supported:
            raise PublicApiError(
                "unsupported_language",
                "lang must be a supported language code.",
                details={"parameter": "lang", "value": value, "supported": sorted(supported)},
            )
        return value

    def timezone(self):
        value = self.query_params.get("tz")
        if value is None:
            return None
        try:
            return pytz.timezone(value)
        except pytz.UnknownTimeZoneError:
            raise PublicApiError(
                "invalid_timezone",
                "tz must be an IANA timezone name.",
                details={"parameter": "tz", "value": value},
            )

    def church_id(self, *, required=False):
        value = self.query_params.get("church_id")
        if value is None:
            if required:
                raise PublicApiError(
                    "missing_parameter",
                    "church_id is required.",
                    details={"parameter": "church_id"},
                )
            return None
        if len(value) > 19:
            raise PublicApiError(
                "invalid_church_id",
                "church_id must be a positive database integer.",
                details={"parameter": "church_id", "value": value},
            )
        try:
            church_id = int(value)
        except (TypeError, ValueError):
            church_id = 0
        if church_id <= 0 or church_id > 9223372036854775807 or str(church_id) != value:
            raise PublicApiError(
                "invalid_church_id",
                "church_id must be a positive integer.",
                details={"parameter": "church_id", "value": value},
            )
        return church_id

    def church(self, *, required=False):
        """Return the requested church or a stable public error."""
        church_id = self.church_id(required=required)
        if church_id is None:
            return None

        from hub.models import Church

        try:
            return Church.objects.get(pk=church_id)
        except Church.DoesNotExist:
            raise PublicApiError(
                "church_not_found",
                "No church exists for church_id.",
                details={"church_id": church_id},
                status_code=status.HTTP_404_NOT_FOUND,
            )


def resource_not_found(resource):
    """Return the stable public error for a valid, absent resource."""
    return PublicApiError(
        "resource_not_found",
        "The requested resource does not exist.",
        details={"resource": resource},
        status_code=status.HTTP_404_NOT_FOUND,
    )
