"""Assign one approved existing icon to a feast."""

import argparse
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hub.cache import invalidate_feast_api_cache_for_feast
from hub.models import Feast
from icons.models import Icon

CURRENT_ICON_UNSET = object()


def positive_integer(value):
    """Return a positive integer for an argparse option."""
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


class Command(BaseCommand):
    """Assign one operator-approved icon without invoking automatic matching."""

    help = (
        "Preview or assign one existing, approved icon to a feast. The icon must "
        "belong to the feast's church. Writing requires --execute and the exact "
        "--confirm feast:<feast-id>:icon:<icon-id> token; replacement also requires --force."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--feast-id",
            required=True,
            type=positive_integer,
            help="Positive ID of the feast to update.",
        )
        parser.add_argument(
            "--icon-id",
            required=True,
            type=positive_integer,
            help="Positive ID of an existing icon from the feast's church.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=("Explicitly request the default non-mutating preview; incompatible with --execute."),
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Apply the assignment; requires the exact --confirm token.",
        )
        parser.add_argument(
            "--confirm",
            help="Exact confirmation token: feast:<feast-id>:icon:<icon-id>.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace a different current icon; valid only with confirmed --execute.",
        )

    def handle(self, *args, **options):
        feast_id = options["feast_id"]
        icon_id = options["icon_id"]
        execute = options["execute"]
        force = options["force"]
        expected_confirmation = f"feast:{feast_id}:icon:{icon_id}"

        if options["dry_run"] and execute:
            raise CommandError("--dry-run cannot be combined with --execute.")
        if force and not execute:
            raise CommandError("--force is valid only with --execute and exact --confirm.")
        if execute and options["confirm"] != expected_confirmation:
            raise CommandError(f"--execute requires --confirm {expected_confirmation}.")

        feast, icon = self._load_records(feast_id, icon_id)

        if not execute:
            self._check_conflict(feast, icon_id, force)
            status = (
                "NO-OP" if feast.icon_id == icon_id else ("WOULD REPLACE" if feast.icon_id is not None else "DRY RUN")
            )
            self.stdout.write(self._result_line(status, feast, icon))
            return

        with transaction.atomic():
            feast, icon = self._load_locked_records(feast_id, icon_id)
            self._check_conflict(feast, icon_id, force)

            if feast.icon_id == icon_id:
                self.stdout.write(self._result_line("NO-OP", feast, icon))
                return

            old_icon = feast.icon
            status = "REPLACED" if feast.icon_id is not None else "ASSIGNED"
            feast.icon = icon
            feast.save(update_fields=["icon"])
            transaction.on_commit(lambda: invalidate_feast_api_cache_for_feast(feast))
            self.stdout.write(self._result_line(status, feast, icon, current_icon=old_icon))

    def _load_records(self, feast_id, icon_id):
        try:
            feast = Feast.objects.select_related("church", "icon").get(pk=feast_id)
        except Feast.DoesNotExist as exc:
            raise CommandError(f"Feast {feast_id} does not exist.") from exc
        return feast, self._get_icon(icon_id, feast.church_id)

    def _load_locked_records(self, feast_id, icon_id):
        try:
            feast = Feast.objects.select_for_update().select_related("church").get(pk=feast_id)
        except Feast.DoesNotExist as exc:
            raise CommandError(f"Feast {feast_id} does not exist.") from exc

        try:
            icon = Icon.objects.select_for_update().get(
                pk=icon_id,
                church_id=feast.church_id,
            )
        except Icon.DoesNotExist as exc:
            self._raise_icon_error(icon_id, feast.church_id, exc)
        return feast, icon

    def _get_icon(self, icon_id, church_id):
        try:
            return Icon.objects.get(pk=icon_id, church_id=church_id)
        except Icon.DoesNotExist as exc:
            self._raise_icon_error(icon_id, church_id, exc)

    def _raise_icon_error(self, icon_id, church_id, cause):
        if Icon.objects.filter(pk=icon_id).exists():
            raise CommandError(f"Icon {icon_id} belongs to a different church; expected church {church_id}.") from cause
        raise CommandError(f"Icon {icon_id} does not exist.") from cause

    def _check_conflict(self, feast, icon_id, force):
        if feast.icon_id not in (None, icon_id) and not force:
            raise CommandError(
                f"Feast {feast.pk} already has icon {feast.icon_id}; review the current "
                "assignment and use --force with confirmed --execute to replace it."
            )

    def _result_line(
        self,
        status,
        feast,
        requested_icon,
        current_icon=CURRENT_ICON_UNSET,
    ):
        if current_icon is CURRENT_ICON_UNSET:
            current_icon = feast.icon
        fields = [
            status,
            f"feast_id={feast.pk}",
            f"feast_name={json.dumps(feast.name, ensure_ascii=True)}",
            "date=null",
            f"church_id={feast.church_id}",
            f"church_name={json.dumps(feast.church.name, ensure_ascii=True)}",
            "current_icon=none"
            if current_icon is None
            else (
                f"current_icon_id={current_icon.pk} "
                f"current_icon_title={json.dumps(current_icon.title, ensure_ascii=True)}"
            ),
            f"requested_icon_id={requested_icon.pk}",
            f"requested_icon_title={json.dumps(requested_icon.title, ensure_ascii=True)}",
        ]
        return " ".join(fields)
