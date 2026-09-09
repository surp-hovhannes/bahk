"""Atomic public-API admission control and bounded telemetry.

This Redis connection is deliberately independent of Django's response caches.
Production must use a bounded, noeviction Redis instance (see the runbook).
"""

import hashlib
import hmac
import ipaddress
import logging
import time
from functools import lru_cache

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry


logger = logging.getLogger("bahk.public_api")
PUBLIC_PREFIX = "/api/v1/"

# The server clock, atomic counter updates, and expiry are shared by all workers.
ADMIT = """
local now = tonumber(redis.call('TIME')[1])
local waits = 0
local keys = {}
local periods = {60, 3600}
for i, period in ipairs(periods) do
    keys[i] = KEYS[1] .. ':' .. period .. ':' .. math.floor(now / period)
    if tonumber(redis.call('GET', keys[i]) or '0') >= tonumber(ARGV[i]) then
        waits = math.max(waits, period - now % period)
    end
end
if waits > 0 then return waits end
for i, period in ipairs(periods) do
    redis.call('INCR', keys[i])
    redis.call('EXPIRE', keys[i], period - now % period + 1)
end
return 0
"""

RECORD = """
for i = 1, #ARGV, 2 do
    redis.call('HINCRBYFLOAT', KEYS[1], ARGV[i], ARGV[i+1])
end
redis.call('EXPIRE', KEYS[1], 86400)
"""


@lru_cache(maxsize=8)
def _connection(url):
    return Redis.from_url(
        url,
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
        max_connections=32,
        decode_responses=True,
        retry=Retry(NoBackoff(), 0),
    )


def connection():
    if not settings.PUBLIC_API_REDIS_URL:
        raise RedisError("Public API Redis is not configured")
    try:
        return _connection(settings.PUBLIC_API_REDIS_URL)
    except ValueError as exc:
        raise RedisError("Public API Redis configuration is invalid") from exc


def namespace():
    return settings.PUBLIC_API_REDIS_PREFIX


def is_public_request(request):
    return request.path_info == PUBLIC_PREFIX[:-1] or request.path_info.startswith(PUBLIC_PREFIX)


def client_identity(request):
    """Trust forwarded addresses only behind explicitly configured proxy networks."""
    address = ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
    networks = [ipaddress.ip_network(value) for value in settings.PUBLIC_API_TRUSTED_PROXIES]
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if networks and any(address in network for network in networks):
        parts = forwarded.split(",")
        if not forwarded or len(parts) > 16:
            raise ValueError("Missing or oversized proxy chain")
        # Walk from the verified socket peer; stop at the first untrusted hop.
        for part in reversed(parts):
            if not any(address in network for network in networks):
                break
            address = ipaddress.ip_address(part.strip())
    return hmac.new(settings.SECRET_KEY.encode(), address.compressed.encode(), hashlib.sha256).hexdigest()


def admit(request):
    identity = client_identity(request)
    return int(
        connection().eval(
            ADMIT,
            1,
            f"{namespace()}:limit:{identity}",
            settings.PUBLIC_API_RATE_MINUTE,
            settings.PUBLIC_API_RATE_HOUR,
        )
    )


def public_error(code, message, status, retry_after=None):
    details = {} if retry_after is None else {"retry_after": retry_after}
    response = JsonResponse({"code": code, "message": message, "details": details}, status=status)
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    response["Cache-Control"] = "no-store"
    return response


# Avoid turning a dependency outage into an unbounded stream of error events.
_last_failure_log = {}


def report_failure(kind):
    now = time.monotonic()
    if now - _last_failure_log.get(kind, float("-inf")) >= 60:
        _last_failure_log[kind] = now
        logger.error("public_api_dependency_failure kind=%s", kind)


ROUTES = frozenset(
    {
        "root",
        "church-list",
        "icon-list",
        "fast-list",
        "fast-detail",
        "fast-by-date",
        "fast-by-feast-date",
        "reading-by-date",
        "feast-by-date",
        "calendar",
    }
)
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
METRIC_TYPES = {
    "requests_total": ("counter", "Public HTTP requests by route and status."),
    "cache_total": ("counter", "Public response cache outcomes."),
    "duration_seconds": ("histogram", "Public HTTP application latency in seconds."),
    "blocked_work_total": ("counter", "Forbidden public work attempts prevented before dispatch."),
    "cache_entries": ("gauge", "Live public response cache entries and fill reservations."),
    "store_up": ("gauge", "Public Redis store is readable and writable."),
}


def record(request, response, elapsed):
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "url_name", None)
    route = route if route in ROUTES else "unresolved"
    status = str(response.status_code)
    cache = getattr(request, "public_cache_outcome", "none")
    if cache not in {"hit", "miss", "bypass", "busy", "none", "oversize", "full"}:
        cache = "none"
    fields = {
        f'requests_total{{route="{route}",status="{status}"}}': 1,
        f'cache_total{{outcome="{cache}"}}': 1,
        f'duration_seconds_count{{route="{route}"}}': 1,
        f'duration_seconds_sum{{route="{route}"}}': elapsed,
        f'duration_seconds_bucket{{route="{route}",le="+Inf"}}': 1,
    }
    for bucket in BUCKETS:
        fields[f'duration_seconds_bucket{{route="{route}",le="{bucket}"}}'] = int(elapsed <= bucket)
    for kind, count in getattr(request, "public_blocked_work", {}).items():
        if kind in {"llm", "passage", "task"}:
            fields[f'blocked_work_total{{kind="{kind}"}}'] = count
    try:
        connection().eval(RECORD, 1, f"{namespace()}:metrics", *[item for pair in fields.items() for item in pair])
    except RedisError:
        report_failure("telemetry")


class PublicApiTrafficMiddleware:
    """Admission precedes sessions, analytics, query work, and response caching."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_public_request(request):
            return self.get_response(request)
        started = time.monotonic()
        response = None
        if settings.PUBLIC_API_TRAFFIC_ENABLED:
            try:
                wait = admit(request)
                if wait:
                    response = public_error("throttled", "Request limit exceeded.", 429, wait)
            except ValueError:
                response = public_error("invalid_client_address", "Invalid client address.", 400)
            except RedisError:
                report_failure("limiter")
                response = public_error("service_unavailable", "Please retry shortly.", 503, 5)
        if response is None:
            from bahk.public_api.work import public_request

            token = public_request.set(request)
            try:
                response = self.get_response(request)
            finally:
                public_request.reset(token)
        if not response.get("Content-Type", "").startswith("application/json"):
            if response.status_code == 404:
                response = public_error("resource_not_found", "The requested resource does not exist.", 404)
            elif response.status_code >= 500:
                response = public_error("service_unavailable", "Please retry shortly.", 503, 5)
        response["Cache-Control"] = "no-store"
        if settings.PUBLIC_API_TRAFFIC_ENABLED:
            record(request, response, time.monotonic() - started)
        return response


def metrics_view(request):
    """Private Prometheus scrape endpoint; never part of the public inventory."""
    expected = settings.PUBLIC_API_METRICS_TOKEN
    supplied = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
        response = HttpResponse(status=404)
        response["Cache-Control"] = "no-store"
        return response
    if request.method != "GET":
        response = HttpResponse(status=405)
        response["Allow"] = "GET"
        response["Cache-Control"] = "no-store"
        return response
    try:
        client = connection()
        # A readable Redis can still reject limiter writes under noeviction/OOM.
        client.set(f"{namespace()}:write-health", "1", ex=60)
        values = client.hgetall(f"{namespace()}:metrics")
        for kind in ("llm", "passage", "task"):
            values.setdefault(f'blocked_work_total{{kind="{kind}"}}', 0)
        seconds, micros = client.time()
        now = seconds + micros / 1_000_000
        entries = client.zcount(f"{namespace()}:responses:index", f"({now}", "+inf")
        lines = ["public_api_store_up 1", f"public_api_cache_entries {entries}"]
        lines.extend(f"public_api_{key} {float(value)}" for key, value in sorted(values.items()))
    except RedisError:
        lines = ["public_api_store_up 0"]
    metadata = [
        line
        for name, (kind, description) in METRIC_TYPES.items()
        for line in (f"# HELP public_api_{name} {description}", f"# TYPE public_api_{name} {kind}")
    ]
    response = HttpResponse("\n".join(metadata + lines) + "\n", content_type="text/plain; version=0.0.4")
    response["Cache-Control"] = "no-store"
    return response
