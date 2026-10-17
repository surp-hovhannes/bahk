"""Apply a reviewed set of feast icon and icon metadata decisions.

The file (see hub/data/icon_review/) records, per feast, the icon a reviewer chose
("icon"), that the current icon is right ("keep"), or that no icon fits ("none"),
together with the icon the feast had when it was reviewed. Per icon it records a new
title and tags to add ("update") or that the icon is fine as it is ("keep").

Dry run by default: every planned change and every skip is printed, nothing is
written. With --apply, each change is written in its own transaction under a row
lock, and only if the row still looks the way the reviewer saw it, the same guard
the staff assign-icon endpoint uses. Running it twice is safe: applied changes
report "already done" the second time.
"""

import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hub.models import Feast
from icons.models import Icon

FEAST_CHOICES = ("icon", "keep", "none")
ICON_CHOICES = ("update", "keep")
TITLE_MAX = Icon._meta.get_field("title").max_length
TAG_MAX = 100  # taggit's Tag.name max_length


class Stale(Exception):
    """The row changed after the review; skip it rather than overwrite."""


def load_review(path):
    try:
        review = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise CommandError(f"Cannot read review file {path}: {exc}") from exc
    if not isinstance(review, dict):
        raise CommandError("Review file must be a JSON object")
    feasts, icons = review.get("feasts", []), review.get("icons", [])
    for item in feasts:
        if item.get("choice") not in FEAST_CHOICES or not isinstance(item.get("feast_id"), int):
            raise CommandError(f"Invalid feast decision: {item}")
        if item["choice"] == "icon" and not isinstance(item.get("icon_id"), int):
            raise CommandError(f"Feast decision without icon_id: {item}")
    for item in icons:
        if item.get("choice") not in ICON_CHOICES or not isinstance(item.get("icon_id"), int):
            raise CommandError(f"Invalid icon decision: {item}")
        if item["choice"] == "update":
            title = item.get("title")
            tags = item.get("add_tags", [])
            if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
                raise CommandError(f"Icon {item['icon_id']}: title must be 1-{TITLE_MAX} characters")
            if not isinstance(tags, list) or any(not isinstance(t, str) or not t or len(t) > TAG_MAX for t in tags):
                raise CommandError(f"Icon {item['icon_id']}: add_tags must be non-empty strings up to {TAG_MAX}")
    return feasts, icons


def plan_feast(item):
    """Return (action, detail) for one feast decision, without writing."""
    if item["choice"] == "keep":
        return "unchanged", "reviewer kept the current icon"
    feast = Feast.objects.filter(pk=item["feast_id"]).first()
    if feast is None:
        return "skip", "feast not found"
    target = item["icon_id"] if item["choice"] == "icon" else None
    if feast.icon_id == target:
        return "done", "already set"
    if feast.icon_id != item.get("expected_icon_id"):
        return "skip", f"icon changed since review (now {feast.icon_id})"
    if target is not None:
        icon = Icon.objects.filter(pk=target).first()
        if icon is None:
            return "skip", f"icon {target} not found"
        if icon.church_id != feast.church_id:
            return "skip", f"icon {target} belongs to another church"
        verb = "replace" if feast.icon_id else "assign"
        return verb, f"{feast.icon_id or '-'} -> {target} ({icon.title})"
    return "remove", f"{feast.icon_id} -> none"


def apply_feast(item):
    target = item["icon_id"] if item["choice"] == "icon" else None
    with transaction.atomic():
        feast = Feast.objects.select_for_update().get(pk=item["feast_id"])
        if feast.icon_id != item.get("expected_icon_id"):
            raise Stale(f"icon changed since review (now {feast.icon_id})")
        if target is not None:
            icon = Icon.objects.select_for_update().get(pk=target)
            if icon.church_id != feast.church_id:
                raise Stale(f"icon {target} belongs to another church")
        feast.icon_id = target
        feast.save(update_fields=["icon"])


def new_tags(icon, wanted):
    have = {name.lower() for name in icon.tags.names()}
    seen, out = set(), []
    for tag in wanted:
        key = tag.strip().lower()
        if key and key not in have and key not in seen:
            seen.add(key)
            out.append(tag.strip())
    return out


def plan_icon(item):
    if item["choice"] == "keep":
        return "unchanged", "reviewer kept title and tags"
    icon = Icon.objects.filter(pk=item["icon_id"]).first()
    if icon is None:
        return "skip", "icon not found"
    title = item["title"].strip()
    tags = new_tags(icon, item.get("add_tags", []))
    if icon.title == title and not tags:
        return "done", "already set"
    if icon.title != item.get("expected_title") and icon.title != title:
        return "skip", f"title changed since review (now {icon.title!r})"
    parts = []
    if icon.title != title:
        parts.append(f"title {icon.title!r} -> {title!r}")
    if tags:
        parts.append("add tags " + ", ".join(tags))
    return "update", "; ".join(parts)


def apply_icon(item):
    title = item["title"].strip()
    with transaction.atomic():
        icon = Icon.objects.select_for_update().get(pk=item["icon_id"])
        if icon.title != item.get("expected_title") and icon.title != title:
            raise Stale(f"title changed since review (now {icon.title!r})")
        if icon.title != title:
            icon.title = title
            icon.save(update_fields=["title"])
        tags = new_tags(icon, item.get("add_tags", []))
        if tags:
            icon.tags.add(*tags)


class Command(BaseCommand):
    help = "Apply reviewed feast icon and icon title/tag decisions. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("review_file")
        parser.add_argument("--apply", action="store_true", help="Write the changes (default: dry run)")

    def handle(self, *args, **options):
        feasts, icons = load_review(options["review_file"])
        live = options["apply"]
        tally = Counter()
        self.stdout.write(("APPLYING" if live else "DRY RUN (nothing is written; pass --apply)") + "\n")

        for label, items, plan, apply, key in (
            ("Feast", feasts, plan_feast, apply_feast, "feast_id"),
            ("Icon", icons, plan_icon, apply_icon, "icon_id"),
        ):
            for item in items:
                action, detail = plan(item)
                if live and action in ("assign", "replace", "remove", "update"):
                    try:
                        apply(item)
                    except Stale as exc:
                        action, detail = "skip", str(exc)
                name = item.get("name", "")
                if action not in ("unchanged", "done"):
                    self.stdout.write(f"{label} {item[key]}: {action:8} {detail}" + (f"  [{name}]" if name else ""))
                tally[f"{label.lower()} {action}"] += 1

        self.stdout.write("\nSummary: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
