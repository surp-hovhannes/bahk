"""Best-effort ingestion. matched/kind are same-user ingest-time hints only.

Reports must join to notification_send on notif_id at report time and expose a
same-user flag; ingestion order can make these hints incomplete.
"""
from datetime import datetime, timedelta, timezone as datetime_timezone

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from events.event_schema import NOTIFICATION_KINDS
from events.models import Event, EventType


def _prompt_capped(user, data, now):
    key = f"notification:prompt:{user.pk}:{data['stage']}:{data['trigger']}:{now.astimezone(datetime_timezone.utc).date()}"
    try:
        if cache.add(key, 1, timeout=2 * 24 * 60 * 60):
            return False
        return cache.incr(key) > 10
    except Exception:
        # Volume control fails open; envelope/storage failures still skip writes.
        return False


def record_notification_telemetry(user, code, data, envelope):
    with transaction.atomic():
        if not EventType.objects.filter(code=code, is_active=True).exists():
            return 'skipped'
        now = timezone.now()
        recent = Event.objects.filter(user=user, timestamp__gte=now - timedelta(days=14))
        if code == EventType.NOTIFICATION_PROMPT:
            if _prompt_capped(user, data, now):
                return 'capped'
        else:
            if recent.filter(event_type__code=code, data__notif_id=data['notif_id']).exists():
                return 'duplicate'
            send = recent.filter(event_type__code=EventType.NOTIFICATION_SEND,
                                 data__notif_id=data['notif_id']).order_by('-timestamp', '-pk').first()
            data['matched'] = send is not None
            if code == EventType.NOTIFICATION_OPENED:
                kind = send.data.get('kind') if send else None
                data['kind'] = kind if isinstance(kind, str) and kind in NOTIFICATION_KINDS else 'unclassified'
        data.update(envelope())
        if 'occurred_at' in data and datetime.fromisoformat(data['occurred_at']) < now - timedelta(minutes=30):
            data['session_id'] = None
        # Never pass request: lifecycle rows contain no IP/UA.
        Event.create_event(event_type_code=code, user=user, data=data)
    return 'ok'
