"""Unit tests for the offline Bible corpus: BibleVerse retrieval + loader.

Covers the core building blocks a future serving-path cutover will depend on:
``BibleVerse.passage_queryset`` / ``compose_passage`` (range selection,
superscription exclusion, ``[verse]`` formatting) and the ``load_bible_hy``
management command (full rebuild, dry run, error handling).
"""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.models import BibleVerse

V = BibleVerse.NOR_EJMIATSIN


class PassageRetrievalTests(TestCase):
    """``passage_queryset`` / ``compose_passage`` selection and formatting."""

    @classmethod
    def setUpTestData(cls):
        rows = []

        def add(book, chapter, verse, text, version=V):
            rows.append(BibleVerse(
                version=version, book=book, chapter=chapter, verse=verse, text=text,
            ))

        # GEN 1: a superscription (verse 0) plus verses 1-3.
        add("GEN", 1, 0, "Title of chapter one")
        add("GEN", 1, 1, "In the beginning")
        add("GEN", 1, 2, "And the earth")
        add("GEN", 1, 3, "And God said")
        # GEN 2: verses 1-3 (used for multi-chapter ranges).
        add("GEN", 2, 1, "Thus the heavens")
        add("GEN", 2, 2, "On the seventh day")
        add("GEN", 2, 3, "And God blessed")
        # GEN 3: verses 1-2 (end-chapter head).
        add("GEN", 3, 1, "Now the serpent")
        add("GEN", 3, 2, "And the woman said")
        # TOB 1: non-contiguous versification (skips verse 2).
        add("TOB", 1, 1, "The book of Tobit")
        add("TOB", 1, 3, "I, Tobit")
        # A different book and a different version, to prove isolation.
        add("EXO", 1, 1, "These are the names")
        add("GEN", 1, 1, "Different translation", version="Other Version")

        BibleVerse.objects.bulk_create(rows)

    def test_same_chapter_bounded_range(self):
        qs = BibleVerse.passage_queryset(V, "GEN", 1, 1, 1, 2)
        self.assertEqual([(r.chapter, r.verse) for r in qs], [(1, 1), (1, 2)])

    def test_superscription_excluded_even_when_range_starts_at_verse_one(self):
        # verse 0 must never appear in a reading, and asking for v1.. must not
        # pull the chapter title in front of it.
        qs = BibleVerse.passage_queryset(V, "GEN", 1, 1, 1, 3)
        self.assertNotIn(0, [r.verse for r in qs])
        self.assertEqual([r.verse for r in qs], [1, 2, 3])

    def test_multi_chapter_range_takes_tail_middle_and_head(self):
        # GEN 1:2 .. 3:1 -> tail of ch1, all of ch2, head of ch3.
        qs = BibleVerse.passage_queryset(V, "GEN", 1, 2, 3, 1)
        self.assertEqual(
            [(r.chapter, r.verse) for r in qs],
            [(1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (3, 1)],
        )

    def test_non_contiguous_versification_preserved(self):
        qs = BibleVerse.passage_queryset(V, "TOB", 1, 1, 1, 3)
        self.assertEqual([r.verse for r in qs], [1, 3])

    def test_isolated_by_version_and_book(self):
        # The GEN 1:1 row from "Other Version" and the EXO row must not leak in.
        qs = BibleVerse.passage_queryset(V, "GEN", 1, 1, 1, 1)
        texts = [r.text for r in qs]
        self.assertEqual(texts, ["In the beginning"])

    def test_compose_passage_formats_inline_markers(self):
        out = BibleVerse.compose_passage(V, "GEN", 1, 1, 1, 3)
        self.assertEqual(out, "[1] In the beginning [2] And the earth [3] And God said")

    def test_compose_passage_multi_chapter(self):
        out = BibleVerse.compose_passage(V, "GEN", 2, 2, 3, 1)
        self.assertEqual(out, "[2] On the seventh day [3] And God blessed [1] Now the serpent")

    def test_compose_passage_absent_returns_empty_string(self):
        self.assertEqual(BibleVerse.compose_passage(V, "GEN", 99, 1, 99, 5), "")


class LoadBibleHyCommandTests(TestCase):
    """Full-rebuild loader: parsing, superscriptions, dry run, errors."""

    def _corpus(self, root: Path):
        (root / "books").mkdir(parents=True)
        (root / "usfm_mapping.json").write_text(
            json.dumps([{"idArt": 9001, "usfm": "GEN"}]), encoding="utf-8",
        )
        (root / "books" / "9001.json").write_text(json.dumps({
            "chapters": {"1": {"1": "In the beginning", "2": "And the earth"}},
            "superscriptions": {"1": "Chapter title"},
        }), encoding="utf-8")

    def test_full_rebuild_loads_verses_and_superscriptions(self):
        with TemporaryDirectory() as tmp:
            self._corpus(Path(tmp))
            call_command("load_bible_hy", dir=tmp, bible_version="TestVer", stdout=StringIO())

        rows = BibleVerse.objects.filter(version="TestVer")
        self.assertEqual(rows.count(), 3)  # 2 verses + 1 superscription (verse 0)
        self.assertTrue(rows.filter(book="GEN", chapter=1, verse=0, text="Chapter title").exists())
        # ...but the superscription is never served in a composed passage.
        self.assertEqual(
            BibleVerse.compose_passage("TestVer", "GEN", 1, 1, 1, 2),
            "[1] In the beginning [2] And the earth",
        )

    def test_rebuild_is_idempotent_and_replaces_prior_rows(self):
        with TemporaryDirectory() as tmp:
            self._corpus(Path(tmp))
            call_command("load_bible_hy", dir=tmp, bible_version="TestVer", stdout=StringIO())
            call_command("load_bible_hy", dir=tmp, bible_version="TestVer", stdout=StringIO())

        # Second run deletes the first run's rows rather than duplicating them.
        self.assertEqual(BibleVerse.objects.filter(version="TestVer").count(), 3)

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            self._corpus(Path(tmp))
            call_command(
                "load_bible_hy", dir=tmp, bible_version="TestVer",
                dry_run=True, stdout=StringIO(),
            )
        self.assertFalse(BibleVerse.objects.filter(version="TestVer").exists())

    def test_missing_mapping_raises(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "books").mkdir()
            with self.assertRaises(CommandError):
                call_command("load_bible_hy", dir=tmp, stdout=StringIO(), stderr=StringIO())

    def test_empty_corpus_raises(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "books").mkdir()
            (root / "usfm_mapping.json").write_text(
                json.dumps([{"idArt": 9001, "usfm": "GEN"}]), encoding="utf-8",
            )
            with self.assertRaises(CommandError):
                call_command("load_bible_hy", dir=tmp, stdout=StringIO(), stderr=StringIO())
