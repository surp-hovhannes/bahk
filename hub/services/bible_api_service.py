"""Service for fetching Bible verse text from API.Bible.

Uses NKJV for canonical books (OT + NT). For Apocrypha / deuterocanonical
books (Tobit, Sirach, Wisdom, Maccabees, etc.), automatically falls back
to the KJV with Apocrypha, American Edition (KJVAIC).

Prerequisites:
    Set the BIBLE_API_KEY environment variable or Django setting.
"""

import logging

import requests
from decouple import config
from django.utils import timezone

from hub.constants import APOCRYPHA_USFM_IDS, BOOK_NAME_TO_USFM

logger = logging.getLogger(__name__)

BASE_URL = "https://rest.api.bible/v1"
NKJV_BIBLE_ID = "63097d2a0a2f7db3-01"
KJVAIC_BIBLE_ID = "a6aee10bb058511c-01"  # KJV with Apocrypha, American Edition

# The Armenian liturgical tradition numbers the closing doxology of Romans as part
# of chapter 14 (verses 24-26). NKJV -- like most Western Bibles -- ends chapter 14
# at verse 23 and places the same doxology at 16:25-27. Citations that reach past
# 14:23 are split into two segments (main text + doxology) and stitched back
# together; see resolve_reading_segments / get_composite_passage.
ROMANS_CH14_LAST_VERSE = 23
ROMANS_DOXOLOGY_CHAPTER = 16
ROMANS_DOXOLOGY_START_VERSE = 25


class BibleAPIService:
    """Client for extracting verses from API.Bible.

    Uses NKJV for canonical books (OT + NT). For Apocrypha / deuterocanonical
    books, automatically falls back to KJVAIC.
    """

    def __init__(self, api_key: str | None = None):
        """Initialize with API key from argument, Django settings, or env var."""
        self.api_key = api_key or config("BIBLE_API_KEY", default="")
        if not self.api_key:
            raise ValueError(
                "API key required. Set BIBLE_API_KEY in environment or Django settings."
            )
        self.timeout = config("BIBLE_API_TIMEOUT_SECONDS", default=10, cast=float)
        self.session = requests.Session()
        self.session.headers.update({"api-key": self.api_key})

    def get_passage(
        self,
        usfm_book_id: str,
        start_chapter: int,
        start_verse: int,
        end_chapter: int,
        end_verse: int,
    ) -> dict:
        """Fetch a passage (range of verses) from API.Bible.

        Args:
            usfm_book_id: 3-letter USFM book abbreviation (e.g. "GEN", "JHN", "TOB").
            start_chapter: Starting chapter number.
            start_verse: Starting verse number.
            end_chapter: Ending chapter number.
            end_verse: Ending verse number.

        Returns:
            Dict with keys:
                "reference"  - The human-readable reference (e.g. "Genesis 1:1-5")
                "content"    - The verse text
                "copyright"  - Copyright statement
                "version"    - "NKJV" or "KJVAIC" depending on which was used
                "fums_token" - FUMS v3 token for fair-use tracking

        Raises:
            requests.HTTPError: On API errors (401, 403, 404, etc.)
        """
        bible_id, version = self._bible_id_for_book(usfm_book_id)
        passage_id = self._build_passage_id(
            usfm_book_id, start_chapter, start_verse, end_chapter, end_verse
        )

        params = {
            "content-type": "text",
            "include-verse-numbers": "true",
            "include-titles": "false",
            "include-notes": "false",
            "fums-version": "3",
        }

        url = f"{BASE_URL}/bibles/{bible_id}/passages/{passage_id}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        json_data = resp.json()

        data = json_data["data"]
        fums_token = json_data.get("meta", {}).get("fumsToken", "")
        return {
            "reference": data.get("reference", passage_id),
            "content": data.get("content", ""),
            "copyright": data.get("copyright", ""),
            "version": version,
            "fums_token": fums_token,
        }

    def get_composite_passage(
        self, segments: list[tuple[str, int, int, int, int]]
    ) -> dict:
        """Fetch one or more passage segments and stitch them into a single result.

        The common case is a single segment, returned unchanged from ``get_passage``.
        Multiple segments (e.g. the Romans doxology split produced by
        ``resolve_reading_segments``) are concatenated in citation order. The first
        segment's FUMS token, version, and copyright are used for the combined
        result -- API.Bible issues one token per call, and the primary segment is
        the bulk of what a viewer reads.

        Args:
            segments: One or more ``(usfm_book_id, start_chapter, start_verse,
                       end_chapter, end_verse)`` tuples, in reading order.

        Returns:
            Same shape as ``get_passage``: "reference", "content", "copyright",
            "version", "fums_token".
        """
        results = [self.get_passage(*segment) for segment in segments]
        if len(results) == 1:
            return results[0]

        return {
            "reference": " / ".join(r["reference"] for r in results),
            "content": " ".join(r["content"] for r in results),
            "copyright": results[0]["copyright"],
            "version": results[0]["version"],
            "fums_token": results[0]["fums_token"],
        }

    @staticmethod
    def resolve_book_name(book_name: str) -> str:
        """Resolve a book name (as stored in Reading.book) to its 3-letter USFM ID.

        Args:
            book_name: Raw book name (may contain curly/smart quotes from scrapers),
                       e.g. "Genesis", "St. Paul's Epistle to the Romans", "Tobit".

        Returns:
            3-letter USFM code (e.g. "GEN", "ROM", "TOB").

        Raises:
            ValueError: If the book name is not found in BOOK_NAME_TO_USFM.
        """
        from hub.constants import normalize_book_name

        usfm_id = BOOK_NAME_TO_USFM.get(normalize_book_name(book_name))
        if usfm_id is None:
            raise ValueError(
                f"Unknown book name: '{book_name}'. "
                f"Add it to BOOK_NAME_TO_USFM in hub/constants.py."
            )
        return usfm_id

    @staticmethod
    def resolve_reading_passage(
        book_name: str,
        start_chapter: int,
        start_verse: int,
        end_chapter: int,
        end_verse: int,
    ) -> tuple[str, int, int, int, int]:
        """Resolve a Reading reference to the API.Bible book/range.

        KJVAIC stores the Greek additions to Esther as ESG 1-7. The liturgical
        source may refer to the first addition as Esther 10:4-13, which maps to
        API.Bible's ESG 1:4-13.
        """
        usfm_id = BibleAPIService.resolve_book_name(book_name)
        if (
            usfm_id == "EST"
            and start_chapter == 10
            and end_chapter == 10
            and start_verse >= 4
            and end_verse <= 13
        ):
            return "ESG", 1, start_verse, 1, end_verse
        return usfm_id, start_chapter, start_verse, end_chapter, end_verse

    @staticmethod
    def resolve_reading_segments(
        book_name: str,
        start_chapter: int,
        start_verse: int,
        end_chapter: int,
        end_verse: int,
    ) -> list[tuple[str, int, int, int, int]]:
        """Resolve a Reading reference into one or more API.Bible passage segments.

        Almost every reading is a single contiguous segment, so this defers to
        ``resolve_reading_passage`` (which also handles the Esther/ESG remap).
        Romans citations that follow the Armenian liturgical tradition's
        versification of the closing doxology (numbered as part of chapter 14,
        e.g. "13.11-14.26") don't exist in NKJV: its chapter 14 ends at verse 23,
        and the same doxology is at 16:25-27. Those readings are split into a
        main-text segment and a doxology segment here, to be stitched back
        together by ``get_composite_passage``.
        """
        usfm_id = BibleAPIService.resolve_book_name(book_name)
        if usfm_id == "ROM" and end_chapter == 14 and end_verse > ROMANS_CH14_LAST_VERSE:
            segments = []
            if start_chapter < 14 or start_verse <= ROMANS_CH14_LAST_VERSE:
                segments.append(
                    (usfm_id, start_chapter, start_verse, 14, ROMANS_CH14_LAST_VERSE)
                )
            doxology_end_verse = ROMANS_DOXOLOGY_START_VERSE + (
                end_verse - (ROMANS_CH14_LAST_VERSE + 1)
            )
            segments.append((
                usfm_id, ROMANS_DOXOLOGY_CHAPTER, ROMANS_DOXOLOGY_START_VERSE,
                ROMANS_DOXOLOGY_CHAPTER, doxology_end_verse,
            ))
            return segments
        return [
            BibleAPIService.resolve_reading_passage(
                book_name, start_chapter, start_verse, end_chapter, end_verse,
            )
        ]

    @staticmethod
    def _bible_id_for_book(usfm_book_id: str) -> tuple[str, str]:
        """Return the (bible_id, version_name) to use for a given book.

        Canonical OT/NT books use NKJV. Apocrypha books use KJVAIC.
        """
        if usfm_book_id.upper() in APOCRYPHA_USFM_IDS:
            return KJVAIC_BIBLE_ID, "KJVAIC"
        return NKJV_BIBLE_ID, "NKJV"

    @staticmethod
    def _build_passage_id(
        book_id: str,
        start_chapter: int,
        start_verse: int,
        end_chapter: int,
        end_verse: int,
    ) -> str:
        """Build the passage ID string for the API.

        Format: {BOOK}.{START_CH}.{START_V}-{BOOK}.{END_CH}.{END_V}
        If start and end are the same verse, returns a single verse ID.
        """
        start = f"{book_id}.{start_chapter}.{start_verse}"
        end = f"{book_id}.{end_chapter}.{end_verse}"
        if start == end:
            return start
        return f"{start}-{end}"


# ------------------------------------------------------------------ #
#  Module-level helpers (used by views, admin, and Celery tasks)
# ------------------------------------------------------------------ #

def fetch_text_for_reading(reading, service: BibleAPIService | None = None) -> bool:
    """Fetch Bible text for a single Reading.

    Each Reading gets its own API call so that it receives a unique FUMS token,
    as required by API.Bible's Fair Use Management System terms of use.

    Can be called synchronously from views or from Celery tasks.

    Args:
        reading: A Reading model instance (must be saved to the database).
        service: Optional pre-initialized BibleAPIService. If None, one will
                 be created (requires BIBLE_API_KEY to be configured).

    Returns:
        True if the reading now has text, False if it could not be populated
        (e.g. missing API key, unknown book).
    """
    from hub.models import Reading as ReadingModel  # deferred to avoid circular import

    if service is None:
        try:
            service = BibleAPIService()
        except ValueError as e:
            logger.error("Cannot initialize BibleAPIService: %s", e)
            return False

    try:
        segments = BibleAPIService.resolve_reading_segments(
            reading.book,
            reading.start_chapter,
            reading.start_verse,
            reading.end_chapter,
            reading.end_verse,
        )
        result = service.get_composite_passage(segments)

        ReadingModel.objects.filter(pk=reading.pk).update(
            text=result["content"],
            text_copyright=result["copyright"],
            text_version=result["version"],
            text_fetched_at=timezone.now(),
            fums_token=result.get("fums_token", ""),
        )
        logger.info(
            "Fetched text for Reading %s (%s).",
            reading.pk, reading.passage_reference,
        )
        return True
    except ValueError as e:
        logger.error(
            "Book name mapping failed for Reading %s ('%s'): %s",
            reading.pk, reading.book, e,
        )
        return False
    except Exception as e:
        logger.error(
            "API call failed for Reading %s (%s): %s",
            reading.pk, reading.passage_reference, e,
        )
        return False
