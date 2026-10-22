"""Bounded, ephemeral Expo receipt ledger on the existing shared Redis cache."""
import json
import logging
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django_redis import get_redis_connection
from exponent_server_sdk import PushClient

from .models import DeviceToken

logger = logging.getLogger(__name__)
TTL = 36 * 60 * 60
CAPACITY = 50000
BATCH_SIZE = 1000
MAX_BATCHES = 5


def _key(suffix):
    return cache.make_key('push-receipts:v1:' + suffix)


# Atomic capacity check and insertion; duplicate tickets never extend retention.
ENQUEUE = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then return 0 end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[5])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""


def remember_ticket(ticket, device_pk, telemetry):
    """Keep cleanup enabled without telemetry; only correlated sends yield events."""
    from .utils import _ticket_field

    ticket_id = _ticket_field(ticket, 'id')
    if (_ticket_field(ticket, 'status') != 'ok' or not isinstance(ticket_id, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', ticket_id)):
        return
    try:
        now = time.time()
        row = {'device_pk': device_pk, 'expires_at': now + TTL,
               'notif_id': telemetry['notif_id'] if telemetry else None,
               'kind': telemetry['kind'] if telemetry else 'unclassified'}
        stored = get_redis_connection('default').eval(
            ENQUEUE, 2, _key('pending'), _key(ticket_id),
            json.dumps(row), TTL, now, CAPACITY, ticket_id,
        )
        if not stored:
            logger.warning('Push receipt ledger insert skipped')
    except Exception:
        logger.warning('Push receipt ledger unavailable')


def _apply_receipt(row, ticket_id, receipt):
    from .utils import EXPO_ERROR_CODES, _device_token_log_id, _ticket_field

    status = _ticket_field(receipt, 'status')
    if status not in {'ok', 'error'}:
        return False
    details = _ticket_field(receipt, 'details')
    code = details.get('error') if isinstance(details, dict) else None
    if status == 'error' and code == 'DeviceNotRegistered':
        device = DeviceToken.objects.filter(pk=row['device_pk']).first()
        if device is not None:
            DeviceToken.objects.filter(pk=device.pk).update(is_active=False)
            logger.warning('Device not registered: %s', _device_token_log_id(device.token))
    with transaction.atomic():
        if getattr(settings, 'NOTIFICATION_TELEMETRY_ENABLED', False) and row['notif_id']:
            from events.event_schema import EVENT_SCHEMA_VERSION
            from events.models import Event, EventType

            # Serialize durable deduplication, including retries after Redis deletion fails.
            event_type = EventType.objects.select_for_update().filter(
                code=EventType.NOTIFICATION_RECEIPT, is_active=True,
            ).first()
            if event_type is not None and not Event.objects.filter(
                event_type=event_type, data__notif_id=row['notif_id'], data__ticket_id=ticket_id,
            ).exists():
                data = {
                    'source': 'server_domain', 'schema_version': EVENT_SCHEMA_VERSION,
                    'notif_id': row['notif_id'], 'ticket_id': ticket_id, 'kind': row['kind'],
                    'receipt_status': status,
                    'outcome': 'delivered' if status == 'ok' else 'provider_error',
                }
                if status == 'error':
                    data['error_code'] = code if isinstance(code, str) and code in EXPO_ERROR_CODES else 'other'
                Event.objects.create(
                    event_type=event_type,
                    title=EventType.NOTIFICATION_LIFECYCLE_TITLES[EventType.NOTIFICATION_RECEIPT],
                    data=data, ip_address=None, user_agent='',
                )
    return True


def collect_receipts():
    """At most 5,000 tickets/run; failures retain mappings until their fixed TTL."""
    try:
        redis = get_redis_connection('default')
        # Hard task limit is shorter than the lease. Never block another worker.
        with redis.lock(_key('lock'), timeout=300, blocking_timeout=0) as lock:
            client = PushClient()
            for _ in range(MAX_BATCHES):
                now = time.time()
                ids = redis.zrangebyscore(_key('pending'), '-inf', now, start=0, num=BATCH_SIZE)
                if not ids:
                    break
                ids = [value.decode() if isinstance(value, bytes) else value for value in ids]
                # Rotate unavailable receipts so later tickets also receive bounded service.
                redis.zadd(_key('pending'), dict.fromkeys(ids, now + 900))
                rows = {}
                for ticket_id, raw in zip(ids, redis.mget([_key(value) for value in ids]), strict=True):
                    if raw is None:
                        redis.zrem(_key('pending'), ticket_id)
                        continue
                    row = json.loads(raw)
                    if row['expires_at'] <= now:
                        redis.delete(_key(ticket_id))
                        redis.zrem(_key('pending'), ticket_id)
                        continue
                    rows[ticket_id] = row
                if not rows:
                    continue
                try:
                    receipts = client.check_receipts_multiple(list(rows))
                except Exception:
                    logger.warning('Push receipt provider unavailable')
                    continue
                for ticket_id, row in rows.items():
                    try:
                        if not lock.owned():
                            return
                        if row['expires_at'] <= time.time():
                            continue
                        receipt = receipts.get(ticket_id)
                        if receipt is not None and _apply_receipt(row, ticket_id, receipt):
                            redis.delete(_key(ticket_id))
                            redis.zrem(_key('pending'), ticket_id)
                    except Exception:
                        logger.warning('Push receipt processing skipped')
    except Exception:
        # Includes lock contention, cache outages, malformed responses. Never log provider text.
        logger.warning('Push receipt collection skipped')
