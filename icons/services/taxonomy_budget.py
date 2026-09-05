"""Atomic cumulative wire reservations, including unknown/timeout usage."""

import json
import math

from django.conf import settings
from django.db import transaction
from django.db.models import F

from icons.models import TaxonomyBudget, TaxonomyCall


class BudgetExhausted(Exception):
    pass


def estimate_reservation(payload, *, image=False):
    # UTF-8 bytes bound text tokenization conservatively. Fixed overhead includes
    # instructions/schema; 65,536 image tokens bounds our <=1536px derivative.
    tokens = len(json.dumps(payload, ensure_ascii=False).encode()) + 8192 + (65536 if image else 0)
    # Operator supplies a conservative MAX(input, output) USD / million tokens.
    rate = getattr(settings, "ICON_TAXONOMY_MAX_USD_PER_MILLION_TOKENS", 0)
    if rate <= 0 or not math.isfinite(rate):
        raise BudgetExhausted("pricing_not_configured")
    return tokens, math.ceil(tokens * rate)


@transaction.atomic
def reserve(stage, payload, *, image=False, analysis=None, budget_name=None):
    tokens, microdollars = estimate_reservation(payload, image=image)
    name = budget_name or getattr(settings, "ICON_TAXONOMY_BUDGET", "")
    # A conditional UPDATE, rather than read-then-write, also protects SQLite.
    updated = TaxonomyBudget.objects.filter(
        name=name,
        enabled=True,
        calls__lt=F("max_calls"),
        tokens__lte=F("max_tokens") - tokens,
        microdollars__lte=F("max_microdollars") - microdollars,
    ).update(calls=F("calls") + 1, tokens=F("tokens") + tokens, microdollars=F("microdollars") + microdollars)
    if not updated:
        raise BudgetExhausted("budget_exhausted_or_unconfigured")
    return TaxonomyCall.objects.create(
        budget=TaxonomyBudget.objects.get(name=name),
        analysis=analysis,
        stage=stage,
        reserved_tokens=tokens,
        reserved_microdollars=microdollars,
    )
