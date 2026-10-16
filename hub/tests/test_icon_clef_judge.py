"""The Clef icon judge decides in code from Clef's probabilities, with no network."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from bahk.public_api.work import public_request
from hub.management.commands.evaluate_icon_matching_cases import evaluate_cases_clef
from hub.services.clef import ClefError
from hub.services.icon_clef_judge import (
    FEAST_QUESTIONS,
    judge_icons,
    lexical_shortlist,
    place,
)
from hub.services.icon_match_profiles import CONTROL
from hub.services.icon_match_service import serialize_catalogue
from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate

CATALOGUE = [
    {"id": 1, "title": "Palm Sunday Entry into Jerusalem", "tags": ["palm-sunday"]},
    {"id": 2, "title": "Palm Sunday 2", "tags": []},
    {"id": 3, "title": "St. John the Baptist", "tags": ["forerunner"]},
    {"id": 4, "title": "StGregoryofTatevandStJohnofVorodn", "tags": ["icon"]},
    {"id": 5, "title": "Crucifixion 4", "tags": ["cross"]},
]


class FakeClef:
    """Answers by icon title; records every call and the request context it ran in."""

    def __init__(self, names_event=0.9, by_title=None, fail_titles=()):
        self.names_event = names_event
        self.by_title = by_title or {}
        self.fail_titles = set(fail_titles)
        self.calls = []
        self.contexts = []

    def __call__(self, state, questions, model="clef"):
        self.calls.append((state, tuple(questions), model))
        self.contexts.append(public_request.get())
        if questions is FEAST_QUESTIONS or "names_event" in questions:
            return {"names_event": {"noul": self.names_event}}
        title = state.split("\n")[1].removeprefix("Icon title: ")
        if title in self.fail_titles:
            raise ClefError("down")
        values = self.by_title.get(title, {"depicts_event": 0.02, "all_figures": 0.02, "related": 0.02})
        return {name: {"noul": value} for name, value in values.items()}


def probs(event=0.02, figures=0.02, related=0.02):
    return {"depicts_event": event, "all_figures": figures, "related": related}


class LexicalShortlistTests(SimpleTestCase):
    def test_shared_words_rank_and_run_together_titles_split(self):
        records = serialize_catalogue(CATALOGUE)
        self.assertEqual([r["id"] for r in lexical_shortlist("Palm Sunday", records)], [1, 2])
        ids = [r["id"] for r in lexical_shortlist("Sts. John the Patriarch and Gregory of Tatev", records)]
        self.assertEqual(ids[0], 4)
        self.assertIn(3, ids)

    def test_generic_words_alone_shortlist_nothing(self):
        self.assertEqual(lexical_shortlist("Remembrance of the Saints", serialize_catalogue(CATALOGUE)), [])


class PlacementTests(SimpleTestCase):
    def test_ladder_and_assignment(self):
        self.assertEqual(place(probs(event=0.9), True), ("exact_event", 0.9))
        self.assertEqual(place(probs(figures=0.9), False), ("exact_subject", 0.9))
        # A portrait for an event feast sits below any depiction of the event.
        self.assertEqual(place(probs(figures=0.95), True), ("subject_portrait", 0.95))
        self.assertEqual(place(probs(related=0.7), False), ("related_specific", 0.7))
        self.assertEqual(place(probs(), True), (None, 0.0))


class JudgeTests(SimpleTestCase):
    def test_ranks_by_rung_and_assigns_only_near_certain_depictions(self):
        clef = FakeClef(
            names_event=0.9,
            by_title={
                "Palm Sunday 2": probs(event=0.7, figures=0.9),
                "Palm Sunday Entry into Jerusalem": probs(event=0.95, figures=0.9),
            },
        )
        outcome = judge_icons(CATALOGUE, "Palm Sunday", run=clef)
        self.assertEqual(outcome.status, "complete")
        self.assertTrue(outcome.catalogue_complete)
        self.assertEqual([m["id"] for m in outcome.matches], [1, 2])
        self.assertEqual([m["auto_assignable"] for m in outcome.matches], [True, False])
        self.assertEqual(outcome.matches[0]["relation"], "exact_event")
        self.assertEqual(outcome.call_count, 3)
        self.assertIn("clef_shortlist:lexical:2", outcome.diagnostics)

    def test_portrait_is_assigned_when_no_event_depiction_is_shortlisted(self):
        clef = FakeClef(names_event=0.95, by_title={"St. John the Baptist": probs(figures=0.97, related=0.99)})
        outcome = judge_icons(CATALOGUE, "Beheading of St. John the Forerunner (Baptist)", run=clef)
        self.assertEqual(outcome.matches[0]["id"], 3)
        self.assertEqual(outcome.matches[0]["relation"], "subject_portrait")
        self.assertTrue(outcome.matches[0]["auto_assignable"])

    def test_portrait_is_only_suggested_when_the_event_may_be_shortlisted(self):
        # A possible depiction of the event, even one too uncertain to assign, outranks the portrait.
        clef = FakeClef(
            names_event=0.95,
            by_title={
                "St. John the Baptist": probs(figures=0.97, related=0.99),
                "StGregoryofTatevandStJohnofVorodn": probs(event=0.6, related=0.7),
            },
        )
        outcome = judge_icons(CATALOGUE, "Beheading of St. John the Forerunner (Baptist)", run=clef)
        self.assertEqual([m["id"] for m in outcome.matches], [4, 3])
        self.assertEqual([m["auto_assignable"] for m in outcome.matches], [False, False])

    def test_unrelated_candidates_are_omitted(self):
        outcome = judge_icons(CATALOGUE, "Feast of the Holy Cross", run=FakeClef())
        self.assertEqual(outcome.status, "complete")
        self.assertEqual(outcome.matches, [])

    def test_no_shortlist_means_silence_without_calls(self):
        clef = FakeClef()
        outcome = judge_icons(CATALOGUE, "Holy Voskian Priests", run=clef)
        self.assertEqual((outcome.status, outcome.matches, clef.calls), ("complete", [], []))

    def test_a_failed_icon_call_keeps_the_outcome_partial(self):
        clef = FakeClef(by_title={"Palm Sunday Entry into Jerusalem": probs(event=0.95)}, fail_titles={"Palm Sunday 2"})
        outcome = judge_icons(CATALOGUE, "Palm Sunday", run=clef)
        self.assertEqual(outcome.status, "partial")
        self.assertFalse(outcome.catalogue_complete)
        self.assertIn("clef_icon_question_failed:1", outcome.diagnostics)

    def test_invalid_probability_counts_as_a_failed_call(self):
        def run(state, questions, model="clef"):
            if "names_event" in questions:
                return {"names_event": {"noul": 0.1}}
            return {name: {"noul": "0.9"} for name in questions}

        outcome = judge_icons(CATALOGUE, "Palm Sunday", run=run)
        self.assertEqual(outcome.status, "partial")
        self.assertEqual(outcome.matches, [])

    def test_failed_feast_question_is_unavailable_and_asks_nothing_else(self):
        def run(state, questions, model="clef"):
            calls.append(questions)
            raise ClefError("down")

        calls = []
        outcome = judge_icons(CATALOGUE, "Palm Sunday", run=run)
        self.assertEqual(outcome.status, "unavailable")
        self.assertEqual(len(calls), 1)

    def test_provided_shortlist_is_used_deduplicated(self):
        clef = FakeClef(by_title={"Crucifixion 4": probs(related=0.8)})
        outcome = judge_icons(CATALOGUE, "Palm Sunday", candidate_ids=[5, 5, 99], run=clef)
        self.assertIn("clef_shortlist:provided:1", outcome.diagnostics)
        self.assertEqual([m["id"] for m in outcome.matches], [5])

    def test_public_reads_cannot_start_clef_work(self):
        clef = FakeClef()
        token = public_request.set(SimpleNamespace())
        try:
            with patch("bahk.public_api.traffic.report_failure"), self.assertRaises(RuntimeError):
                judge_icons(CATALOGUE, "Palm Sunday", run=clef)
        finally:
            public_request.reset(token)
        self.assertEqual(clef.calls, [])

    def test_worker_threads_see_the_callers_request_context(self):
        sentinel = object()
        clef = FakeClef(by_title={"Palm Sunday 2": probs(event=0.9)})
        token = public_request.set(sentinel)
        try:
            with patch("hub.services.icon_clef_judge.reject_public_work"):
                judge_icons(CATALOGUE, "Palm Sunday", run=clef)
        finally:
            public_request.reset(token)
        self.assertEqual(len(clef.contexts), 3)
        self.assertTrue(all(context is sentinel for context in clef.contexts))


class HarnessTests(SimpleTestCase):
    cases = [
        {"feast": "Palm Sunday", "correct": [1, 2], "acceptable": []},
        {"feast": "Holy Voskian Priests", "correct": [], "acceptable": []},
    ]

    def test_clef_engine_needs_no_llm(self):
        clef = FakeClef(by_title={"Palm Sunday Entry into Jerusalem": probs(event=0.95)})
        with (
            patch("hub.services.icon_clef_judge.run_clef", clef),
            patch("hub.management.commands.evaluate_icon_matching_cases.OpenAIIconProvider") as provider,
        ):
            report = evaluate_cases_clef(CATALOGUE, self.cases)
        provider.assert_not_called()
        self.assertEqual(report["profile"]["engine"], "clef")
        self.assertEqual(report["verdicts"]["exact"], 1)
        self.assertEqual(report["verdicts"]["abstained"], 1)
        self.assertEqual(report["assigned_count"], 1)
        self.assertEqual(report["unsafe_assignment_count"], 0)

    def test_llm_engine_shortlist_is_judged_by_clef(self):
        records = [{"id": 7, "title": "Saint Narek", "tags": []}, {"id": 8, "title": "Other", "tags": []}]
        clef = FakeClef(names_event=0.1, by_title={"Saint Narek": probs(figures=0.9)})
        with patch("hub.services.icon_clef_judge.run_clef", clef):
            report = evaluate_cases_clef(
                records,
                [{"feast": "Saint Narek", "correct": [7], "acceptable": []}],
                profile=CONTROL,
                provider_factory=lambda p: FixtureProvider(analysis_for("Saint Narek"), [candidate(7, "Saint Narek")]),
            )
        row = report["cases"][0]
        self.assertEqual(report["profile"]["engine"], "llm+clef")
        self.assertIn("clef_shortlist:provided:1", row["diagnostics"])
        self.assertIn("llm_shortlist_status:complete", row["diagnostics"])
        self.assertEqual((row["verdict"], row["assigned_id"]), ("exact", 7))
