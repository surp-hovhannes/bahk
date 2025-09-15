from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandParser
from django.db.models import Count, DateField, F, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from events.models import Event, EventType, UserActivityFeed
from hub.models import Fast


@dataclass
class NewUsersOverTimeRow:
    date: str
    count: int


@dataclass
class FastEngagementRow:
    fast_id: int
    fast_name: str
    church_name: Optional[str]
    participants: int
    joins_in_period: int
    leaves_in_period: int


@dataclass
class UserActivityRow:
    user_id: int
    username: str
    email: str
    total_items: int
    by_type: Dict[str, int]


class Command(BaseCommand):
    help = (
        "Generate user engagement metrics for a date range. Outputs JSON or CSV. "
        "Optionally zip and upload to S3."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--start",
            type=str,
            required=False,
            help="Start date (YYYY-MM-DD). Defaults to 30 days ago.",
        )
        parser.add_argument(
            "--end",
            type=str,
            required=False,
            help="End date (YYYY-MM-DD). Defaults to today (inclusive).",
        )
        parser.add_argument(
            "--format",
            choices=["json", "csv"],
            default="json",
            help="Output format for per-section files.",
        )
        parser.add_argument(
            "--stdout",
            action="store_true",
            help="Print consolidated JSON to stdout instead of writing files.",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            default=None,
            help="Directory to write output files (default: a temp dir).",
        )
        parser.add_argument(
            "--zip",
            action="store_true",
            help="Zip generated files into an archive.",
        )
        parser.add_argument(
            "--upload-s3",
            action="store_true",
            help="Upload the zip (or files) to S3 and print the link.",
        )
        parser.add_argument(
            "--s3-prefix",
            type=str,
            default="engagement-reports/",
            help="S3 key prefix for uploads.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        tz_now = timezone.now()
        start_date = self._parse_date(options.get("start")) or (tz_now - timedelta(days=30))
        end_date = self._parse_date(options.get("end")) or tz_now
        # Normalize to date boundaries
        start_dt = timezone.make_aware(datetime.combine(start_date.date(), datetime.min.time())) if isinstance(start_date, datetime) else start_date
        end_dt = timezone.make_aware(datetime.combine(end_date.date(), datetime.max.time())) if isinstance(end_date, datetime) else end_date

        output_format = options["format"]
        print_stdout = options["stdout"]
        zip_output = options["zip"]
        upload_s3 = options["upload_s3"]
        s3_prefix = options["s3_prefix"]

        # Compute metrics
        new_users_over_time = self._compute_new_users_over_time(start_dt, end_dt)
        fast_engagement = self._compute_fast_engagement(start_dt, end_dt)
        user_activity = self._compute_user_activity(start_dt, end_dt)
        other_metrics = self._compute_other_metrics(start_dt, end_dt)

        consolidated = {
            "range": {
                "start": start_dt.date().isoformat(),
                "end": end_dt.date().isoformat(),
            },
            "new_users_over_time": [asdict(row) for row in new_users_over_time],
            "fasts": [asdict(row) for row in fast_engagement],
            "user_activity": [
                {
                    **{k: v for k, v in asdict(row).items() if k != "by_type"},
                    "by_type": row.by_type,
                }
                for row in user_activity
            ],
            "other_metrics": other_metrics,
        }

        if print_stdout:
            self.stdout.write(json.dumps(consolidated, indent=2, default=str))
            return

        # Write files
        output_dir = options.get("output_dir") or tempfile.mkdtemp(prefix="engagement_report_")
        os.makedirs(output_dir, exist_ok=True)

        written_files: List[str] = []
        if output_format == "json":
            written_files.append(self._write_json(os.path.join(output_dir, "new_users_over_time.json"), [asdict(r) for r in new_users_over_time]))
            written_files.append(self._write_json(os.path.join(output_dir, "fasts.json"), [asdict(r) for r in fast_engagement]))
            written_files.append(self._write_json(os.path.join(output_dir, "user_activity.json"), [
                {**{k: v for k, v in asdict(r).items() if k != "by_type"}, "by_type": r.by_type} for r in user_activity
            ]))
            written_files.append(self._write_json(os.path.join(output_dir, "other_metrics.json"), other_metrics))
        else:
            written_files.append(self._write_csv(os.path.join(output_dir, "new_users_over_time.csv"), [asdict(r) for r in new_users_over_time], ["date", "count"]))
            written_files.append(self._write_csv(os.path.join(output_dir, "fasts.csv"), [asdict(r) for r in fast_engagement], [
                "fast_id", "fast_name", "church_name", "participants", "joins_in_period", "leaves_in_period"
            ]))
            # For user activity CSV, flatten by_type keys into columns
            activity_rows, fieldnames = self._flatten_user_activity_for_csv(user_activity)
            written_files.append(self._write_csv(os.path.join(output_dir, "user_activity.csv"), activity_rows, fieldnames))
            # Write other metrics as JSON even in CSV mode
            written_files.append(self._write_json(os.path.join(output_dir, "other_metrics.json"), other_metrics))

        # Optionally zip and upload
        archive_path = None
        if zip_output:
            archive_path = self._zip_files(written_files, output_dir, start_dt, end_dt)
            self.stdout.write(f"Created archive: {archive_path}")

        if upload_s3:
            url = self._upload_to_s3(archive_path or written_files, s3_prefix)
            if url:
                self.stdout.write(f"S3 URL: {url}")
            else:
                self.stderr.write("Failed to upload to S3. Check AWS settings.")
        else:
            self.stdout.write(f"Output directory: {output_dir}")

    def _parse_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.")

    def _compute_new_users_over_time(self, start_dt: datetime, end_dt: datetime) -> List[NewUsersOverTimeRow]:
        User = get_user_model()
        qs = (
            User.objects.filter(date_joined__gte=start_dt, date_joined__lte=end_dt)
            .annotate(day=TruncDate("date_joined"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )
        return [NewUsersOverTimeRow(date=x["day"].isoformat(), count=x["count"]) for x in qs]

    def _compute_fast_engagement(self, start_dt: datetime, end_dt: datetime) -> List[FastEngagementRow]:
        fasts = Fast.objects.all().select_related("church")

        # joins/leaves in period based on Events
        joins = (
            Event.objects.filter(
                event_type__code=EventType.USER_JOINED_FAST,
                timestamp__gte=start_dt,
                timestamp__lte=end_dt,
                content_type__model="fast",
            )
            .values("object_id")
            .annotate(count=Count("id"))
        )
        leaves = (
            Event.objects.filter(
                event_type__code=EventType.USER_LEFT_FAST,
                timestamp__gte=start_dt,
                timestamp__lte=end_dt,
                content_type__model="fast",
            )
            .values("object_id")
            .annotate(count=Count("id"))
        )
        joins_map = {x["object_id"]: x["count"] for x in joins}
        leaves_map = {x["object_id"]: x["count"] for x in leaves}

        rows: List[FastEngagementRow] = []
        for fast in fasts:
            rows.append(
                FastEngagementRow(
                    fast_id=fast.id,
                    fast_name=str(fast),
                    church_name=fast.church.name if fast.church_id else None,
                    participants=fast.profiles.count(),
                    joins_in_period=joins_map.get(fast.id, 0),
                    leaves_in_period=leaves_map.get(fast.id, 0),
                )
            )
        return rows

    def _compute_user_activity(self, start_dt: datetime, end_dt: datetime) -> List[UserActivityRow]:
        feed = UserActivityFeed.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
        # Aggregate per user
        base = feed.values("user_id", "user__username", "user__email").annotate(total=Count("id"))

        # Collect counts by type per user
        by_type = (
            feed.values("user_id", "activity_type").annotate(count=Count("id"))
        )
        # Build map: user_id -> {type: count}
        by_user_type: Dict[int, Dict[str, int]] = {}
        for row in by_type:
            by_user_type.setdefault(row["user_id"], {})[row["activity_type"]] = row["count"]

        rows: List[UserActivityRow] = []
        for row in base:
            rows.append(
                UserActivityRow(
                    user_id=row["user_id"],
                    username=row["user__username"],
                    email=row["user__email"],
                    total_items=row["total"],
                    by_type=by_user_type.get(row["user_id"], {}),
                )
            )
        return rows

    def _compute_other_metrics(self, start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
        # Events by type in range
        events_in_range = Event.objects.filter(timestamp__gte=start_dt, timestamp__lte=end_dt)
        by_type = dict(
            events_in_range.values("event_type__code").annotate(count=Count("id")).values_list("event_type__code", "count")
        )

        # Active users: who had any activity feed item in period
        active_users = (
            UserActivityFeed.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
            .values("user_id")
            .distinct()
            .count()
        )

        # Top fasts by joins in period
        top_fasts_qs = (
            events_in_range.filter(
                event_type__code=EventType.USER_JOINED_FAST, content_type__model="fast"
            )
            .values("object_id")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        fast_id_to_name = {f.id: str(f) for f in Fast.objects.filter(id__in=[x["object_id"] for x in top_fasts_qs])}
        top_fasts = [
            {"fast_id": x["object_id"], "fast": fast_id_to_name.get(x["object_id"], str(x["object_id"])), "joins": x["count"]}
            for x in top_fasts_qs
        ]

        return {
            "events_by_type": by_type,
            "active_users": active_users,
            "top_fasts_by_joins": top_fasts,
        }

    def _write_json(self, path: str, obj: Any) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)
        return path

    def _write_csv(self, path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> str:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def _flatten_user_activity_for_csv(self, rows: List[UserActivityRow]) -> Tuple[List[Dict[str, Any]], List[str]]:
        # Determine all activity types present
        activity_types: List[str] = sorted({t for r in rows for t in r.by_type.keys()})
        base_fields = ["user_id", "username", "email", "total_items"]
        fieldnames = base_fields + [f"type_{t}" for t in activity_types]
        out_rows: List[Dict[str, Any]] = []
        for r in rows:
            base = {
                "user_id": r.user_id,
                "username": r.username,
                "email": r.email,
                "total_items": r.total_items,
            }
            for t in activity_types:
                base[f"type_{t}"] = r.by_type.get(t, 0)
            out_rows.append(base)
        return out_rows, fieldnames

    def _zip_files(self, files: List[str], output_dir: str, start_dt: datetime, end_dt: datetime) -> str:
        import zipfile

        base = f"engagement_{start_dt.date().isoformat()}_to_{end_dt.date().isoformat()}"
        archive_path = os.path.join(output_dir, f"{base}.zip")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                arcname = os.path.basename(path)
                zf.write(path, arcname=arcname)
        return archive_path

    def _upload_to_s3(self, path_or_files: Any, s3_prefix: str) -> Optional[str]:
        try:
            from django.conf import settings
            import boto3
        except Exception:
            return None

        bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", None)
        region = getattr(settings, "AWS_S3_REGION_NAME", None)
        access_key = getattr(settings, "AWS_ACCESS_KEY_ID", None)
        secret_key = getattr(settings, "AWS_SECRET_ACCESS_KEY", None)

        if not bucket or not access_key or not secret_key or not region:
            return None

        s3 = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        # If we have an archive path, upload that. Otherwise, create a new archive in memory.
        if isinstance(path_or_files, str):
            key = os.path.join(s3_prefix.rstrip("/"), os.path.basename(path_or_files))
            s3.upload_file(path_or_files, bucket, key, ExtraArgs={"ContentType": "application/zip"})
            return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

        # path_or_files is a list of files; zip in-memory and upload
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in path_or_files:
                zf.write(fpath, arcname=os.path.basename(fpath))
        buffer.seek(0)
        key = os.path.join(s3_prefix.rstrip("/"), "engagement_report.zip")
        s3.putObject = getattr(s3, "put_object")
        s3.putObject(Bucket=bucket, Key=key, Body=buffer.getvalue(), ContentType="application/zip")
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

