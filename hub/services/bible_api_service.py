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
