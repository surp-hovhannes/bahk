"""General cross-language verse-numbering alignment for the lectionary's Bible readings.

Armenian (Grabar/Nor Ejmiatsin) versification and English (NKJV/KJVAIC) versification
occasionally diverge -- a sub-range gets relocated to a different chapter or book (the Romans
doxology, Esther's Greek additions), a book is embedded inside another book in one language only
(the Prayer of Azariah, embedded in Armenian Daniel 3), or a language simply has verses the other
lacks entirely. Rather than hand-coding a special case per book, known discrepancies are declared
as data in ``hub/data/verse_mappings.json`` and resolved here.

``resolve_segments`` is the single entry point, used by both language sides:

    - ``bible_api_service.BibleAPIService.resolve_reading_segments`` (English/API.Bible fetch)
    - ``reading_text_service.fetch_armenian_text`` (Armenian/offline corpus fetch)

A reading with no applicable rule resolves to a single identity segment -- the common case, and
the entire behavior prior to this module existing.
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

RULES_PATH = Path(settings.BASE_DIR) / "hub" / "data" / "verse_mappings.json"


@dataclass(frozen=True)
class VerseSegment:
    """One contiguous range to actually fetch/compose, in a specific book."""

    usfm: str
    start_chapter: int
    start_verse: int
    end_chapter: int
    end_verse: int

    def as_tuple(self) -> tuple[str, int, int, int, int]:
        return (self.usfm, self.start_chapter, self.start_verse, self.end_chapter, self.end_verse)


@dataclass(frozen=True)
class VerseGap:
    """A portion of the nominal range with no counterpart in the requested language.

    Reported rather than silently dropped, so callers can log it instead of serving
    quietly-incomplete text.
    """

    start_chapter: int
    start_verse: int
    end_chapter: int
    end_verse: int
    rule_id: str
    note: str
    needs_review: bool = False


@dataclass(frozen=True)
class ResolvedPassage:
    segments: list[VerseSegment]
    gaps: list[VerseGap]


@lru_cache(maxsize=1)
def _load_rules() -> tuple[dict, ...]:
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read verse mapping rules at %s", RULES_PATH, exc_info=True)
        return ()
    return tuple(data.get("rules", []))


def _identity(usfm, start_chapter, start_verse, end_chapter, end_verse) -> ResolvedPassage:
    return ResolvedPassage(
        segments=[VerseSegment(usfm, start_chapter, start_verse, end_chapter, end_verse)],
        gaps=[],
    )


def _resolve_relocation(rule, start_chapter, start_verse, end_chapter, end_verse):
    mode = rule.get("mode")

    if mode == "whole_range":
        trigger = rule["trigger"]
        if not (
            start_chapter == trigger["chapter"] == end_chapter
            and trigger["min_verse"] <= start_verse
            and end_verse <= trigger["max_verse"]
        ):
            return None
        dest = rule["relocate_to"]
        dest_usfm = dest.get("usfm", rule["usfm"])
        return ResolvedPassage(
            segments=[VerseSegment(dest_usfm, dest["chapter"], start_verse, dest["chapter"], end_verse)],
            gaps=[],
        )

    if mode == "tail_split":
        split = rule["split_after"]
        split_point = (split["chapter"], split["verse"])
        if (end_chapter, end_verse) <= split_point:
            return None  # request never reaches the relocated tail

        dest = rule["relocate_to"]
        dest_usfm = dest.get("usfm", rule["usfm"])
        offset = dest["start_verse"] - (split["verse"] + 1)

        segments = []
        if (start_chapter, start_verse) <= split_point:
            segments.append(VerseSegment(
                rule["usfm"], start_chapter, start_verse, split["chapter"], split["verse"],
            ))
            tail_start_verse = split["verse"] + 1
        else:
            tail_start_verse = start_verse
        segments.append(VerseSegment(
            dest_usfm, dest["chapter"], tail_start_verse + offset, dest["chapter"], end_verse + offset,
        ))
        return ResolvedPassage(segments=segments, gaps=[])

    logger.warning("Unknown relocation mode %r (rule id=%s)", mode, rule.get("id"))
    return None


def _resolve_composite(rule, start_chapter, start_verse, end_chapter, end_verse):
    gaps = []
    gap_spec = rule.get("gap")
    if gap_spec:
        gap_start = (gap_spec["start_chapter"], gap_spec["start_verse"])
        gap_end = (gap_spec["end_chapter"], gap_spec["end_verse"])
        req_start = (start_chapter, start_verse)
        req_end = (end_chapter, end_verse)
        if req_start <= gap_end and gap_start <= req_end:
            gaps.append(VerseGap(
                start_chapter=gap_spec["start_chapter"],
                start_verse=gap_spec["start_verse"],
                end_chapter=gap_spec["end_chapter"],
                end_verse=gap_spec["end_verse"],
                rule_id=rule["id"],
                note=gap_spec.get("note", ""),
                needs_review=gap_spec.get("needs_review", False),
            ))
    # The embedding is already materialized into real corpus rows by the offline derivation
    # script; at runtime this is an identity passthrough plus gap-awareness.
    return ResolvedPassage(
        segments=[VerseSegment(rule["usfm"], start_chapter, start_verse, end_chapter, end_verse)],
        gaps=gaps,
    )


def _resolve_no_equivalent(rule, usfm, start_chapter, start_verse, end_chapter, end_verse):
    trigger = rule.get("trigger", {})
    truncate_to = rule.get("truncate_to")
    chapter = trigger.get("chapter")
    if truncate_to is None or chapter is None:
        return None
    if start_chapter != chapter or end_chapter != chapter:
        return None

    boundary = truncate_to["verse"]
    if end_verse <= boundary:
        return None  # entirely within the range both languages agree on

    gap_spec = rule.get("gap", {})
    gap = VerseGap(
        start_chapter=chapter, start_verse=max(start_verse, boundary + 1),
        end_chapter=chapter, end_verse=end_verse,
        rule_id=rule["id"], note=gap_spec.get("note", ""),
        needs_review=gap_spec.get("needs_review", False),
    )
    if start_verse > boundary:
        return ResolvedPassage(segments=[], gaps=[gap])
    return ResolvedPassage(
        segments=[VerseSegment(usfm, start_chapter, start_verse, chapter, boundary)],
        gaps=[gap],
    )


_RESOLVERS = {
    "relocation": lambda rule, sc, sv, ec, ev: _resolve_relocation(rule, sc, sv, ec, ev),
    "composite": lambda rule, sc, sv, ec, ev: _resolve_composite(rule, sc, sv, ec, ev),
    "no_equivalent": lambda rule, sc, sv, ec, ev: _resolve_no_equivalent(rule, rule["usfm"], sc, sv, ec, ev),
}


def resolve_segments(
    language: str,
    usfm: str,
    start_chapter: int,
    start_verse: int,
    end_chapter: int,
    end_verse: int,
    *,
    rules: tuple[dict, ...] | None = None,
) -> ResolvedPassage:
    """Resolve a nominal verse range to what should actually be fetched/composed in ``language``.

    Args:
        language: ``"en"`` or ``"hy"``.
        usfm: 3-letter USFM book id of the nominal (as-requested) reference.
        start_chapter, start_verse, end_chapter, end_verse: The nominal range.
        rules: Override the loaded rule table (for tests). Defaults to
            ``hub/data/verse_mappings.json``.

    Returns:
        A :class:`ResolvedPassage` -- one or more segments to fetch/compose, plus any gaps
        (portions with no counterpart in ``language``). A reading with no applicable rule
        resolves to a single identity segment and no gaps.
    """
    applicable = _load_rules() if rules is None else rules
    for rule in applicable:
        if rule.get("usfm") != usfm or rule.get("language") != language:
            continue
        resolver = _RESOLVERS.get(rule.get("kind"))
        if resolver is None:
            logger.warning("Unknown verse mapping rule kind %r (id=%s)", rule.get("kind"), rule.get("id"))
            continue
        result = resolver(rule, start_chapter, start_verse, end_chapter, end_verse)
        if result is not None:
            return result
    return _identity(usfm, start_chapter, start_verse, end_chapter, end_verse)
