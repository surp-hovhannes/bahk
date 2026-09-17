"""Project-wide request/response middleware."""

import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)


class SlowRequestLoggingMiddleware:
    """Log a WARNING for any request slower than ``SLOW_REQUEST_THRESHOLD_SECONDS``.

    Times the full downstream stack (every later middleware, the view, the response
    rendering), so a request that a gateway or client times out on — the 504s of
    issue #506 — can be attributed to an endpoint from the logs alone: the record
    names the method, full path (query string included), status, and duration.
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
                request.get_full_path(),
                response.status_code,
                duration,
                threshold,
            )
        return response
