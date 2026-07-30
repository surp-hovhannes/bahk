"""Tests for hub.services.verse_mapping.resolve_segments.

Covers the three rule kinds -- relocation (whole_range and tail_split), composite, and
no_equivalent -- using both the real shipped rules (Romans, Esther, Azariah) and synthetic
fixtures for cases not yet backed by real corpus data (e.g. Song of Songs' extra verses).
"""
from django.test import TestCase

from hub.constants import APOCRYPHA_USFM_IDS, BOOK_NAME_TO_USFM
from hub.services.verse_mapping import (
    VerseSegment,
    _load_rules,
    resolve_segments,
)


class DefaultIdentityTests(TestCase):
    """A reading with no applicable rule passes through unchanged."""

    def test_no_rule_returns_identity_segment(self):
        resolved = resolve_segments("en", "GEN", 1, 1, 1, 5)
        self.assertEqual(resolved.segments, [VerseSegment("GEN", 1, 1, 1, 5)])
        self.assertEqual(resolved.gaps, [])

    def test_armenian_side_unaffected_by_english_only_rules(self):
        """Romans and Esther rules are English-only; the Armenian corpus needs no adjustment."""
        resolved = resolve_segments("hy", "ROM", 13, 11, 14, 26)
        self.assertEqual(resolved.segments, [VerseSegment("ROM", 13, 11, 14, 26)])
        self.assertEqual(resolved.gaps, [])


class RomansDoxologyRelocationTests(TestCase):
    """tail_split relocation: Romans 13.11-14.26 (Armenian numbering) -> NKJV split."""

    def test_full_range_splits_main_and_doxology(self):
        resolved = resolve_segments("en", "ROM", 13, 11, 14, 26)
        self.assertEqual(
            resolved.segments,
            [
                VerseSegment("ROM", 13, 11, 14, 23),
                VerseSegment("ROM", 16, 25, 16, 27),
            ],
        )
        self.assertEqual(resolved.gaps, [])

    def test_partial_doxology_range(self):
        """A request ending mid-doxology (14.25) still splits and offsets correctly."""
        resolved = resolve_segments("en", "ROM", 13, 11, 14, 25)
        self.assertEqual(
            resolved.segments,
            [
                VerseSegment("ROM", 13, 11, 14, 23),
                VerseSegment("ROM", 16, 25, 16, 26),
            ],
        )

    def test_doxology_only_range(self):
        """A request starting inside the relocated tail produces only the relocated segment."""
        resolved = resolve_segments("en", "ROM", 14, 24, 14, 26)
        self.assertEqual(resolved.segments, [VerseSegment("ROM", 16, 25, 16, 27)])

    def test_in_range_request_untouched(self):
        """A request that never reaches the doxology doesn't trigger relocation."""
        resolved = resolve_segments("en", "ROM", 13, 11, 14, 23)
        self.assertEqual(resolved.segments, [VerseSegment("ROM", 13, 11, 14, 23)])

    def test_non_romans_book_unaffected(self):
        resolved = resolve_segments("en", "1CO", 13, 1, 14, 26)
        self.assertEqual(resolved.segments, [VerseSegment("1CO", 13, 1, 14, 26)])


class EstherGreekAdditionsRelocationTests(TestCase):
    """whole_range relocation: Esther's Greek additions -> KJVAIC's standalone ESG."""

    def test_esther_greek_addition_maps_to_esg(self):
        resolved = resolve_segments("en", "EST", 10, 4, 10, 9)
        self.assertEqual(resolved.segments, [VerseSegment("ESG", 1, 4, 1, 9)])
        self.assertEqual(resolved.gaps, [])

    def test_regular_esther_stays_canonical(self):
        resolved = resolve_segments("en", "EST", 10, 1, 10, 3)
        self.assertEqual(resolved.segments, [VerseSegment("EST", 10, 1, 10, 3)])


class AzariahCompositeTests(TestCase):
    """composite kind: Azariah embedded in Armenian Daniel 3; runtime is gap-awareness only."""

    def test_range_within_known_verses_has_no_gap(self):
        resolved = resolve_segments("hy", "S3Y", 1, 1, 1, 67)
        self.assertEqual(resolved.segments, [VerseSegment("S3Y", 1, 1, 1, 67)])
        self.assertEqual(resolved.gaps, [])

    def test_range_touching_verse_68_reports_gap(self):
        resolved = resolve_segments("hy", "S3Y", 1, 1, 1, 68)
        self.assertEqual(resolved.segments, [VerseSegment("S3Y", 1, 1, 1, 68)])
        self.assertEqual(len(resolved.gaps), 1)
        gap = resolved.gaps[0]
        self.assertEqual(gap.rule_id, "azariah-in-daniel")
        self.assertTrue(gap.needs_review)
        self.assertEqual((gap.start_chapter, gap.start_verse, gap.end_chapter, gap.end_verse), (1, 68, 1, 68))

    def test_english_side_unaffected(self):
        """English S3Y is native/complete (1-68); no gap on that side."""
        resolved = resolve_segments("en", "S3Y", 1, 1, 1, 68)
        self.assertEqual(resolved.segments, [VerseSegment("S3Y", 1, 1, 1, 68)])
        self.assertEqual(resolved.gaps, [])


class NoEquivalentSyntheticTests(TestCase):
    """no_equivalent kind: proven with a synthetic fixture (Song of Songs' extra verses aren't
    in the loaded corpus yet -- see hub/data/bible_hy/books/2827.json, which has 14 verses in
    chapter 8, matching English; this becomes a real rule once that corpus gap is filled)."""

    RULES = (
        {
            "id": "song-of-songs-8-extra-example",
            "kind": "no_equivalent",
            "usfm": "SNG",
            "language": "en",
            "trigger": {"chapter": 8, "max_verse": 20},
            "truncate_to": {"verse": 14},
            "gap": {
                "note": "Armenian ch.8 has 6 additional verses absent from every English translation.",
            },
        },
    )

    def test_range_within_aligned_verses_untouched(self):
        resolved = resolve_segments("en", "SNG", 8, 1, 8, 14, rules=self.RULES)
        self.assertEqual(resolved.segments, [VerseSegment("SNG", 8, 1, 8, 14)])
        self.assertEqual(resolved.gaps, [])

    def test_range_touching_excluded_tail_truncates_and_reports_gap(self):
        resolved = resolve_segments("en", "SNG", 8, 1, 8, 20, rules=self.RULES)
        self.assertEqual(resolved.segments, [VerseSegment("SNG", 8, 1, 8, 14)])
        self.assertEqual(len(resolved.gaps), 1)
        gap = resolved.gaps[0]
        self.assertEqual(gap.rule_id, "song-of-songs-8-extra-example")
        self.assertEqual((gap.start_chapter, gap.start_verse, gap.end_chapter, gap.end_verse), (8, 15, 8, 20))

    def test_range_entirely_in_excluded_tail_yields_no_segments(self):
        resolved = resolve_segments("en", "SNG", 8, 15, 8, 20, rules=self.RULES)
        self.assertEqual(resolved.segments, [])
        self.assertEqual(len(resolved.gaps), 1)


class RuleSchemaSanityTests(TestCase):
    """Every shipped rule's `usfm` must resolve somewhere real, catching typos/dead entries."""

    def test_every_rule_usfm_is_known(self):
        known_usfm_ids = set(BOOK_NAME_TO_USFM.values()) | APOCRYPHA_USFM_IDS
        for rule in _load_rules():
            self.assertIn(
                rule["usfm"], known_usfm_ids,
                f"Rule {rule.get('id')!r} references unknown USFM id {rule['usfm']!r}",
            )

    def test_every_rule_has_required_keys(self):
        for rule in _load_rules():
            self.assertIn("id", rule)
            self.assertIn("kind", rule)
            self.assertIn("usfm", rule)
            self.assertIn("language", rule)
            self.assertIn(rule["language"], ("en", "hy"))
