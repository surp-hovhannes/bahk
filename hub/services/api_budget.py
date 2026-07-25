"""Per-period spend budgets for metered third-party APIs.

Backed by ``django.core.cache`` so a counter is shared across web dynos and Celery
workers (Redis in production, LocMemCache under ``tests.test_settings``).  Counters are
namespaced by *local* calendar day or month and expire on their own; nothing resets them.

Used to keep API.Bible calls inside the plan quota.  See ``hub/services/reading_text_service.py``
for the call sites and ``bahk/settings.py`` (API.BIBLE SETTINGS) for the limits.
"""

import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

DAY = "day"
MONTH = "month"

# Each counter must outlive its own period so a late call on the last day/hour still
# lands on the same key rather than silently starting a fresh budget.
PERIOD_TTL_SECONDS = {
    DAY: 36 * 60 * 60,       # 1.5 days
    MONTH: 35 * 24 * 60 * 60,  # 35 days
}


class APIBudget:
    """Best-effort cap on how many calls may be made to an API per calendar period.

    Not atomic: ``consume`` reads then increments, so under concurrency the counter can
    overshoot ``limit`` by at most (concurrent callers - 1), and a token may be burned
    without a call being made when the increment lands over the limit.  Both errors are
    bounded and in the safe direction.  An atomic version would need a Redis Lua script,
    which would not work under LocMemCache in tests.

    Fails *closed*: if the cache is unreachable, ``consume`` returns False.  Reopening the
    spend hole during a Redis outage is worse than briefly withholding expired text.
    """

    def __init__(self, name: str, limit: int, *, period: str = DAY):
        if period not in PERIOD_TTL_SECONDS:
            raise ValueError(f"Unknown budget period {period!r}; expected one of {list(PERIOD_TTL_SECONDS)}.")
        self.name = name
        self.limit = int(limit)
        self.period = period
        self.ttl = PERIOD_TTL_SECONDS[period]

    def key(self, today=None) -> str:
        """Cache key for the period containing *today* (defaults to the current local date).

        Computed on each call rather than cached on the instance so a long-lived budget
        object (or a request straddling midnight) rolls over to the next period correctly.
        """
        today = today or timezone.localdate()
        stamp = today.strftime("%Y-%m") if self.period == MONTH else today.isoformat()
        return f"api_budget:{self.name}:{self.period}:{stamp}"

    def used(self) -> int:
        """Calls consumed in the current period, or ``limit`` if the cache is unreachable."""
        try:
            return int(cache.get(self.key()) or 0)
        except Exception:
            logger.warning("Could not read %s budget counter; assuming exhausted.", self.name, exc_info=True)
            return self.limit

    def remaining(self) -> int:
        return max(self.limit - self.used(), 0)

    def consume(self, amount: int = 1) -> bool:
        """Reserve *amount* calls.  Returns False when the budget is exhausted."""
        if self.limit <= 0:
            return False
        key = self.key()
        try:
            if int(cache.get(key) or 0) >= self.limit:
                return False
            total = self._increment(key, amount)
        except Exception:
            logger.warning("%s budget cache unavailable; refusing spend.", self.name, exc_info=True)
            return False
        return total <= self.limit

    def _increment(self, key: str, amount: int) -> int:
        """Increment *key*, creating it with a TTL if absent.

        ``cache.incr`` raises ValueError on a missing key and preserves the existing TTL
        on both django-redis and LocMemCache, so create-then-increment gives a counter
        that expires on schedule.
        """
        try:
            return cache.incr(key, amount)
        except ValueError:
            pass
        if cache.add(key, amount, timeout=self.ttl):
            return amount
        # Another caller created the key between our incr and add.
        try:
            return cache.incr(key, amount)
        except ValueError:
            # Expired again in that window; treat it as a fresh period.
            cache.set(key, amount, timeout=self.ttl)
            return amount
