"""Tests for the merge rule the re-key migration applies.

``survivor`` decides which of several same-named Feast rows survives a collapse onto
``(church, name)``, and what happens to the enrichment on the ones that lose. Getting it wrong
means silently discarding LLM-generated contexts, icon assignments or user feedback, so the rule
is pinned here rather than left implicit in the migration.

These use plain stubs, not model rows, for two reasons: the shape being tested -- several feasts
sharing a church and a name -- is exactly what the post-migration unique constraint forbids, so it
cannot be built in the database any more; and the migration itself runs against *historical*
models, so the rule must not depend on anything the real model adds.
"""
import datetime

from django.test import SimpleTestCase

from hub.services.feast_merge import survivor


class _Context:
    """Stands in for FeastContext, exposing only what the rule reads."""

    def __init__(self, id, active=True, time_of_generation=None, thumbs_up=0, thumbs_down=0):
        self.id = id
        self.active = active
        self.time_of_generation = time_of_generation
        self.thumbs_up = thumbs_up
        self.thumbs_down = thumbs_down


class _Contexts:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _Feast:
    """Stands in for Feast, exposing only what the rule reads."""

    def __init__(self, id, contexts=(), icon_id=None, designation=None):
        self.id = id
        self.contexts = _Contexts(contexts)
        self.icon_id = icon_id
        self.designation = designation


def _at(day):
    return datetime.datetime(2026, 1, day, tzinfo=datetime.timezone.utc)


class SurvivorTests(SimpleTestCase):

    def test_keeps_the_oldest_row_and_absorbs_the_rest(self):
        first, second, third = _Feast(1), _Feast(2), _Feast(3)
        merge = survivor([third, first, second])  # input order must not matter
        self.assertEqual(merge["keep"], first)
        self.assertEqual(merge["absorbed"], [second, third])

    def test_newest_active_context_survives(self):
        old = _Context(1, time_of_generation=_at(1))
        new = _Context(2, time_of_generation=_at(9))
        merge = survivor([_Feast(1, [old]), _Feast(2, [new])])
        self.assertEqual(merge["context_kept"], new)
        self.assertEqual(merge["contexts_deactivated"], [old])

    def test_an_inactive_newer_context_does_not_beat_an_active_one(self):
        """An inactive context was deliberately retired; recency must not resurrect it."""
        active = _Context(1, active=True, time_of_generation=_at(1))
        retired = _Context(2, active=False, time_of_generation=_at(9))
        merge = survivor([_Feast(1, [active]), _Feast(2, [retired])])
        self.assertEqual(merge["context_kept"], active)

    def test_falls_back_to_inactive_when_none_are_active(self):
        older = _Context(1, active=False, time_of_generation=_at(1))
        newer = _Context(2, active=False, time_of_generation=_at(9))
        self.assertEqual(survivor([_Feast(1, [older, newer])])["context_kept"], newer)

    def test_null_timestamps_rank_below_any_dated_context(self):
        """time_of_generation gained auto_now_add after the model shipped, so old rows are NULL.

        Comparing None against a datetime would raise; ranking them last is the intent.
        """
        undated = _Context(1, time_of_generation=None)
        dated = _Context(2, time_of_generation=_at(1))
        self.assertEqual(survivor([_Feast(1, [undated, dated])])["context_kept"], dated)

    def test_all_null_timestamps_fall_back_to_id_order(self):
        a, b = _Context(1, time_of_generation=None), _Context(2, time_of_generation=None)
        self.assertEqual(survivor([_Feast(1, [a, b])])["context_kept"], b)

    def test_thumbs_are_summed_across_the_group(self):
        merge = survivor([
            _Feast(1, [_Context(1, thumbs_up=12, thumbs_down=1)]),
            _Feast(2, [_Context(2, thumbs_up=3, thumbs_down=2)]),
        ])
        self.assertEqual((merge["thumbs_up"], merge["thumbs_down"]), (15, 3))

    def test_first_non_null_icon_and_designation_win(self):
        """The oldest row usually holds them; this rescues the case where a later one does."""
        merge = survivor([_Feast(1), _Feast(2, icon_id=7, designation="Martyrs")])
        self.assertEqual(merge["icon_id"], 7)
        self.assertEqual(merge["designation"], "Martyrs")

    def test_conflicts_are_flagged_for_a_human(self):
        merge = survivor([
            _Feast(1, icon_id=7, designation="Martyrs"),
            _Feast(2, icon_id=9, designation="Fast"),
        ])
        self.assertEqual((merge["icon_id"], merge["designation"]), (7, "Martyrs"))
        self.assertTrue(merge["icon_conflict"])
        self.assertTrue(merge["designation_conflict"])

    def test_no_conflict_when_the_group_agrees(self):
        merge = survivor([
            _Feast(1, icon_id=7, designation="Martyrs"),
            _Feast(2, icon_id=7, designation="Martyrs"),
        ])
        self.assertFalse(merge["icon_conflict"])
        self.assertFalse(merge["designation_conflict"])

    def test_group_without_contexts_survives_cleanly(self):
        merge = survivor([_Feast(1)])
        self.assertIsNone(merge["context_kept"])
        self.assertEqual(merge["contexts_deactivated"], [])
        self.assertEqual((merge["thumbs_up"], merge["thumbs_down"]), (0, 0))
        self.assertEqual(merge["absorbed"], [])
