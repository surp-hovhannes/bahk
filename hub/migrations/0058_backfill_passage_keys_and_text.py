"""Backfill Reading.passage_key and seed PassageText from text already in the table.

Both steps are grouped by passage rather than done per row.  The lectionary resolves to
~1,124 distinct passages no matter how many Reading rows exist, so this migration's cost
is bounded by the corpus, not by table size.

Seeding matters as much as the backfill: without it the first refresh run after deploy
would re-retrieve text the database already holds, spending most of a month's API.Bible
quota to arrive back where it started.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    # hub.constants.passage_key is a pure function over module-level dicts -- no models, no
    # settings -- so importing it here cannot desync from historical model state the way
    # importing a service would.  Inlining a snapshot of BOOK_NAME_TO_USFM instead would
    # rot the moment a book name is added, leaving the migration disagreeing with runtime
    # about what a key is.
    from hub.constants import passage_key

    Reading = apps.get_model("hub", "Reading")
    PassageText = apps.get_model("hub", "PassageText")

    # --- 1. Reading.passage_key, one UPDATE per distinct citation ---
    # order_by() clears Reading.Meta.ordering, which Django would otherwise append to the
    # SELECT DISTINCT and thereby group per row instead of per citation.
    citations = (
        Reading.objects.filter(passage_key="")
        .order_by()
        .values_list("book", "start_chapter", "start_verse", "end_chapter", "end_verse")
        .distinct()
    )
    for book, start_ch, start_v, end_ch, end_v in list(citations):
        key = passage_key(book, start_ch, start_v, end_ch, end_v)
        if not key:
            continue  # no USFM mapping; stays "" and is excluded from retrieval
        Reading.objects.filter(
            book=book,
            start_chapter=start_ch,
            start_verse=start_v,
            end_chapter=end_ch,
            end_verse=end_v,
            passage_key="",
        ).update(passage_key=key)

    # --- 2. Seed PassageText with the freshest text already stored per passage ---
    # `text` and `i18n` are the concrete columns; modeltrans exposes text_en/text_hy as
    # virtual fields over them, and historical models do not carry that machinery.
    best_en: dict[str, tuple] = {}
    best_hy: dict[str, tuple] = {}

    rows = (
        Reading.objects.exclude(passage_key="")
        .values_list(
            "passage_key",
            "text", "text_copyright", "text_version", "text_fetched_at", "fums_token",
            "i18n", "text_hy_version", "text_hy_copyright", "text_hy_fetched_at",
        )
        .iterator(chunk_size=500)
    )
    for (
        key,
        text, copyright_, version, fetched_at, fums_token,
        i18n, hy_version, hy_copyright, hy_fetched_at,
    ) in rows:
        # English is only servable with a timestamp -- that is what the freshness cap is
        # measured against -- so untimestamped text is not worth carrying over.
        if text and fetched_at is not None:
            current = best_en.get(key)
            if current is None or fetched_at > current[3]:
                best_en[key] = (text, copyright_, version or "", fetched_at, fums_token or "")

        text_hy = (i18n or {}).get("text_hy")
        if text_hy:
            current = best_hy.get(key)
            # Armenian has no freshness cap, so a missing timestamp is not disqualifying;
            # it only affects which row wins when several carry the same passage.
            if current is None or (
                hy_fetched_at is not None
                and (current[3] is None or hy_fetched_at > current[3])
            ):
                best_hy[key] = (text_hy, hy_copyright or "", hy_version or "", hy_fetched_at, "")

    for language, best in (("en", best_en), ("hy", best_hy)):
        PassageText.objects.bulk_create(
            [
                PassageText(
                    passage_key=key,
                    language=language,
                    text=text,
                    copyright=copyright_,
                    version=version,
                    fetched_at=fetched_at,
                    fums_token=fums_token,
                )
                for key, (text, copyright_, version, fetched_at, fums_token) in best.items()
            ],
            batch_size=500,
            ignore_conflicts=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("hub", "0057_passage_text_and_reading_passage_key"),
    ]

    operations = [
        # Forward only: reversing would mean deleting text that is expensive to re-retrieve,
        # and the columns it was copied from are still in place, so there is nothing to undo.
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
