"""Offline diagnostics; only this new command enumerates the festal calendar."""

import json
from datetime import date, timedelta
from importlib.metadata import version as package_version

from django.core.management.base import BaseCommand, CommandError

from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_taxonomy_matching import interpret, match_icons
from icons.models import Icon
from icons.services.taxonomy_inputs import versions


def calendar_requests():
    import armenian_lectionary as engine

    day, end = date(engine.MIN_YEAR, 1, 1), date(engine.MAX_YEAR, 12, 31)
    seen = set()
    while day <= end:
        en = (engine.compute_armenian_lectionary(day, language="en").get("Liturgical Day") or "").strip()
        hy = (engine.compute_armenian_lectionary(day, language="hy").get("Liturgical Day") or "").strip()
        if en and (en, hy) not in seen:
            seen.add((en, hy))
            yield {"text": en, "bilingual_labels": [en, hy], "kind": "feast"}
        day += timedelta(days=1)


class Command(BaseCommand):
    help = "Report deterministic coverage and unresolved interpretation; never visual accuracy."

    def add_arguments(self, parser):
        parser.add_argument("--church", type=int, required=True)
        parser.add_argument("--calendar", action="store_true")
        parser.add_argument("--request", action="append", default=[])
        parser.add_argument("--fixtures", help="JSON list of text/kind requests; original labels are retained")
        parser.add_argument("--output")

    def handle(self, **options):
        requests = [{"text": text, "kind": "content"} for text in options["request"]]
        if options["fixtures"]:
            with open(options["fixtures"]) as source:
                requests.extend(json.load(source))
        if options["calendar"]:
            requests.extend(calendar_requests())
        if not requests:
            raise CommandError("Supply --calendar, --request or --fixtures")
        icons = list(Icon.objects.filter(church_id=options["church"]).prefetch_related("tags"))
        rows = []
        # Explicitly prohibit the optional adapter in offline evaluation.
        from django.test.utils import override_settings

        with override_settings(ICON_TAXONOMY_REQUEST_ADAPTER_ENABLED=False):
            for row in requests:
                request = IconMatchRequest(
                    kind=row.get("kind", "content"),
                    primary_text=row.get("text", row.get("primary_text", "")),
                    context_terms=tuple(row.get("context_terms", ())),
                    auto_assign_policy="feast_strict" if row.get("kind") == "feast" else "none",
                    max_results=10,
                )
                labels = row.get("bilingual_labels", [])
                commemoration = {"name_en": labels[0], "name_hy": labels[1]} if len(labels) == 2 else None
                parsed = interpret(request, church_id=options["church"], commemoration=commemoration)
                outcome = match_icons(icons, request, church_id=options["church"], commemoration=commemoration)
                alternate = [
                    interpret(IconMatchRequest(kind=request.kind, primary_text=label), church_id=options["church"])
                    for label in labels
                ]
                rows.append(
                    {
                        "source": row,
                        "interpretation": parsed,
                        "bilingual_interpretations": alternate,
                        "resolved": not parsed["unresolved"],
                        "candidate_count": len(outcome.matches),
                        "assignment_eligible": any(m["auto_assignable"] for m in outcome.matches),
                        "fallback_available": any(m["relation"] == "subject_portrait" for m in outcome.matches),
                        "outcome": outcome.to_dict(),
                    }
                )
        report = {
            "versions": versions(),
            "engine_version": package_version("armenian-lectionary"),
            "church": options["church"],
            "metric": "evidence_consistency_and_retrieval_coverage_not_visual_accuracy",
            "calendar_artifact_only": options["calendar"],
            "catalogue_count": len(icons),
            "requests": rows,
        }
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if options["output"]:
            with open(options["output"], "w") as target:
                target.write(output + "\n")
        else:
            self.stdout.write(output)
