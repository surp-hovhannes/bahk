"""Bounded public data cache. Never stores request headers or pagination URLs."""

import hashlib
import json
import uuid
from contextvars import copy_context
from functools import wraps
from threading import Event, Thread

from django.conf import settings
from django.utils.translation import get_language
from redis.exceptions import RedisError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from bahk.public_api.traffic import connection, namespace, report_failure
from bahk.public_api.v1.validation import PublicApiError


# Store payloads and in-flight reservations under one bounded admission index.
# Expired payloads and leases expire themselves; index metadata is pruned on fill.
RESERVE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local data = redis.call('GET', KEYS[2])
if data then return {'hit', data} end
if redis.call('EXISTS', KEYS[3]) == 1 then return {'busy', ''} end
if not redis.call('ZSCORE', KEYS[1], KEYS[2]) and
   redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return {'full', ''} end
redis.call('SET', KEYS[3], ARGV[2], 'EX', ARGV[3])
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), KEYS[2])
redis.call('EXPIRE', KEYS[1], math.max(tonumber(ARGV[3]), tonumber(ARGV[4])) + 1)
return {'miss', ''}
"""

FINISH = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 0 end
if ARGV[2] ~= '' then
    local clock = redis.call('TIME')
    local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
    redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
    redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), KEYS[2])
    redis.call('EXPIRE', KEYS[1], math.max(tonumber(ARGV[3]) + 1, redis.call('TTL', KEYS[1])))
else
    redis.call('ZREM', KEYS[1], KEYS[2])
end
redis.call('DEL', KEYS[3])
return 1
"""


RENEW = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 0 end
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
redis.call('EXPIRE', KEYS[3], ARGV[2])
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), KEYS[2])
redis.call('EXPIRE', KEYS[1], math.max(tonumber(ARGV[2]) + 1, redis.call('TTL', KEYS[1])))
return 1
"""


class FillHeartbeat:
    """Keep ownership through rendering; stop and join before owner-checked finish."""

    def __init__(self, client, keys, token):
        self.client, self.keys, self.token = client, keys, token
        self.lease = settings.PUBLIC_API_CACHE_LEASE_SECONDS
        self.interval = settings.PUBLIC_API_CACHE_HEARTBEAT_SECONDS
        self.stopped = Event()
        self.lost = Event()
        self.thread = Thread(target=copy_context().run, args=(self.run,), daemon=True)

    def run(self):
        while not self.stopped.wait(self.interval):
            try:
                if self.client.eval(RENEW, 3, *self.keys, self.token, self.lease):
                    continue
            except RedisError:
                pass
            self.lost.set()
            report_failure("response_cache")
            return

    def stop(self):
        self.stopped.set()
        # Redis connections have bounded socket timeouts and no retries.
        if self.thread.ident is not None:
            self.thread.join()


def canonical_parameters(view, request):
    query = view.public_query()
    values = dict(view.kwargs)
    if view.language_parameter:
        values["lang"] = query.language() or get_language() or "en"
    for name in view.public_parameters:
        if name == "church_id":
            values[name] = query.church_id(required=view.church_required)
        elif name == "date":
            values[name] = query.date("date", required=True).isoformat()
        elif name == "range":
            values["start_date"], values["end_date"] = [item.isoformat() for item in query.effective_date_range()]
    paginator = getattr(view, "paginator", None)
    if paginator is not None:
        values["limit"] = paginator.get_limit(request)
        values["offset"] = paginator.get_offset(request)
    return values


def cached_public_get(handler):
    @wraps(handler)
    def wrapped(view, request, *args, **kwargs):
        parameters = canonical_parameters(view, request)
        if not settings.PUBLIC_API_RESPONSE_CACHE_ENABLED:
            return handler(view, request, *args, **kwargs)
        raw = json.dumps([view.__class__.__name__, parameters], sort_keys=True)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        prefix = f"{namespace()}:responses"
        # Bump the version when cached public data changes shape.
        key = f"{prefix}:v2:{digest}"
        keys = [f"{prefix}:index", key, f"{key}:lease"]
        token = uuid.uuid4().hex
        try:
            client = connection()
            outcome, payload = client.eval(
                RESERVE,
                3,
                *keys,
                settings.PUBLIC_API_CACHE_MAX_ENTRIES,
                token,
                settings.PUBLIC_API_CACHE_LEASE_SECONDS,
                settings.PUBLIC_API_CACHE_TTL,
            )
        except RedisError:
            report_failure("response_cache")
            request._request.public_cache_outcome = "bypass"
            return handler(view, request, *args, **kwargs)
        request._request.public_cache_outcome = outcome
        if outcome == "hit":
            data = json.loads(payload)
            paginator = getattr(view, "paginator", None)
            if paginator is not None:
                paginator.request = request
                paginator.count = data["count"]
                paginator.limit = parameters["limit"]
                paginator.offset = parameters["offset"]
                return paginator.get_paginated_response(data["results"])
            return Response(data)
        if outcome == "busy":
            raise PublicApiError(
                "service_unavailable",
                "This response is being prepared. Please retry shortly.",
                details={"retry_after": 1},
                status_code=503,
            )
        if outcome == "full":
            # Admission and query bounds still protect uncached reads.
            return handler(view, request, *args, **kwargs)
        encoded = ""
        heartbeat = FillHeartbeat(client, keys, token)
        try:
            heartbeat.thread.start()
            response = handler(view, request, *args, **kwargs)
            if response.status_code == 200:
                data = response.data
                if getattr(view, "paginator", None) is not None:
                    data = {"count": data["count"], "results": data["results"]}
                rendered = JSONRenderer().render(data)
                if len(rendered) <= settings.PUBLIC_API_CACHE_MAX_BYTES:
                    encoded = rendered.decode()
                else:
                    request._request.public_cache_outcome = "oversize"
            return response
        finally:
            heartbeat.stop()
            try:
                client.eval(
                    FINISH,
                    3,
                    *keys,
                    token,
                    "" if heartbeat.lost.is_set() else encoded,
                    settings.PUBLIC_API_CACHE_TTL,
                )
            except RedisError:
                report_failure("response_cache")

    return wrapped
