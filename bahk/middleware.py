"""Project-wide request/response middleware."""

import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)


class SlowRequestLoggingMiddleware:
    """Log a WARNING for any request slower than ``SLOW_REQUEST_THRESHOLD_SECONDS``.

    Times the full downstream stack (every later middleware, the view, the response
    rendering). Records method, path without query secrets, status and duration.
    This measures completed responses only: a terminated Gunicorn worker cannot
    emit a completion log. Worker/proxy timeout logs and transaction-duration
    monitoring are required to diagnose gateway 504s (issue #506).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        duration = time.monotonic() - started
        threshold = getattr(settings, "SLOW_REQUEST_THRESHOLD_SECONDS", 5.0)
        if duration >= threshold:
            logger.warning(
                "Slow request: %s %s -> %s in %.2fs (threshold %.1fs)",
                request.method,
                request.path,
                response.status_code,
                duration,
                threshold,
            )
        return response
