"""Export one church's icon catalogue and feasts as labelled-case inputs. Read-only.

Writes a catalogue snapshot and a case file in `evaluate_icon_matching_cases`
format. Every case starts unreviewed with empty labels and the icon currently
stored on the feast, if any: a stored icon is evidence for the reviewer, not a
label, because some stored assignments are wrong. The scorer skips cases still
marked `"reviewed": false`, so the file can be scored as labelling progresses.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from hub.models import Church, Feast
from icons.models import Icon


def export(church):
    icons = Icon.objects.filter(church=church).prefetch_related("tags").order_by("id")
    catalogue = [{"id": i.id, "title": i.title, "tags": sorted(t.name for t in i.tags.all())} for i in icons]
    cases = [
        {
            "feast": feast.name,
            "observance_id": feast.observance_id,
            "reviewed": False,
            "correct": [],
            "acceptable": [],
            "note": "",
            "stored_icon": {"id": feast.icon.id, "title": feast.icon.title} if feast.icon else None,
        }
        for feast in Feast.objects.filter(church=church).select_related("icon").order_by("name", "id")
    ]
    return catalogue, cases


class Command(BaseCommand):
    help = "Write a church's icon catalogue and unreviewed feast cases for evaluate_icon_matching_cases."

    def add_arguments(self, parser):
        parser.add_argument("--church-id", type=int, required=True)
        parser.add_argument("--catalogue-json", required=True)
        parser.add_argument("--cases-json", required=True)
        parser.add_argument("--stored-icon-only", action="store_true", help="Only feasts that already have an icon")

    def handle(self, *args, **options):
        church = Church.objects.filter(pk=options["church_id"]).first()
        if church is None:
            raise CommandError(f"No church with id {options['church_id']}")
        outputs = [Path(options["catalogue_json"]), Path(options["cases_json"])]
        if outputs[0].resolve() == outputs[1].resolve():
            raise CommandError("--catalogue-json and --cases-json must differ")
        # Never overwrite a case file that may already hold a reviewer's labels.
        existing = [str(path) for path in outputs if path.exists()]
        if existing:
            raise CommandError(f"Refusing to overwrite: {', '.join(existing)}")
        catalogue, cases = export(church)
        if options["stored_icon_only"]:
            cases = [case for case in cases if case["stored_icon"]]
        for path, data in zip(outputs, (catalogue, cases)):
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        stored = sum(1 for case in cases if case["stored_icon"])
        self.stdout.write(f"{len(catalogue)} icons, {len(cases)} feasts ({stored} with a stored icon)")
