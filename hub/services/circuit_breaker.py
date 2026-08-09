"""Per-source circuit breaker so a persistent failure cannot drain a shared spend budget.

``APIBudget`` (see ``hub/services/api_budget.py``) caps *total* spend; it says nothing
about whether calls are succeeding.  Without this, a source that is rejecting every
call (bad credentials, a missing entitlement, an outage) still burns one budget slot
per attempt, and a small shared daily budget can be exhausted entirely on doomed
retries before an unrelated, healthy source gets a turn.  This stops attempts to a
source once it has failed ``threshold`` times in a row, until ``cooldown_seconds`` has
passed -- so a rejected source stops spending budget quickly, and self-heals once
whatever was wrong is fixed, without needing a manual reset.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class ConsecutiveFailureBreaker:
    """Opens after *threshold* consecutive failures for one *name*; auto-closes after
    *cooldown_seconds*.

    Backed by ``django.core.cache`` (Redis in production), so state is shared across
    web dynos and Celery workers -- unlike a local loop variable, this catches failures
    that accumulate across separate requests, which is exactly the case a shared daily
    budget needs protecting from.

    Fails *open* (permits the attempt) if the cache is unreachable.  This differs from
    ``APIBudget``, which fails closed: a budget's job is to guarantee a ceiling is never
    exceeded, so it must refuse spend it cannot verify.  A breaker's job is only to stop
    wasting spend on a source known to be failing; if the cache itself is down, calls
    already can't be budgeted (``APIBudget.consume`` also fails closed on that same
    outage), so failing open here adds no new exposure.
    """

    def __init__(self, name: str, *, threshold: int, cooldown_seconds: int):
        self.name = name
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds

    def _failures_key(self) -> str:
        return f"circuit_breaker:{self.name}:failures"

    def _open_key(self) -> str:
        return f"circuit_breaker:{self.name}:open"

    def is_open(self) -> bool:
        """True when further attempts should be skipped."""
        try:
            return bool(cache.get(self._open_key()))
        except Exception:
            logger.warning(
                "Could not read %s circuit breaker state; permitting the attempt.",
                self.name, exc_info=True,
            )
            return False

    def record_failure(self) -> None:
        """Count one failure; open the breaker once *threshold* is reached in a row."""
        try:
            try:
                count = cache.incr(self._failures_key())
            except ValueError:
                # Key missing or expired: this is the first failure of a new streak.
                cache.set(self._failures_key(), 1, timeout=self.cooldown_seconds)
                count = 1
        except Exception:
            logger.warning(
                "Could not update %s circuit breaker failure count.",
                self.name, exc_info=True,
            )
            return

        if count >= self.threshold:
            try:
                cache.set(self._open_key(), True, timeout=self.cooldown_seconds)
            except Exception:
                logger.warning(
                    "Could not open %s circuit breaker after %d consecutive failures.",
                    self.name, count, exc_info=True,
                )
                return
            logger.error(
                "Circuit breaker for %s opened after %d consecutive failures; skipping "
                "further attempts for %ds.",
                self.name, count, self.cooldown_seconds,
            )

    def record_success(self) -> None:
        """Reset the failure streak and close the breaker."""
        try:
            cache.delete(self._failures_key())
            cache.delete(self._open_key())
        except Exception:
            logger.warning(
                "Could not reset %s circuit breaker state after a success.",
                self.name, exc_info=True,
            )
