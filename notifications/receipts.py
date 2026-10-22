"""Bounded, ephemeral Expo receipt ledger on the existing shared Redis cache."""
import json
import logging
import time
from copy import deepcopy
from datetime import timedelta
from functools import lru_cache
from threading import Lock

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.core.cache import CacheHandler
from django.db import transaction
from django.utils import timezone
from django_redis.pool import ConnectionFactory
from exponent_server_sdk import PushClient, PushTicket
from redis.exceptions import LockError
from redis.backoff import NoBackoff
from redis.retry import Retry

from .models import DeviceToken
from .utils import _device_token_log_id, _expo_error_code, _ticket_field, _valid_ticket_id

logger = logging.getLogger(__name__)
NAMESPACE = 'push-receipts:v1:'
# Expo keeps receipts for 24 hours; polling longer only spends the run budget.
TTL = 24 * 60 * 60
CAPACITY = 50000
BATCH_SIZE = 1000
MAX_BATCHES = 5
# Below the 15-minute beat interval: runs start after their tick, so a delay of
# exactly 15 minutes would land just after the next run and wait 30.
RETRY_DELAY = 14 * 60
PROVIDER_TIMEOUT = 20
LOCK_LEASE = 300
REDIS_TIMEOUT = 0.4
_warning_lock = Lock()
_next_capacity_warning = 0


class ReceiptConnectionFactory(ConnectionFactory):
    # django-redis keys pools by URL across cache handlers. Own the registry so
    # the shared app cache and receipt ledger cannot inherit each other's pool.
    _pools = {}


@lru_cache(maxsize=1)
def _receipt_cache():
    """Separate pool, same keys/serializer/TLS; never alter the app cache pool."""
    config = deepcopy(settings.CACHES['default'])
    if config['BACKEND'] == 'django_redis.cache.RedisCache':
        options = config.setdefault('OPTIONS', {})
        options.update(SOCKET_CONNECT_TIMEOUT=REDIS_TIMEOUT, SOCKET_TIMEOUT=REDIS_TIMEOUT,
                       CONNECTION_FACTORY='notifications.receipts.ReceiptConnectionFactory',
                       CONNECTION_POOL_CLASS='redis.ConnectionPool')
        # Nonblocking pool exhaustion and no implicit redis-py retries.
        options.pop('CONNECTION_POOL_CLASS_KWARGS', None)
        options.setdefault('CONNECTION_POOL_KWARGS', {}).update(
            socket_connect_timeout=REDIS_TIMEOUT, socket_timeout=REDIS_TIMEOUT,
            max_connections=8, retry=Retry(NoBackoff(), 0), retry_on_timeout=False,
        )
    return CacheHandler({'default': config})['default']


def get_redis_connection(alias='default'):
    return _receipt_cache().client.get_client(write=True)


def _warn_capacity():
    """At most one constant warning per minute per process; no extra Redis I/O."""
    global _next_capacity_warning
    with _warning_lock:
        now = time.monotonic()
        if now < _next_capacity_warning:
            return
        _next_capacity_warning = now + 60
    logger.warning('Push receipt ledger insert skipped')


def _now():
    return time.time()


def _key(suffix):
    return cache.make_key(NAMESPACE + suffix)


def _registration_key(device_pk):
    # Django cache API: it applies the cache prefix itself.
    return f'{NAMESPACE}registered:{device_pk}'


def enabled():
    """Config kill switch for ledger writes, collection and receipt cleanup."""
    return getattr(settings, 'PUSH_RECEIPTS_ENABLED', True)


# Atomic capacity check and insertion; duplicate tickets never extend retention.
ENQUEUE = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then return 0 end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""


def ledger_entry(ticket, device_pk, telemetry):
    """Map an accepted ticket; telemetry only adds correlation, cleanup runs without it."""
    ticket_id = _ticket_field(ticket, 'id')
    if not enabled() or _ticket_field(ticket, 'status') != 'ok' or not _valid_ticket_id(ticket_id):
        return None
    sent_at = _now()
    return ticket_id, {
        'device_pk': device_pk, 'sent_at': sent_at, 'expires_at': sent_at + TTL,
        'notif_id': telemetry['notif_id'] if telemetry else None,
        'kind': telemetry['kind'] if telemetry else 'unclassified',
    }


def remember_tickets(entries):
    """Store a send's mappings in one pipelined round trip per 500; never raises."""
    if not entries:
        return
    try:
        redis = get_redis_connection('default')
        enqueue = redis.register_script(ENQUEUE)
        skipped = 0
        for start in range(0, len(entries), 500):
            pipe = redis.pipeline(transaction=False)
            for ticket_id, row in entries[start:start + 500]:
                # Receipts are seldom ready at once; the first poll waits one interval.
                enqueue(keys=[_key('pending'), _key(ticket_id)],
                        args=[json.dumps(row), TTL, row['sent_at'] + RETRY_DELAY, CAPACITY, ticket_id],
                        client=pipe)
            skipped += sum(1 for stored in pipe.execute() if not stored)
        if skipped:
            _warn_capacity()
    except Exception as exc:
        logger.warning('Push receipt ledger unavailable (%s)', type(exc).__name__)


def note_registration(device_pk):
    """A token registered again after a send outranks that send's late DNR receipt.

    Recorded even with the kill switch off: mappings from before it was turned off
    are still collected once it is back on.
    """
    try:
        _receipt_cache().set(_registration_key(device_pk), _now(), TTL)
    except Exception as exc:
        logger.warning('Push receipt registration marker unavailable (%s)', type(exc).__name__)


def _parse(raw):
    try:
        row = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if (isinstance(row, dict) and isinstance(row.get('device_pk'), int)
            and all(isinstance(row.get(key), (int, float)) for key in ('sent_at', 'expires_at'))):
        return row
    return None


def _deactivate_dead_device(row):
    registered_at = _receipt_cache().get(_registration_key(row['device_pk']))
    if registered_at is not None and registered_at >= row['sent_at']:
        # Re-registration since the send (#593 reactivates the row) wins over this receipt.
        return
    active = DeviceToken.objects.filter(pk=row['device_pk'], is_active=True)
    token = active.values_list('token', flat=True).first()
    if token is not None and active.update(is_active=False):
        logger.warning('Device not registered: %s', _device_token_log_id(token))


def _record_receipt(row, ticket_id, status, code):
    from events.event_schema import EVENT_SCHEMA_VERSION
    from events.models import Event, EventType

    with transaction.atomic():
        # Serialize durable deduplication, including retries after Redis deletion fails.
        event_type = EventType.objects.select_for_update().filter(
            code=EventType.NOTIFICATION_RECEIPT, is_active=True,
        ).first()
        # A mapping is processed only within TTL of its send, so any earlier row for
        # it is that recent; the bound keeps this on the (event_type, -timestamp) index.
        if event_type is None or Event.objects.filter(
            event_type=event_type, timestamp__gte=timezone.now() - timedelta(seconds=TTL),
            data__notif_id=row['notif_id'], data__ticket_id=ticket_id,
        ).exists():
            return
        data = {
            'source': 'server_domain', 'schema_version': EVENT_SCHEMA_VERSION,
            'notif_id': row['notif_id'], 'ticket_id': ticket_id, 'kind': row['kind'],
            'receipt_status': status,
            'outcome': 'delivered' if status == 'ok' else 'provider_error',
        }
        if status == 'error':
            data['error_code'] = code
        Event.objects.create(
            event_type=event_type,
            title=EventType.NOTIFICATION_LIFECYCLE_TITLES[EventType.NOTIFICATION_RECEIPT],
            data=data, ip_address=None, user_agent='',
        )


def _apply_receipt(row, ticket_id, receipt):
    status = _ticket_field(receipt, 'status')
    if status not in {'ok', 'error'}:
        return False
    code = _expo_error_code(_ticket_field(receipt, 'details')) if status == 'error' else None
    if code == 'DeviceNotRegistered':
        _deactivate_dead_device(row)
    if getattr(settings, 'NOTIFICATION_TELEMETRY_ENABLED', False) and row['notif_id']:
        _record_receipt(row, ticket_id, status, code)
    return True


def _collect_batch(redis, client):
    """Process one due batch; False once nothing is due."""
    now = _now()
    ids = redis.zrangebyscore(_key('pending'), '-inf', now, start=0, num=BATCH_SIZE)
    if not ids:
        return False
    ids = [value.decode() if isinstance(value, bytes) else value for value in ids]
    # Rotate unavailable receipts so later tickets also receive bounded service.
    redis.zadd(_key('pending'), dict.fromkeys(ids, now + RETRY_DELAY))
    rows, done, unreadable = {}, [], 0
    for ticket_id, raw in zip(ids, redis.mget([_key(value) for value in ids]), strict=True):
        row = _parse(raw)
        if row is None or row['expires_at'] <= now:
            # Missing, unreadable or expired mappings can never be processed.
            unreadable += raw is not None and row is None
            done.append(ticket_id)
        else:
            rows[ticket_id] = row
    if unreadable:
        logger.warning('Push receipt mappings unreadable: %d', unreadable)
    receipts = {}
    if rows:
        tickets = [
            PushTicket(push_message=None, status='ok', message=None, details=None, id=ticket_id)
            for ticket_id in rows
        ]
        try:
            receipts = {receipt.id: receipt for receipt in client.check_receipts_multiple(tickets)}
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.warning('Push receipt provider unavailable (%s)', type(exc).__name__)
    for ticket_id, receipt in receipts.items():
        if ticket_id not in rows:
            continue
        try:
            if _apply_receipt(rows[ticket_id], ticket_id, receipt):
                done.append(ticket_id)
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            logger.warning('Push receipt processing skipped (%s)', type(exc).__name__)
    if done:
        pipe = redis.pipeline(transaction=False)
        pipe.delete(*[_key(ticket_id) for ticket_id in done])
        pipe.zrem(_key('pending'), *done)
        pipe.execute()
    return True


def collect_receipts():
    """At most MAX_BATCHES x BATCH_SIZE tickets/run; unresolved mappings retry until expiry.

    Raises only for collector-wide failures (cache outage, time limit) so the task's
    cron monitor reports them; per-ticket and provider failures are retried instead.
    """
    if not enabled():
        return
    try:
        redis = get_redis_connection('default')
        # The hard task limit is shorter than the lease. Never block another worker.
        lock = redis.lock(_key('lock'), timeout=LOCK_LEASE, blocking_timeout=0)
        if not lock.acquire():
            logger.info('Push receipt collection already running')
            return
        try:
            client = PushClient(timeout=PROVIDER_TIMEOUT)
            for _ in range(MAX_BATCHES):
                if not lock.owned() or not _collect_batch(redis, client):
                    break
        finally:
            try:
                lock.release()
            except LockError:
                pass  # Lease already expired; another run may own it now.
    except SoftTimeLimitExceeded:
        logger.warning('Push receipt collection hit its time limit')
        raise
    except Exception as exc:
        # Constant text and the type only: provider and cache errors may echo payloads.
        logger.warning('Push receipt collection failed (%s)', type(exc).__name__)
        raise RuntimeError(f'Push receipt collection failed ({type(exc).__name__})') from None
