"""Shared fixed-window email reservations on the configured Django cache.

Redis executes each transition atomically, following the Lua admission pattern in
bahk.public_api. LocMem is only a single-process fallback; all its transitions
share a bounded thread lock. Unsupported backends and cache errors fail closed.
"""

import logging
import threading
import time
import uuid

from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django_redis.cache import RedisCache

logger = logging.getLogger(__name__)
COUNT_KEY = 'email_count'
TOKENS_KEY = 'email_count:reservations'
_local_lock = threading.Lock()

# The hash is the window generation: its unique token fields disappear with the
# counter. Clearing it on counter creation also handles an independently lost key.
# Raw integers are compatible with django-redis's default integer serialization.
_TRANSITION = """
local count = redis.call('GET', KEYS[1])
if ARGV[1] == 'release' then
    if not count then return 0 end
    if redis.call('HDEL', KEYS[2], ARGV[2]) == 0 then return 0 end
    if tonumber(count) > 0 then return redis.call('DECR', KEYS[1]) end
    return 0
end
if not count then
    redis.call('DEL', KEYS[2])
    redis.call('SET', KEYS[1], 0, 'PX', ARGV[4])
    count = '0'
end
if ARGV[1] == 'reserve' and tonumber(count) >= tonumber(ARGV[3]) then
    return 0
end
local result = redis.call('INCR', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
    ttl = tonumber(ARGV[4])
    redis.call('PEXPIRE', KEYS[1], ttl)
end
redis.call('HSET', KEYS[2], ARGV[2], 1)
redis.call('PEXPIRE', KEYS[2], ttl)
return result
"""


def _transition(operation, token):
    backend = caches['default']
    if isinstance(backend, RedisCache):
        client = backend.client.get_client(write=True)
        return client.eval(
            _TRANSITION,
            2,
            backend.make_key(COUNT_KEY),
            backend.make_key(TOKENS_KEY),
            operation,
            token,
            settings.EMAIL_RATE_LIMIT,
            max(1, int(settings.EMAIL_RATE_LIMIT_WINDOW * 1000)),
        )
    if not isinstance(backend, LocMemCache):
        raise RuntimeError('Email quota requires django-redis or LocMemCache')
    if not _local_lock.acquire(timeout=1):
        raise RuntimeError('Email quota lock unavailable')
    try:
        count = backend.get(COUNT_KEY)
        tokens = backend.get(TOKENS_KEY, set())
        if operation == 'release':
            if count is None or token not in tokens:
                return 0
            tokens.remove(token)
            count = max(0, count - 1)
        else:
            if count is None:
                count, tokens = 0, set()
            if operation == 'reserve' and count >= settings.EMAIL_RATE_LIMIT:
                return 0
            count += 1
            tokens.add(token)
        # LocMem exposes no TTL API. Preserve its absolute counter expiry instead
        # of starting another full window on every reservation or release.
        deadline = backend._expire_info.get(backend.make_key(COUNT_KEY))
        if deadline is None:
            deadline = time.time() + settings.EMAIL_RATE_LIMIT_WINDOW
        remaining = deadline - time.time()
        if remaining <= 0:
            # Expired during the transition: never resurrect or deliver on it.
            return 0
        backend.set(COUNT_KEY, count, timeout=remaining)
        backend.set(TOKENS_KEY, tokens, timeout=remaining)
        # set() computes expiry relative to its own call time. Pin both keys to
        # the original deadline so transitions never extend the quota window.
        with backend._lock:
            backend._expire_info[backend.make_key(COUNT_KEY)] = deadline
            backend._expire_info[backend.make_key(TOKENS_KEY)] = deadline
        return count
    finally:
        _local_lock.release()


def get_email_count():
    return caches['default'].get(COUNT_KEY, 0)


def increment_email_count():
    """Legacy counter utility; senders must reserve before provider delivery."""
    return _transition('increment', uuid.uuid4().hex)


def reserve_email_quota():
    """Return a unique window-bound token, or None when admission is denied."""
    token = uuid.uuid4().hex
    try:
        return token if _transition('reserve', token) else None
    except Exception:
        logger.exception('Email quota unavailable; deferring delivery')
        return None


def release_email_quota(token):
    """Release exactly once, and only from the window that issued this token."""
    try:
        _transition('release', token)
    except Exception:
        logger.exception('Email quota release failed; capacity remains consumed')
