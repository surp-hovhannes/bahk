"""Celery tasks for keeping Scripture text fresh.

Provides:
    - fetch_reading_text_task: fetch text for the passage a single Reading cites.
      Useful for management commands or ad-hoc backfills.
    - fetch_missing_passage_texts_task: fetch text the readings view served blank.
      The view no longer calls API.Bible in the request cycle (issue #506); it hands
      its missing-passage set to this task instead.
    - prewarm_next_day_readings_task: scheduled task that creates tomorrow's
      Day/Reading rows and warms their passage text before the first request.
    - refresh_all_reading_texts_task: scheduled task that re-retrieves passages whose text
      has passed READING_TEXT_REFRESH_DAYS, to stay inside API.Bible's 30-day cap.

Text is stored per passage, not per reading (see hub.models.PassageText), so the unit of
work here is a passage key.  The lectionary resolves to ~1,124 distinct passages across all
years, against a reading table that grows ~4 rows a day forever — so one run covers the
entire corpus, and that number does not grow as the table does.
"""

import logging
import time

from celery import shared_task
from django.conf import settings

from hub.services.reading_text_service import (
    TEXT_FETCHERS,
    bible_api_budgets,
    fetch_passage_text,
    languages_needing_fetch,
    load_passage_texts,
    missing_passage_texts,
    prepare_shared_resources,
    stale_passage_text_queryset,
)

logger = logging.getLogger(__name__)

# Languages whose retrieval costs quota, and so must respect the circuit breaker, the spend
# ceiling, and the inter-call delay.  Everything else composes from a local corpus.
METERED_LANGUAGES = ("en",)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='hub.tasks.fetch_reading_text_task')
def fetch_reading_text_task(self, reading_id: int):
    """Fetch text for the passage cited by a single Reading.

    NOTE: the readings view no longer fetches text in the request cycle (issue #506);
    it enqueues fetch_missing_passage_texts_task instead.  This task remains available
    for management commands and ad-hoc backfills.
    """
    from hub.models import Reading
    from hub.services.reading_text_service import fetch_all_reading_texts

    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    if not reading.passage_key:
        logger.warning(
            "Reading %s (%s) has no passage key; add its book to BOOK_NAME_TO_USFM.",
            reading_id, reading.passage_reference,
        )
        return

    # Charge the monthly ceiling only, as the refresh task does.  The daily budget is
    # scoped to the public on-demand path; a backfill run must not eat the allowance that
    # keeps user-facing requests served.
    shared = prepare_shared_resources()
    shared["budgets"] = bible_api_budgets(include_daily=False)

    results = fetch_all_reading_texts(reading, **shared)
    failed = [lang for lang, ok in results.items() if not ok]
    if failed:
        logger.warning(
            "Could not fetch %s text for Reading %s (%s).",
            ", ".join(failed), reading_id, reading.passage_reference,
        )


@shared_task(name='hub.tasks.fetch_missing_passage_texts_task')
def fetch_missing_passage_texts_task(items):
    """Fetch and store text for passages the readings view served blank.

    The view stopped calling API.Bible in the request cycle (issue #506): a burst of
    missing passages used to serialize 10-second-timeout calls inside the request and
    push it past the gateway's limit.  It now returns the partial response (blank text
    fields -- the long-standing contract for unfetched text) and hands the missing set
    to this task.

    Args:
        items: the view's missing set, JSON-shaped as a list of
            ``{"key": passage_key, "citation": [book, sch, sv, ech, ev],
            "langs": [...]}`` dicts -- one per passage, at most a day's worth.

    Idempotent: missing state is re-derived at execution time (``languages_needing_fetch``
    against the current store), so duplicate enqueues from client retries, races with the
    weekly refresh, and re-served dates only ever fetch what is still missing.

    Budgets: charges the same daily + monthly envelopes the view path always did.  The
    daily cap keeps user-triggered fetches bounded, and a zero daily budget (the test
    suite's guard) keeps eager in-line runs from reaching API.Bible.
    """
    if not items:
        return

    texts = load_passage_texts({item.get("key") for item in items})
    pending = []
    for item in items:
        key = item.get("key")
        if not key:
            continue
        still_missing = [
            lang for lang in item.get("langs", ())
            if lang in set(languages_needing_fetch(key, texts))
        ]
        if still_missing:
            pending.append((key, tuple(item["citation"]), still_missing))

    if not pending:
        logger.info("Text already stored for all %d enqueued passages; nothing to fetch.", len(items))
        return

    shared = prepare_shared_resources()
    fetched = failed = 0
    pending_count = len(pending)
    for i, (key, citation, langs) in enumerate(pending):
        results = fetch_passage_text(key, citation, langs=langs, **shared)
        fetched += sum(1 for ok in results.values() if ok)
        failed += sum(1 for ok in results.values() if not ok)
        # Sleep only after a fetch that actually reached API.Bible, and not after
        # the last item (nothing to space out).  An exhausted budget or an
        # unmappable book returns before any HTTP call -- no need to wait.
        if i < pending_count - 1 and any(
            lang in METERED_LANGUAGES and results.get(lang) for lang in langs
        ):
            time.sleep(0.5)

    logger.info(
        "Follow-up text fetch complete: %d language(s) stored, %d failed, of %d passages enqueued.",
        fetched, failed, len(items),
    )


@shared_task(name='hub.tasks.prewarm_next_day_readings_task')
def prewarm_next_day_readings_task():
    """Create tomorrow's Day/Reading rows and warm their passage text.

    Scheduled daily ahead of the day rolling over, so the first request for the date
    finds readings and text already stored instead of paying for lectionary computation
    and API.Bible retrieval in the request cycle (issue #506).

    For every church: get-or-create the Day, import the lectionary readings if absent,
    then fetch still-missing passage text.  Armenian composes locally; English charges
    the monthly ceiling only, like the other background runs -- the daily budget stays
    reserved for the public on-demand path.  Re-running is a no-op: readings persist via
    get-or-create and only missing or expired text is fetched.
    """
    from datetime import timedelta

    from django.utils import timezone

    from hub.models import Church, Day
    from hub.services.lectionary_service import get_daily_readings, persist_readings

    target_date = timezone.localdate() + timedelta(days=1)
    readings_created = 0
    passages_warmed = 0

    for church in Church.objects.iterator():
        try:
            day, _ = Day.objects.get_or_create(date=target_date, church=church)
            if not day.readings.exists():
                readings_created += len(persist_readings(day, get_daily_readings(target_date, church)))
            passages_warmed += _warm_day_passage_texts(day)
        except Exception:
            logger.exception("Prewarm failed for church %s on %s.", church.name, target_date)

    logger.info(
        "Prewarm for %s: %d reading(s) imported, %d language fetch(es) stored.",
        target_date, readings_created, passages_warmed,
    )


def _warm_day_passage_texts(day) -> int:
    """Fetch still-missing passage text for *day*'s readings.  Returns fetches stored.

    Shared with the per-church loop above; passage-keyed, so churches whose days cite
    the same passages warm them once.  Charges the monthly ceiling only, matching the
    refresh task: background runs must not be able to starve the public daily budget.
    """
    readings = list(day.readings.all())
    if not readings:
        return 0

    passage_texts = load_passage_texts({r.passage_key for r in readings if r.passage_key})
    missing = missing_passage_texts(readings, passage_texts)
    if not missing:
        return 0

    shared = prepare_shared_resources()
    shared["budgets"] = bible_api_budgets(include_daily=False)

    stored = 0
    items = list(missing.items())
    for i, (key, (citation, langs)) in enumerate(items):
        lang_list = sorted(langs)
        results = fetch_passage_text(key, citation, langs=lang_list, **shared)
        stored += sum(1 for ok in results.values() if ok)
        # Same rule as the follow-up task: sleep only after an API.Bible fetch,
        # not after the last item, and not when the budget refused.
        if i < len(items) - 1 and any(
            lang in METERED_LANGUAGES and results.get(lang) for lang in lang_list
        ):
            time.sleep(0.5)
    return stored


def _citations_by_key(keys) -> dict[str, tuple]:
    """One representative citation per passage key.

    Every reading sharing a key cites the same passage, so any of them serves as the
    argument to the fetchers.  Taking one per key is what collapses thousands of rows into
    ~1,124 retrievals.
    """
    from hub.models import Reading

    citations: dict[str, tuple] = {}
    rows = (
        Reading.objects.filter(passage_key__in=keys)
        .values_list("passage_key", "book", "start_chapter", "start_verse",
                     "end_chapter", "end_verse")
        .iterator(chunk_size=1000)
    )
    for key, *citation in rows:
        citations.setdefault(key, tuple(citation))
    return citations


def _repair_missing_passage_keys() -> int:
    """Fill in ``passage_key`` for rows that have none but whose book now resolves.

    Rows with an empty key are excluded from retrieval, so without this a fix to
    ``BOOK_NAME_TO_USFM`` would be inert — the readings it enables would stay excluded
    forever.  Grouped by citation, so the cost is the number of distinct unmappable
    citations, not the number of rows.
    """
    from hub.constants import passage_key
    from hub.models import Reading

    repaired = 0
    citations = (
        Reading.objects.filter(passage_key="")
        .order_by()  # clear Reading.Meta.ordering; it would defeat the DISTINCT
        .values_list("book", "start_chapter", "start_verse", "end_chapter", "end_verse")
        .distinct()
    )
    for book, start_ch, start_v, end_ch, end_v in list(citations):
        key = passage_key(book, start_ch, start_v, end_ch, end_v)
        if not key:
            continue
        repaired += Reading.objects.filter(
            book=book, start_chapter=start_ch, start_verse=start_v,
            end_chapter=end_ch, end_verse=end_v, passage_key="",
        ).update(passage_key=key)
    if repaired:
        logger.info("Repaired passage_key on %d readings whose book now resolves.", repaired)
    return repaired


def _report_unmappable() -> None:
    """Log book names that still have no USFM mapping, so the fix is discoverable."""
    from hub.models import Reading

    books = list(
        Reading.objects.filter(passage_key="")
        .order_by()  # clear Reading.Meta.ordering; it would defeat the DISTINCT
        .values_list("book", flat=True)
        .distinct()[:20]
    )
    if books:
        logger.warning(
            "%d distinct book name(s) have no USFM mapping and are never retrieved: %s. "
            "Add them to BOOK_NAME_TO_USFM in hub/constants.py.",
            len(books), ", ".join(repr(b) for b in books),
        )


@shared_task(bind=True, max_retries=1, default_retry_delay=300, name='hub.tasks.refresh_all_reading_texts_task')
def refresh_all_reading_texts_task(self):
    """Re-retrieve passages whose text has gone stale, in every registered language.

    Scheduled weekly via Celery Beat.  Steps:
        1. Repair readings whose book has become mappable since they were created.
        2. Per language, select stale passages among those actually read and re-retrieve
           at most READING_REFRESH_LIMIT of them.
        3. Log the dedup ratio and an error summary.

    Metered languages (API.Bible) additionally respect the monthly spend ceiling and abort
    after READING_REFRESH_MAX_CONSECUTIVE_FAILURES consecutive failures — a failed fetch
    stores no timestamp, so without the breaker a rejecting API would be retried against a
    wall for the whole run.  Locally composed languages have no quota and skip all of that.
    """
    from hub.models import PassageText, Reading

    limit = getattr(settings, "READING_REFRESH_LIMIT", 1500)
    max_consecutive_failures = getattr(settings, "READING_REFRESH_MAX_CONSECUTIVE_FAILURES", 10)

    _repair_missing_passage_keys()

    keys_in_use = set(
        Reading.objects.exclude(passage_key="")
        .order_by()  # clear Reading.Meta.ordering; it would defeat the DISTINCT
        .values_list("passage_key", flat=True)
        .distinct()
    )
    if not keys_in_use:
        logger.info("No readings with a resolvable passage. Nothing to refresh.")
        _report_unmappable()
        return

    total_readings = Reading.objects.count()
    # The dedup ratio is the only signal that passage keying still works.  If it drifts
    # toward 1.0, retrieval cost has quietly gone back to scaling with the table.
    logger.info(
        "Refresh scope: %d readings resolve to %d distinct passages (%.1fx dedup).",
        total_readings, len(keys_in_use), total_readings / len(keys_in_use),
    )

    shared = prepare_shared_resources()
    # The task charges only the monthly ceiling; the daily budget is scoped to the public
    # on-demand path, which a background run must not be able to starve.
    budgets = bible_api_budgets(include_daily=False)
    shared["budgets"] = budgets
    # Counts calls that actually reached API.Bible, which is not the number of passages
    # processed: an unmappable book or a refused budget returns before any HTTP request.
    # Without the distinction, a circuit-breaker trip logs "0 API calls" and reads like a
    # run that never tried.
    stats = {"attempted": 0}
    shared["stats"] = stats
    monthly_budget = next((b for b in budgets if b.period == "month"), None)

    for language in TEXT_FETCHERS:
        metered = language in METERED_LANGUAGES

        if metered and monthly_budget is not None and monthly_budget.remaining() <= 0:
            # Called out separately from an API rejection: the cause is our own ceiling,
            # and the operator's response is different.
            logger.error(
                "Skipping %s refresh: the API.Bible monthly ceiling (%d) is already spent. "
                "Nothing will be retrieved until the counter rolls over.",
                language, monthly_budget.limit,
            )
            continue

        stale = set(
            stale_passage_text_queryset(language)
            .filter(passage_key__in=keys_in_use)
            .values_list("passage_key", flat=True)
        )
        # Passages never retrieved in this language have no row at all, so they cannot show
        # up in a queryset over PassageText.
        known = set(
            PassageText.objects.filter(language=language, passage_key__in=keys_in_use)
            .values_list("passage_key", flat=True)
        )
        stale_keys = sorted(stale | (keys_in_use - known))

        if not stale_keys:
            logger.info("No stale %s passages. Nothing to refresh.", language)
            continue

        selected = stale_keys[:limit]
        citations = _citations_by_key(selected)

        logger.info(
            "Refreshing %d of %d stale %s passages%s.",
            len(selected), len(stale_keys), language,
            f" (limit {limit})" if len(stale_keys) > limit else "",
        )

        retrieved = 0
        consecutive_failures = 0
        failures = []
        aborted = False
        attempts_before = stats["attempted"]

        for key in selected:
            citation = citations.get(key)
            if citation is None:
                continue  # every reading citing it was deleted mid-run

            ok = fetch_passage_text(key, citation, langs=[language], **shared).get(language)
            if ok:
                retrieved += 1
                consecutive_failures = 0
            else:
                failures.append((key, citation))
                if metered:
                    consecutive_failures += 1

            if metered and consecutive_failures >= max_consecutive_failures:
                aborted = True
                logger.error(
                    "Aborting %s refresh after %d consecutive failures (%d of %d passages "
                    "processed, %d API calls made). API.Bible is likely rejecting calls "
                    "(quota or credentials); continuing would spend the remaining ceiling "
                    "without storing any text.",
                    language, consecutive_failures, retrieved + len(failures), len(selected),
                    stats["attempted"] - attempts_before,
                )
                break

            if metered:
                time.sleep(0.5)  # spacing between API calls to avoid rate limiting

        logger.info(
            "%s refresh %s: %d passages retrieved, %d failed (of %d selected), "
            "%d API call(s) made.",
            language, "aborted" if aborted else "complete",
            retrieved, len(failures), len(selected), stats["attempted"] - attempts_before,
        )

        if failures:
            report = "\n".join(
                f"  - {key} ({c[0]} {c[1]}:{c[2]}-{c[3]}:{c[4]})" for key, c in failures
            )
            logger.error("%s refresh failures (%d total):\n%s", language, len(failures), report)

    _report_unmappable()
