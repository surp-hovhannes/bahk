"""The rule for collapsing duplicate ``Feast`` rows onto one commemoration.

Lives here, free of model imports, so the two callers can share one implementation:

  * ``hub/management/commands/audit_feast_duplicates.py``, which reports what a merge would do
    against real data before it runs;
  * the re-key data migration, which runs against *historical* models and so cannot import
    ``hub.models`` at all.

That sharing is the point. A dry run is only worth running if it predicts what actually happens,
and a rule written twice is a rule that will eventually be written differently twice.

Every function here takes plain model instances and reads only attributes both the real and the
historical model expose, so it works either side of the migration.
"""
import datetime


def survivor(feasts):
    """Pick the row a group of same-named feasts collapses onto, and describe the merge.

      * the surviving *context* is the newest active one across the group, falling back to the
        newest of any state -- an inactive context was deliberately retired, so it should not win
        on recency alone;
      * thumbs are summed across every context in the group, so feedback is never dropped;
      * icon and designation are the first non-null in ``id`` order, which is the oldest row --
        the one an admin is most likely to have curated by hand.

    Returns a dict describing the merge. Nothing is written; the caller applies it (or reports it).
    """
    feasts = sorted(feasts, key=lambda f: f.id)
    contexts = [ctx for feast in feasts for ctx in feast.contexts.all()]

    active = [c for c in contexts if c.active]
    pool = active or contexts
    keeper = max(pool, key=_context_recency, default=None)

    icons = [f.icon_id for f in feasts if f.icon_id]
    designations = [f.designation for f in feasts if f.designation]

    return {
        "keep": feasts[0],
        "absorbed": feasts[1:],
        "context_kept": keeper,
        "contexts_deactivated": [c for c in contexts if keeper and c.id != keeper.id],
        "thumbs_up": sum(c.thumbs_up for c in contexts),
        "thumbs_down": sum(c.thumbs_down for c in contexts),
        "icon_id": icons[0] if icons else None,
        "icon_conflict": len(set(icons)) > 1,
        "designation": designations[0] if designations else None,
        "designation_conflict": len(set(designations)) > 1,
    }


def _context_recency(context):
    """Sort key for "newest context", tolerant of the nullable timestamp.

    ``time_of_generation`` gained ``auto_now_add`` after the model shipped, so rows written before
    that are NULL. Rank those below every timestamped row rather than letting None compare against
    a datetime, and break ties on ``id``. The leading flag also keeps the naive ``datetime.min``
    placeholder from ever being compared against a timezone-aware value.
    """
    return (
        context.time_of_generation is not None,
        context.time_of_generation or datetime.datetime.min,
        context.id,
    )
