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
        resp = self.session.get(url, params=params)
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

    @staticmethod
    def resolve_book_name(book_name: str) -> str:
        """Resolve a book name (as stored in Reading.book) to its 3-letter USFM ID.

        Args:
            book_name: Book name as it appears in the database, e.g. "Genesis",
                       "St. Paul's Epistle to the Romans", "Tobit".

        Returns:
            3-letter USFM code (e.g. "GEN", "ROM", "TOB").

        Raises:
            ValueError: If the book name is not found in BOOK_NAME_TO_USFM.
        """
        usfm_id = BOOK_NAME_TO_USFM.get(book_name)
        if usfm_id is None:
            raise ValueError(
                f"Unknown book name: '{book_name}'. "
                f"Add it to BOOK_NAME_TO_USFM in hub/constants.py."
            )
        return usfm_id

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
#  Module-level helpers (used by views and Celery tasks)
# ------------------------------------------------------------------ #

def fetch_and_update_passage(
    service: BibleAPIService,
    book_name: str,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> int:
    """Fetch a passage from API.Bible and update ALL matching Readings.

    Args:
        service: Initialized BibleAPIService instance.
        book_name: Book name as stored in Reading.book (English).
        start_chapter: Starting chapter number.
        start_verse: Starting verse number.
        end_chapter: Ending chapter number.
        end_verse: Ending verse number.

    Returns:
        Number of Reading rows updated.

    Raises:
        ValueError: If book name cannot be resolved to a USFM code.
        requests.HTTPError: On API errors.
    """
    from hub.models import Reading  # deferred to avoid circular import

    usfm_id = BibleAPIService.resolve_book_name(book_name)
    result = service.get_passage(
        usfm_id, start_chapter, start_verse, end_chapter, end_verse
    )

    now = timezone.now()

    # Update ALL readings with this exact passage (across all days/years)
    updated = Reading.objects.filter(
        book=book_name,
        start_chapter=start_chapter,
        start_verse=start_verse,
        end_chapter=end_chapter,
        end_verse=end_verse,
    ).update(
        text=result["content"],
        text_copyright=result["copyright"],
        text_version=result["version"],
        text_fetched_at=now,
        fums_token=result.get("fums_token", ""),
    )

    return updated


def fetch_text_for_reading(reading, service: BibleAPIService | None = None) -> bool:
    """Fetch Bible text for a single Reading, with deduplication.

    If another Reading with the same passage already has text, copies it
    instead of making an API call. Otherwise, fetches from API.Bible and
    updates ALL Readings sharing the same passage.

    Can be called synchronously from views or from Celery tasks.

    Args:
        reading: A Reading model instance (must be saved to the database).
        service: Optional pre-initialized BibleAPIService. If None, one will
                 be created (requires BIBLE_API_KEY to be configured).

    Returns:
        True if the reading now has text (either fetched or copied),
        False if it could not be populated (e.g. missing API key, unknown book).
    """
    from hub.models import Reading  # deferred to avoid circular import

    # --- Deduplication: check for an existing reading with the same passage ---
    existing = Reading.objects.filter(
        book=reading.book,
        start_chapter=reading.start_chapter,
        start_verse=reading.start_verse,
        end_chapter=reading.end_chapter,
        end_verse=reading.end_verse,
        text_fetched_at__isnull=False,
    ).exclude(text="").first()

    if existing and existing.pk != reading.pk:
        Reading.objects.filter(pk=reading.pk).update(
            text=existing.text,
            text_copyright=existing.text_copyright,
            text_version=existing.text_version,
            text_fetched_at=existing.text_fetched_at,
            fums_token=existing.fums_token,
        )
        logger.info(
            "Copied text for Reading %s from existing Reading %s (%s)",
            reading.pk, existing.pk, reading.passage_reference,
        )
        return True

    # --- Fetch from API.Bible ---
    if service is None:
        try:
            service = BibleAPIService()
        except ValueError as e:
            logger.error("Cannot initialize BibleAPIService: %s", e)
            return False

    try:
        updated = fetch_and_update_passage(
            service,
            reading.book,
            reading.start_chapter,
            reading.start_verse,
            reading.end_chapter,
            reading.end_verse,
        )
        logger.info(
            "Fetched text for Reading %s (%s), updated %d reading(s).",
            reading.pk, reading.passage_reference, updated,
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
