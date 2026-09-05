"""Offline evaluation must use the shared matcher and never access the ORM."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from hub.management.commands.evaluate_icon_matching import evaluate
from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate


class EvaluationTests(SimpleTestCase):
    def test_offline_default_never_constructs_live_provider(self):
        with patch("hub.services.icon_match_service.OpenAIIconProvider") as provider:
            report = evaluate([{"id": 1, "title": "Saint Narek", "tag_list": []}], ["Saint Narek"])
        provider.assert_not_called()
        self.assertEqual(report["status_counts"], {"unavailable": 1})
        self.assertEqual(report["recommendation_count"], 0)
        self.assertEqual(report["mode"], "offline")

    def test_injected_fixture_uses_production_grounding_and_eligibility(self):
        report = evaluate(
            [{"id": 1, "title": "Saint Narek", "tags": []}],
            [{"kind": "feast", "title": "Saint Narek"}],
            provider_factory=lambda i: FixtureProvider(analysis_for("Saint Narek"), [candidate(1, "Saint Narek")]),
        )
        self.assertEqual(report["auto_assignment_count"], 1)
        self.assertEqual(report["recommendation_count"], 1)
        self.assertEqual(report["call_count"], 3)
        self.assertEqual(report["results"][0]["outcome"]["model"], "offline-fixture")

    def test_json_command_writes_explicit_unavailable_offline_report(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalogue = root / "catalogue.json"
            requests = root / "requests.json"
            output = root / "output.json"
            catalogue.write_text(json.dumps([{"id": 1, "title": "Հայերեն", "tag_list": []}]))
            requests.write_text(json.dumps([{"kind": "content", "text": "Հայերեն", "tags": []}]))
            call_command(
                "evaluate_icon_matching",
                catalogue_json=str(catalogue),
                requests_json=str(requests),
                output_json=str(output),
            )
            report = json.loads(output.read_text())
            self.assertEqual(report["request_count"], 1)
            self.assertEqual(report["status_counts"], {"unavailable": 1})
            with self.assertRaises(CommandError):
                call_command(
                    "evaluate_icon_matching",
                    catalogue_json=str(catalogue),
                    requests_json=str(requests),
                    output_json=str(catalogue),
                )
