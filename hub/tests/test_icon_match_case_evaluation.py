"""Labelled-case scoring must judge what production would actually write."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from hub.management.commands.evaluate_icon_matching_cases import evaluate_cases, rescore, score, verdict
from hub.services.icon_match_profiles import CONTROL
from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate

EXACT = {"feast": "Palm Sunday", "correct": [1], "acceptable": [2]}
FALLBACK = {"feast": "Beheading", "correct": [], "acceptable": [3]}
NOTHING = {"feast": "Remembrance of the Dead", "correct": [], "acceptable": []}


class FailingProvider:
    model = "offline-failing"

    def call(self, stage, payload, schema, timeout):
        raise RuntimeError("provider down")


def match(icon_id, assignable=False):
    return {"id": icon_id, "auto_assignable": assignable}


class VerdictTests(SimpleTestCase):
    def test_top_match_is_classified_against_labels(self):
        self.assertEqual(verdict(EXACT, [match(1), match(9)]), "exact")
        self.assertEqual(verdict(EXACT, [match(2)]), "acceptable")
        self.assertEqual(verdict(EXACT, [match(9), match(1)]), "wrong")
        self.assertEqual(verdict(NOTHING, [match(9)]), "wrong")

    def test_silence_is_right_only_when_nothing_fits(self):
        self.assertEqual(verdict(NOTHING, []), "abstained")
        self.assertEqual(verdict(FALLBACK, []), "missed")
        self.assertEqual(verdict(EXACT, []), "missed")


class AssignmentScoringTests(SimpleTestCase):
    def test_only_the_first_match_of_a_complete_outcome_is_written(self):
        # A labelled, assignable second match must not hide a wrong first one.
        row = score(EXACT, "complete", [match(9, True), match(1, True)])
        self.assertEqual(row["assigned_id"], 9)
        self.assertTrue(row["unsafe_assignment"])
        self.assertIsNone(score(EXACT, "partial", [match(1, True)])["assigned_id"])
        self.assertIsNone(score(EXACT, "complete", [match(1, False)])["assigned_id"])

    def test_fallbacks_are_not_assignable_unless_labelled(self):
        self.assertTrue(score(FALLBACK, "complete", [match(3, True)])["unsafe_assignment"])
        labelled = {**FALLBACK, "assignable": [3]}
        self.assertFalse(score(labelled, "complete", [match(3, True)])["unsafe_assignment"])
        self.assertFalse(score(EXACT, "complete", [match(1, True)])["unsafe_assignment"])
        self.assertTrue(score(EXACT, "complete", [match(2, True)])["unsafe_assignment"])


class EvaluateCasesTests(SimpleTestCase):
    def test_runs_the_production_matcher_and_summarises(self):
        catalogue = [{"id": 1, "title": "Saint Narek", "tags": []}, {"id": 2, "title": "Other", "tags": []}]
        cases = [{"feast": "Saint Narek", "correct": [1], "acceptable": []}]
        report = evaluate_cases(
            catalogue,
            cases,
            CONTROL,
            provider_factory=lambda p: FixtureProvider(analysis_for("Saint Narek"), [candidate(1, "Saint Narek")]),
        )
        self.assertEqual(report["verdicts"]["exact"], 1)
        self.assertEqual(report["desirable_count"], 1)
        self.assertEqual(report["complete_count"], 1)
        self.assertEqual(report["assigned_count"], 1)
        self.assertEqual(report["unsafe_assignment_count"], 0)
        self.assertEqual(report["by_expectation"]["exact_exists"], {"exact": 1})

    def test_provider_failure_is_an_error_row_without_details(self):
        def broken(profile):
            raise RuntimeError("secret-bearing message")

        report = evaluate_cases([{"id": 1, "title": "x", "tags": []}], [NOTHING], CONTROL, provider_factory=broken)
        row = report["cases"][0]
        self.assertEqual(row["verdict"], "error")
        self.assertEqual(row["diagnostics"], ["arm_failed"])
        self.assertNotIn("secret", json.dumps(report))
        self.assertEqual(report["desirable_count"], 0)


class RescoreTests(SimpleTestCase):
    def test_rescore_applies_current_labels_to_saved_rows(self):
        rows = [
            {"feast": "Beheading", "status": "complete", "matches": [match(3, True)]},
            {"feast": "Remembrance of the Dead", "status": "unavailable", "diagnostics": ["arm_failed"]},
        ]
        report = rescore(rows, [FALLBACK, NOTHING, EXACT])
        self.assertEqual(report["unsafe_assignment_count"], 1)
        self.assertEqual(report["verdicts"]["error"], 1)
        self.assertEqual(report["unscored_cases"], ["Palm Sunday"])
        relabelled = rescore(
            {"profile": {"profile_id": "x"}, "cases": rows}, [{**FALLBACK, "assignable": [3]}, NOTHING]
        )
        self.assertEqual(relabelled["unsafe_assignment_count"], 0)
        self.assertEqual(relabelled["profile"], {"profile_id": "x"})

    def test_unavailable_outcome_is_an_error_not_a_silence(self):
        rows = [{"feast": "Remembrance of the Dead", "status": "unavailable", "matches": []}]
        self.assertEqual(rescore(rows, [NOTHING])["verdicts"]["error"], 1)
        report = evaluate_cases(
            [{"id": 1, "title": "x", "tags": []}],
            [NOTHING],
            CONTROL,
            provider_factory=lambda p: FailingProvider(),
        )
        self.assertEqual(report["verdicts"]["error"], 1)
        self.assertEqual(report["desirable_count"], 0)

    def test_rescore_rejects_unlabelled_rows(self):
        with self.assertRaises(ValueError):
            rescore([{"feast": "Unknown", "status": "complete", "matches": []}], [EXACT])


class CommandTests(SimpleTestCase):
    def test_requires_exactly_one_mode(self):
        with self.assertRaises(CommandError):
            call_command("evaluate_icon_matching_cases", cases_json="c.json", output_json="o.json")
        with self.assertRaises(CommandError):
            call_command(
                "evaluate_icon_matching_cases",
                cases_json="c.json",
                output_json="o.json",
                live=True,
                rescore_json="r.json",
            )

    def test_rescore_writes_report_without_constructing_a_provider(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cases.json").write_text(json.dumps([EXACT]))
            (root / "report.json").write_text(
                json.dumps([{"feast": "Palm Sunday", "status": "complete", "matches": [match(1, True)]}])
            )
            with patch("hub.management.commands.evaluate_icon_matching_cases.OpenAIIconProvider") as provider:
                call_command(
                    "evaluate_icon_matching_cases",
                    cases_json=str(root / "cases.json"),
                    rescore_json=str(root / "report.json"),
                    output_json=str(root / "out.json"),
                    stdout=StringIO(),
                )
            provider.assert_not_called()
            report = json.loads((root / "out.json").read_text())
            self.assertEqual(report["verdicts"]["exact"], 1)
            self.assertEqual(report["assigned_count"], 1)

    def test_refuses_to_overwrite_an_input(self):
        with TemporaryDirectory() as directory:
            cases = Path(directory) / "cases.json"
            cases.write_text(json.dumps([EXACT]))
            with self.assertRaises(CommandError):
                call_command(
                    "evaluate_icon_matching_cases",
                    cases_json=str(cases),
                    rescore_json=str(cases),
                    output_json=str(cases),
                )
