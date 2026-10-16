"""Exported cases start unreviewed, and the scorer leaves them out until labelled."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.models import Church, Feast
from icons.models import Icon


class ExportIconMatchCasesTests(TestCase):
    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.icon = Icon.objects.create(
            title="Palm Sunday",
            church=self.church,
            image=SimpleUploadedFile("palm.jpg", b"fake", content_type="image/jpeg"),
        )
        self.icon.tags.add("palm-sunday", "feast")
        with patch("hub.signals.match_icon_to_feast_task.delay"):
            Feast.objects.create(church=self.church, name="Palm Sunday", icon=self.icon)
            Feast.objects.create(church=self.church, name="Holy Voskian Priests")

    def export(self, root, **options):
        options.setdefault("church_id", self.church.pk)
        call_command(
            "export_icon_match_cases",
            catalogue_json=str(root / "catalogue.json"),
            cases_json=str(root / "cases.json"),
            stdout=StringIO(),
            **options,
        )
        return json.loads((root / "catalogue.json").read_text()), json.loads((root / "cases.json").read_text())

    def test_exports_catalogue_and_unreviewed_cases(self):
        with TemporaryDirectory() as directory:
            catalogue, cases = self.export(Path(directory))
        self.assertEqual(catalogue, [{"id": self.icon.id, "title": "Palm Sunday", "tags": ["feast", "palm-sunday"]}])
        self.assertEqual([c["feast"] for c in cases], ["Holy Voskian Priests", "Palm Sunday"])
        self.assertTrue(all(c["reviewed"] is False and c["correct"] == [] for c in cases))
        self.assertEqual(cases[1]["stored_icon"], {"id": self.icon.id, "title": "Palm Sunday"})

    def test_stored_icon_only_for_the_default_church(self):
        with TemporaryDirectory() as directory:
            _, cases = self.export(Path(directory), stored_icon_only=True, church_id=None)
        self.assertEqual([c["feast"] for c in cases], ["Palm Sunday"])

    def test_never_overwrites_a_case_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cases.json").write_text("[]")
            with self.assertRaises(CommandError):
                self.export(root)
            self.assertEqual((root / "cases.json").read_text(), "[]")

    def test_scorer_skips_unreviewed_cases(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, cases = self.export(root)
            cases[1].update(reviewed=True, correct=[self.icon.id])
            (root / "cases.json").write_text(json.dumps(cases))
            (root / "outcomes.json").write_text(
                json.dumps(
                    [
                        {"feast": "Palm Sunday", "status": "complete", "matches": [{"id": self.icon.id}]},
                        {"feast": "Holy Voskian Priests", "status": "complete", "matches": [{"id": 1}]},
                    ]
                )
            )
            call_command(
                "evaluate_icon_matching_cases",
                cases_json=str(root / "cases.json"),
                rescore_json=str(root / "outcomes.json"),
                output_json=str(root / "report.json"),
                stdout=StringIO(),
            )
            report = json.loads((root / "report.json").read_text())
        self.assertEqual(report["case_count"], 1)
        self.assertEqual(report["verdicts"]["exact"], 1)
        self.assertEqual(report["unreviewed_cases"], ["Holy Voskian Priests"])
