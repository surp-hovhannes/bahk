"""App constants."""
import unicodedata


DAYS_TO_CACHE_THUMBNAIL = 7
NUMBER_PARTICIPANTS_TO_SHOW_WEB = 6  # number of participant thumbnails to show on fast card on web version
DATE_FORMAT_STRING = "%m/%d/%Y"  # format string for dates (month, day, year)
ICON_MATCH_CONFIDENCE_THRESHOLD = 'high'  # Minimum confidence for icon matching (feasts use 'high', prayers use 'medium')

# --- API.Bible USFM mappings ---
# Maps book names (as stored in Reading.book, matching CATENA_ABBREV_FOR_BOOK keys)
# to 3-letter USFM codes used by the API.Bible API.
BOOK_NAME_TO_USFM = {
    # Old Testament
    "Genesis": "GEN",
    "Exodus": "EXO",
    "Leviticus": "LEV",
    "Numbers": "NUM",
    "Deuteronomy": "DEU",
    "Joshua": "JOS",
    "Judges": "JDG",
    "Ruth": "RUT",
    "1 Samuel": "1SA",
    "2 Samuel": "2SA",
    "1 Kings": "1KI",
    "2 Kings": "2KI",
    "1 Chronicles": "1CH",
    "2 Chronicles": "2CH",
    "Ezra": "EZR",
    "Nehemiah": "NEH",
    "Esther": "EST",
    "Job": "JOB",
    "Psalms": "PSA",
    "Proverbs": "PRO",
    "Ecclesiastes": "ECC",
    "Song of Solomon": "SNG",
    "Isaiah": "ISA",
    "Jeremiah": "JER",
    "Lamentations": "LAM",
    "Ezekiel": "EZK",
    "Daniel": "DAN",
    "Hosea": "HOS",
    "Joel": "JOL",
    "Amos": "AMO",
    "Obadiah": "OBA",
    "Jonah": "JON",
    "Micah": "MIC",
    "Nahum": "NAM",
    "Habakkuk": "HAB",
    "Zephaniah": "ZEP",
    "Haggai": "HAG",
    "Zechariah": "ZEC",
    "Malachi": "MAL",
    # Apocrypha / Deuterocanonical
    "Tobit": "TOB",
    "Judith": "JDT",
    "1 Maccabees": "1MA",
    "2 Maccabees": "2MA",
    "Wisdom of Solomon": "WIS",
    "Wisdom": "WIS",
    "Sirach": "SIR",
    "Epistle of Jeremiah": "LJE",
    "Baruch": "BAR",
    # The engine's one composite citation, "Daniel 3.1-23, Azariah. 1-68", resolves its second
    # half to the book name "Azariah". S3Y is already in APOCRYPHA_USFM_IDS, so its English text
    # comes from KJVAIC like the other deuterocanonical books. The Armenian corpus embeds this
    # material inside Daniel 3 rather than carrying it as a standalone book, so hy text for S3Y
    # arrives with the verse-mapping work (#470) -- until then Armenian falls back to empty here.
    "Azariah": "S3Y",
    "Prayer of Azariah": "S3Y",
    "Song of the Three Young Men": "S3Y",
    # New Testament
    "Matthew": "MAT",
    "Mark": "MRK",
    "Luke": "LUK",
    "John": "JHN",
    "Acts": "ACT",
    "Acts of the Apostles": "ACT",
    "Romans": "ROM",
    "St. Paul's Epistle to the Romans": "ROM",
    "1 Corinthians": "1CO",
    "St. Paul's First Epistle to the Corinthians": "1CO",
    "2 Corinthians": "2CO",
    "St. Paul's Second Epistle to the Corinthians": "2CO",
    "Galatians": "GAL",
    "St. Paul's Epistle to the Galatians": "GAL",
    "Ephesians": "EPH",
    "St. Paul's Epistle to the Ephesians": "EPH",
    "Philippians": "PHP",
    "St. Paul's Epistle to the Philippians": "PHP",
    "Colossians": "COL",
    "St. Paul's Epistle to the Colossians": "COL",
    "1 Thessalonians": "1TH",
    "St. Paul's First Epistle to the Thessalonians": "1TH",
    "2 Thessalonians": "2TH",
    "St. Paul's Second Epistle to the Thessalonians": "2TH",
    "1 Timothy": "1TI",
    "St. Paul's First Epistle to Timothy": "1TI",
    "2 Timothy": "2TI",
    "St. Paul's Second Epistle to Timothy": "2TI",
    "Titus": "TIT",
    "St. Paul's Epistle to Titus": "TIT",
    "Philemon": "PHM",
    "St. Paul's Epistle to Philemon": "PHM",
    "Hebrews": "HEB",
    "St. Paul's Epistle to the Hebrews": "HEB",
    "James": "JAS",
    "St. James' Epistle General": "JAS",
    "St. James General Epistle": "JAS",
    "1 Peter": "1PE",
    "St. Peter's First Epistle General": "1PE",
    "2 Peter": "2PE",
    "St. Peter's Second Epistle General": "2PE",
    "1 John": "1JN",
    "St. John's First Epistle General": "1JN",
    "2 John": "2JN",
    "St. John's Second Epistle General": "2JN",
    "3 John": "3JN",
    "St. John's Third Epistle General": "3JN",
    "St. John's Third Epistle": "3JN",
    "Jude": "JUD",
    "St. Jude's General Epistle": "JUD",
    "Revelation": "REV",
}

# Deuterocanonical / Apocrypha USFM book IDs (not in NKJV, served from KJVAIC)
APOCRYPHA_USFM_IDS = {
    "TOB",  # Tobit
    "JDT",  # Judith
    "ESG",  # Esther (Greek)
    "WIS",  # Wisdom of Solomon
    "SIR",  # Sirach / Ecclesiasticus
    "BAR",  # Baruch
    "LJE",  # Letter of Jeremiah
    "S3Y",  # Song of Three Young Men
    "SUS",  # Susanna
    "BEL",  # Bel and the Dragon
    "1MA",  # 1 Maccabees
    "2MA",  # 2 Maccabees
    "1ES",  # 1 Esdras
    "2ES",  # 2 Esdras
    "MAN",  # Prayer of Manasseh
    "PS2",  # Psalm 151
    "3MA",  # 3 Maccabees
    "4MA",  # 4 Maccabees
}

CATENA_HOME_PAGE_URL = "https://catenabible.com/"
CATENA_ABBREV_FOR_BOOK = {
    # Old Testament
    "Genesis": "gn",
    "Exodus": "ex",
    "Leviticus": "lv",
    "Numbers": "nm",
    "Deuteronomy": "dt",
    "Joshua": "jo",
    "Judges": "jgs",
    "Ruth": "ru",
    "1 Samuel": "1sm",
    "2 Samuel": "2sm",
    "1 Kings": "1kgs",
    "2 Kings": "2kgs",
    "1 Chronicles": "1chr",
    "2 Chronicles": "2chr",
    "Ezra": "ezr",
    "Nehemiah": "neh",
    "Tobit": "tb",
    "Judith": "jdt",
    "Esther": "est",
    "1 Maccabees": "1mc",
    "2 Maccabees": "2mc",
    "Job": "jb",
    "Psalms": "ps",
    "Proverbs": "prv",
    "Ecclesiastes": "eccl",
    "Song of Solomon": "sg",
    "Wisdom of Solomon": "ws",
    "Wisdom": "ws",
    "Sirach": "sir",
    "Isaiah": "is",
    "Jeremiah": "jer",
    "Lamentations": "lam",
    "Epistle of Jeremiah": "eoj",
    "Baruch": "bar",
    "Ezekiel": "ez",
    "Daniel": "dn",
    "Hosea": "hos",
    "Joel": "jl",
    "Amos": "am",
    "Obadiah": "ob",
    "Jonah": "jon",
    "Micah": "mi",
    "Nahum": "na",
    "Habakkuk": "hb",
    "Zephaniah": "zep",
    "Haggai": "hg",
    "Zechariah": "zec",
    "Malachi": "mal",
    # New Testament
    "Matthew": "mt",
    "Mark": "mk",
    "Luke": "lk",
    "John": "jn",
    "Acts": "acts",
    "Acts of the Apostles": "acts",
    "Romans": "rom",
    "St. Paul's Epistle to the Romans": "rom",
    "1 Corinthians": "1cor",
    "St. Paul's First Epistle to the Corinthians": "1cor",
    "2 Corinthians": "2cor",
    "St. Paul's Second Epistle to the Corinthians": "2cor",
    "Galatians": "gal",
    "St. Paul's Epistle to the Galatians": "gal",
    "Ephesians": "eph",
    "St. Paul's Epistle to the Ephesians": "eph",
    "Philippians": "phil",
    "St. Paul's Epistle to the Philippians": "phil",
    "Colossians": "col",
    "St. Paul's Epistle to the Colossians": "col",
    "1 Thessalonians": "1thes",
    "St. Paul's First Epistle to the Thessalonians": "1thes",
    "2 Thessalonians": "2thes",
    "St. Paul's Second Epistle to the Thessalonians": "2thes",
    "1 Timothy": "1tm",
    "St. Paul's First Epistle to Timothy": "1tm",
    "2 Timothy": "2tm",
    "St. Paul's Second Epistle to Timothy": "2tm",
    "Titus": "ti",
    "St. Paul's Epistle to Titus": "ti",
    "Philemon": "phlm",
    "St. Paul's Epistle to Philemon": "phm",
    "Hebrews": "heb",
    "St. Paul's Epistle to the Hebrews": "heb",
    "James": "jas",
    "St. James' Epistle General": "jas",
    "St. James General Epistle": "jas",
    "1 Peter": "1pt",
    "St. Peter's First Epistle General": "1pt",
    "2 Peter": "2pt",
    "St. Peter's Second Epistle General": "2pt",
    "1 John": "1jn",
    "St. John's First Epistle General": "1jn",
    "2 John": "2jn",
    "St. John's Second Epistle General": "2jn",
    "3 John": "3jn",
    "St. John's Third Epistle General": "3jn",
    "St. John's Third Epistle": "3jn",
    "Jude": "jude",
    "St. Jude's General Epistle": "jude",
    "Revelation": "rv"
}


def normalize_book_name(name: str | None) -> str | None:
    """Normalize a book name for dictionary lookup.

    Handles curly/smart quotes that scrapers may return:
    - U+2018/U+2019 curly single quotes → straight apostrophe (U+0027)
    - U+201C/U+201D curly double quotes → straight double quote (U+0022)
    - NFKC normalization handles other Unicode variations
    - Strips leading/trailing whitespace

    Returns None if name is None or empty after stripping.
    """
    if not name:
        return None
    name = unicodedata.normalize("NFKC", name)
    name = name.replace("\u2018", "'").replace("\u2019", "'")
    name = name.replace("\u201c", '"').replace("\u201d", '"')
    name = name.strip()
    return name if name else None


# Normalized lookup dict -- built once at import time
# Keys are normalized via normalize_book_name so curly-quote variants match
CATENA_ABBREV_FOR_BOOK_NORMALIZED: dict[str, str] = {
    normalize_book_name(k): v
    for k, v in CATENA_ABBREV_FOR_BOOK.items()
}

# Normalized English-book-name -> USFM lookup, built once at import time.
# Absorbs smart-quote/whitespace variants in book names coming from the lectionary engine.
BOOK_NAME_TO_USFM_NORMALIZED: dict[str, str] = {
    normalize_book_name(k): v
    for k, v in BOOK_NAME_TO_USFM.items()
}


def passage_key(
    book: str | None,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
) -> str:
    """Identity of a Scripture citation, independent of language or edition.

    Format ``{USFM}.{start_ch}.{start_v}-{end_ch}.{end_v}``, e.g. ``"GEN.1.1-1.5"``.
    Two readings with this key cite the same passage, so text fetched for one is the
    text for all of them — which is what lets a lectionary of tens of thousands of
    reading rows be served from ~1,100 retrievals.

    Book *names* are fully normalized here: ``normalize_book_name`` plus
    ``BOOK_NAME_TO_USFM`` collapse every spelling variant (curly vs. straight
    apostrophes, "Song of Solomon", the verbose Epistle titles) onto one USFM id.
    That much is language-neutral by construction, since USFM is a standard.

    Deliberately *not* normalized here: per-edition versification.  KJVAIC splits the
    Greek additions to Esther into a separate ESG book numbered 1-7, while the Armenian
    Nor Ejmiatsin corpus keeps them inline as EST chapters 10-16 — so ``Esther 10:4-13``
    is ``ESG 1:4-13`` in one and ``EST 10:4-13`` in the other.  Baking either mapping
    into the shared key would impose one edition's quirk on the other; each fetcher
    applies its own (see ``BibleAPIService.resolve_reading_passage``).

    Returns "" when the book has no USFM mapping, i.e. when no retrieval is possible in
    any language.  Never raises: an unmappable book name is a data problem to fix in
    ``BOOK_NAME_TO_USFM``, and must not be able to break a save or a request.
    """
    usfm_id = BOOK_NAME_TO_USFM_NORMALIZED.get(normalize_book_name(book))
    if usfm_id is None:
        return ""
    try:
        return (
            f"{usfm_id}.{int(start_chapter)}.{int(start_verse)}"
            f"-{int(end_chapter)}.{int(end_verse)}"
        )
    except (TypeError, ValueError):
        return ""