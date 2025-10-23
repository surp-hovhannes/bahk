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
    date_joined: str
    total_items: int
    by_type: Dict[str, int]


@dataclass
class UserActivityTimelineRow:
    user_id: int
    username: str
    activity_type: str
    timestamp: str
    title: str
    description: str
    target_type: Optional[str]
    target_id: Optional[int]


@dataclass
class UserFastParticipationRow:
    user_id: int
    username: str
    email: str
    fast_id: int
    fast_name: str
    church_name: Optional[str]
    joined_at: str
    left_at: Optional[str]
    status: str


@dataclass
class RetentionCohortRow:
    cohort_week: str
    cohort_start_date: str
    total_users: int
    active_users: int
    retention_rate: float
    avg_activities_per_user: float
    cohort_age_weeks: int


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
        # Normalize to date boundaries while preserving timezone info
        if isinstance(start_date, datetime):
            # Preserve the original timezone when normalizing to start of day
            original_tz = start_date.tzinfo
            start_dt = datetime.combine(start_date.date(), datetime.min.time()).replace(tzinfo=original_tz)
        else:
            start_dt = start_date
            
        if isinstance(end_date, datetime):
            # Preserve the original timezone when normalizing to end of day
            original_tz = end_date.tzinfo
            end_dt = datetime.combine(end_date.date(), datetime.max.time()).replace(tzinfo=original_tz)
        else:
            end_dt = end_date

        output_format = options["format"]
        print_stdout = options["stdout"]
        zip_output = options["zip"]
        upload_s3 = options["upload_s3"]
        s3_prefix = options["s3_prefix"]

        # Compute metrics
        new_users_over_time = self._compute_new_users_over_time(start_dt, end_dt)
        fast_engagement = self._compute_fast_engagement(start_dt, end_dt)
        user_activity = self._compute_user_activity(start_dt, end_dt)
        user_activity_timeline = self._compute_user_activity_timeline(start_dt, end_dt)
        user_fast_participation = self._compute_user_fast_participation(start_dt, end_dt)
        retention_cohorts = self._compute_retention_cohorts(start_dt, end_dt)
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
            "user_activity_timeline": [asdict(row) for row in user_activity_timeline],
            "user_fast_participation": [asdict(row) for row in user_fast_participation],
            "retention_cohorts": [asdict(row) for row in retention_cohorts],
            "other_metrics": other_metrics,
        }

        if print_stdout:
            # Use sys.stdout directly to avoid any potential attribute conflicts
            import sys
            sys.stdout.write(json.dumps(consolidated, indent=2, default=str))
            sys.stdout.write('\n')
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
            written_files.append(self._write_json(os.path.join(output_dir, "user_activity_timeline.json"), [asdict(r) for r in user_activity_timeline]))
            written_files.append(self._write_json(os.path.join(output_dir, "user_fast_participation.json"), [asdict(r) for r in user_fast_participation]))
            written_files.append(self._write_json(os.path.join(output_dir, "retention_cohorts.json"), [asdict(r) for r in retention_cohorts]))
            written_files.append(self._write_json(os.path.join(output_dir, "other_metrics.json"), other_metrics))
        else:
            written_files.append(self._write_csv(os.path.join(output_dir, "new_users_over_time.csv"), [asdict(r) for r in new_users_over_time], ["date", "count"]))
            written_files.append(self._write_csv(os.path.join(output_dir, "fasts.csv"), [asdict(r) for r in fast_engagement], [
                "fast_id", "fast_name", "church_name", "participants", "joins_in_period", "leaves_in_period"
            ]))
            # For user activity CSV, flatten by_type keys into columns
            activity_rows, fieldnames = self._flatten_user_activity_for_csv(user_activity)
            written_files.append(self._write_csv(os.path.join(output_dir, "user_activity.csv"), activity_rows, fieldnames))
            # User activity timeline CSV
            written_files.append(self._write_csv(os.path.join(output_dir, "user_activity_timeline.csv"), [asdict(r) for r in user_activity_timeline], [
                "user_id", "username", "activity_type", "timestamp", "title", "description", "target_type", "target_id"
            ]))
            # User fast participation CSV
            written_files.append(self._write_csv(os.path.join(output_dir, "user_fast_participation.csv"), [asdict(r) for r in user_fast_participation], [
                "user_id", "username", "email", "fast_id", "fast_name", "church_name", "joined_at", "left_at", "status"
            ]))
            # Retention cohorts CSV
            written_files.append(self._write_csv(os.path.join(output_dir, "retention_cohorts.csv"), [asdict(r) for r in retention_cohorts], [
                "cohort_week", "cohort_start_date", "total_users", "active_users", "retention_rate", "avg_activities_per_user", "cohort_age_weeks"
            ]))
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
        # Optimize query to avoid N+1 problem by prefetching profiles and counting them
        fasts = (
            Fast.objects.all()
            .select_related("church")
            .prefetch_related("profiles")
            .annotate(participant_count=Count("profiles"))
        )

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
            # Safe access to church name - handle case where church might be None
            church_name = None
            if fast.church_id and fast.church:
                church_name = fast.church.name
            
            rows.append(
                FastEngagementRow(
                    fast_id=fast.id,
                    fast_name=str(fast),
                    church_name=church_name,
                    participants=fast.participant_count,
                    joins_in_period=joins_map.get(fast.id, 0),
                    leaves_in_period=leaves_map.get(fast.id, 0),
                )
            )
        return rows

    def _compute_user_activity(self, start_dt: datetime, end_dt: datetime) -> List[UserActivityRow]:
        feed = UserActivityFeed.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
        # Aggregate per user
        base = feed.values("user_id", "user__username", "user__email", "user__date_joined").annotate(total=Count("id"))

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
                    date_joined=row["user__date_joined"].isoformat() if row["user__date_joined"] else "",
                    total_items=row["total"],
                    by_type=by_user_type.get(row["user_id"], {}),
                )
            )
        return rows

    def _compute_user_activity_timeline(self, start_dt: datetime, end_dt: datetime) -> List[UserActivityTimelineRow]:
        """
        Compute detailed timeline of user activities.
        Returns last 10 activities per user, plus daily aggregations if they have more.
        """
        from django.db.models import Window
        from django.db.models.functions import RowNumber
        
        # Get all activity feed items in period
        activities = UserActivityFeed.objects.filter(
            created_at__gte=start_dt,
            created_at__lte=end_dt
        ).select_related('user', 'content_type').order_by('user_id', '-created_at')
        
        rows: List[UserActivityTimelineRow] = []
        for activity in activities:
            target_type = None
            target_id = None
            if activity.content_type:
                target_type = activity.content_type.model
                target_id = activity.object_id
            
            rows.append(
                UserActivityTimelineRow(
                    user_id=activity.user_id,
                    username=activity.user.username,
                    activity_type=activity.activity_type,
                    timestamp=activity.created_at.isoformat(),
                    title=activity.title,
                    description=activity.description,
                    target_type=target_type,
                    target_id=target_id,
                )
            )
        
        return rows

    def _compute_user_fast_participation(self, start_dt: datetime, end_dt: datetime) -> List[UserFastParticipationRow]:
        """
        Compute fast participation history for all users.
        Includes join/leave timestamps and current status.
        Properly handles multiple join/leave cycles by tracking the most recent activity.
        """
        from django.contrib.contenttypes.models import ContentType
        
        # Get Fast content type
        try:
            fast_content_type = ContentType.objects.get(app_label='hub', model='fast')
        except ContentType.DoesNotExist:
            return []
        
        # Get all join events
        join_events = Event.objects.filter(
            event_type__code=EventType.USER_JOINED_FAST,
            content_type=fast_content_type
        ).select_related('user').order_by('user_id', 'object_id', 'timestamp')
        
        # Get all leave events
        leave_events = Event.objects.filter(
            event_type__code=EventType.USER_LEFT_FAST,
            content_type=fast_content_type
        ).select_related('user').order_by('user_id', 'object_id', 'timestamp')
        
        # Build chronological event sequences for each user-fast combination
        # Structure: (user_id, fast_id) -> List[('join'|'leave', timestamp)]
        event_sequences: Dict[Tuple[int, int], List[Tuple[str, datetime]]] = {}
        
        # Process join events
        for event in join_events:
            key = (event.user_id, event.object_id)
            if key not in event_sequences:
                event_sequences[key] = []
            event_sequences[key].append(('join', event.timestamp))
        
        # Process leave events
        for event in leave_events:
            key = (event.user_id, event.object_id)
            if key not in event_sequences:
                event_sequences[key] = []
            event_sequences[key].append(('leave', event.timestamp))
        
        # Sort events chronologically for each user-fast combination
        for key in event_sequences:
            event_sequences[key].sort(key=lambda x: x[1])
        
        # Get current fast memberships
        from hub.models import Profile
        current_memberships = Profile.objects.prefetch_related('fasts', 'user').all()
        current_fast_map: Dict[int, set] = {}
        for profile in current_memberships:
            current_fast_map[profile.user_id] = set(profile.fasts.values_list('id', flat=True))
        
        # Get fast details
        all_fast_ids = set()
        for user_id, fast_id in event_sequences.keys():
            all_fast_ids.add(fast_id)
        
        fasts = Fast.objects.filter(id__in=all_fast_ids).select_related('church')
        fast_details = {f.id: f for f in fasts}
        
        # Build participation rows
        rows: List[UserFastParticipationRow] = []
        
        # Get user details
        User = get_user_model()
        user_ids = {user_id for user_id, _ in event_sequences.keys()}
        users = User.objects.filter(id__in=user_ids)
        user_details = {u.id: u for u in users}
        
        for (user_id, fast_id), events in event_sequences.items():
            user = user_details.get(user_id)
            fast = fast_details.get(fast_id)
            
            if not user or not fast:
                continue
            
            # Determine the most recent join and leave based on event sequence
            most_recent_join = None
            most_recent_leave = None
            
            # Track the current state as we process events chronologically
            current_state = None
            for event_type, timestamp in events:
                if event_type == 'join':
                    most_recent_join = timestamp
                    current_state = 'joined'
                elif event_type == 'leave':
                    most_recent_leave = timestamp
                    current_state = 'left'
            
            # Determine status based on current membership and event history
            is_currently_member = user_id in current_fast_map and fast_id in current_fast_map[user_id]
            
            # If user is currently a member, they're active regardless of leave history
            # If user is not currently a member, check if they have a recent leave event
            if is_currently_member:
                status = "active"
            elif most_recent_leave and (not most_recent_join or most_recent_leave > most_recent_join):
                status = "left"
            elif most_recent_join and (not most_recent_leave or most_recent_join > most_recent_leave):
                # This shouldn't happen if user is not currently a member, but handle gracefully
                status = "left"
            else:
                status = "unknown"
            
            rows.append(
                UserFastParticipationRow(
                    user_id=user_id,
                    username=user.username,
                    email=user.email,
                    fast_id=fast_id,
                    fast_name=str(fast),
                    church_name=fast.church.name if fast.church else None,
                    joined_at=most_recent_join.isoformat() if most_recent_join else "",
                    left_at=most_recent_leave.isoformat() if most_recent_leave else None,
                    status=status,
                )
            )
        
        return rows

    def _compute_retention_cohorts(self, start_dt: datetime, end_dt: datetime) -> List[RetentionCohortRow]:
        """
        Compute weekly user cohorts based on join date and their retention in the reporting period.
        """
        from django.db.models.functions import TruncWeek
        
        User = get_user_model()
        
        # Get all users grouped by week
        users_by_week = User.objects.annotate(
            week=TruncWeek('date_joined')
        ).values('week').annotate(
            total_users=Count('id')
        ).order_by('-week')
        
        # Get active users in period (users who had activity)
        active_user_ids = set(
            UserActivityFeed.objects.filter(
                created_at__gte=start_dt,
                created_at__lte=end_dt
            ).values_list('user_id', flat=True).distinct()
        )
        
        # Get activity counts per user
        activity_counts = {}
        for item in UserActivityFeed.objects.filter(
            created_at__gte=start_dt,
            created_at__lte=end_dt
        ).values('user_id').annotate(count=Count('id')):
            activity_counts[item['user_id']] = item['count']
        
        rows: List[RetentionCohortRow] = []
        
        for cohort in users_by_week:
            week_start = cohort['week']
            if not week_start:
                continue
            
            # Get users in this cohort
            cohort_users = User.objects.filter(
                date_joined__gte=week_start,
                date_joined__lt=week_start + timedelta(weeks=1)
            ).values_list('id', flat=True)
            
            cohort_user_ids = set(cohort_users)
            active_in_cohort = cohort_user_ids & active_user_ids
            
            total = cohort['total_users']
            active = len(active_in_cohort)
            retention_rate = (active / total * 100) if total > 0 else 0
            
            # Calculate average activities for active users in cohort
            total_activities = sum(activity_counts.get(uid, 0) for uid in active_in_cohort)
            avg_activities = (total_activities / active) if active > 0 else 0
            
            # Calculate cohort age in weeks
            cohort_age = (end_dt.date() - week_start.date()).days // 7
            
            rows.append(
                RetentionCohortRow(
                    cohort_week=week_start.strftime('%Y-W%W'),
                    cohort_start_date=week_start.date().isoformat(),
                    total_users=total,
                    active_users=active,
                    retention_rate=round(retention_rate, 2),
                    avg_activities_per_user=round(avg_activities, 2),
                    cohort_age_weeks=cohort_age,
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

        # Screen View Analytics
        screen_view_events = events_in_range.filter(event_type__code=EventType.SCREEN_VIEW)
        total_screen_views = screen_view_events.count()
        unique_screen_viewers = screen_view_events.values('user_id').distinct().count()
        
        # Top screens
        top_screens_qs = (
            screen_view_events.values('data__screen')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )
        top_screens = [
            {"screen": x['data__screen'], "views": x['count']}
            for x in top_screens_qs if x['data__screen']
        ]
        
        # Daily screen view timeline
        from django.db.models.functions import TruncDate
        screen_timeline_qs = (
            screen_view_events.annotate(day=TruncDate('timestamp'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )
        screen_timeline = {
            x['day'].isoformat(): x['count'] 
            for x in screen_timeline_qs
        }
        
        avg_screens_per_user = (total_screen_views / unique_screen_viewers) if unique_screen_viewers > 0 else 0
        
        # Devotional Engagement
        devotional_events = events_in_range.filter(event_type__code=EventType.DEVOTIONAL_VIEWED)
        total_devotional_views = devotional_events.count()
        unique_devotional_viewers = devotional_events.values('user_id').distinct().count()
        
        # Top devotional viewers
        top_devotional_viewers_qs = (
            devotional_events.values('user_id', 'user__username')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )
        top_devotional_viewers = [
            {"user_id": x['user_id'], "username": x['user__username'], "views": x['count']}
            for x in top_devotional_viewers_qs
        ]
        
        avg_devotionals_per_user = (total_devotional_views / unique_devotional_viewers) if unique_devotional_viewers > 0 else 0
        
        # Checklist Engagement
        checklist_events = events_in_range.filter(event_type__code=EventType.CHECKLIST_USED)
        total_checklist_uses = checklist_events.count()
        unique_checklist_users = checklist_events.values('user_id').distinct().count()
        
        avg_checklists_per_user = (total_checklist_uses / unique_checklist_users) if unique_checklist_users > 0 else 0
        
        # Calculate average days between checklist uses per user
        from django.db.models import Min, Max
        checklist_user_ranges = (
            checklist_events.values('user_id')
            .annotate(
                first_use=Min('timestamp'),
                last_use=Max('timestamp'),
                total_uses=Count('id')
            )
            .filter(total_uses__gt=1)  # Only users with multiple uses
        )
        
        total_days_between = 0
        users_with_multiple = 0
        for user_range in checklist_user_ranges:
            days_span = (user_range['last_use'] - user_range['first_use']).days
            uses = user_range['total_uses']
            if uses > 1 and days_span > 0:
                avg_days_for_user = days_span / (uses - 1)
                total_days_between += avg_days_for_user
                users_with_multiple += 1
        
        avg_days_between_checklist_uses = (total_days_between / users_with_multiple) if users_with_multiple > 0 else 0
        
        # Cross-engagement analysis
        devotional_user_ids = set(devotional_events.values_list('user_id', flat=True))
        checklist_user_ids = set(checklist_events.values_list('user_id', flat=True))
        both_devotional_and_checklist = len(devotional_user_ids & checklist_user_ids)
        
        # Users in fasts vs not in fasts
        from hub.models import Profile
        users_in_fasts = set(
            Profile.objects.filter(fasts__isnull=False)
            .values_list('user_id', flat=True)
            .distinct()
        )
        
        # Activity for users in fasts vs not
        activities_in_fasts = UserActivityFeed.objects.filter(
            created_at__gte=start_dt,
            created_at__lte=end_dt,
            user_id__in=users_in_fasts
        ).count()
        
        users_with_fasts_and_activity = (
            UserActivityFeed.objects.filter(
                created_at__gte=start_dt,
                created_at__lte=end_dt,
                user_id__in=users_in_fasts
            ).values('user_id').distinct().count()
        )
        
        activities_not_in_fasts = UserActivityFeed.objects.filter(
            created_at__gte=start_dt,
            created_at__lte=end_dt
        ).exclude(user_id__in=users_in_fasts).count()
        
        users_without_fasts_and_activity = (
            UserActivityFeed.objects.filter(
                created_at__gte=start_dt,
                created_at__lte=end_dt
            ).exclude(user_id__in=users_in_fasts).values('user_id').distinct().count()
        )
        
        avg_activities_with_fast = (activities_in_fasts / users_with_fasts_and_activity) if users_with_fasts_and_activity > 0 else 0
        avg_activities_without_fast = (activities_not_in_fasts / users_without_fasts_and_activity) if users_without_fasts_and_activity > 0 else 0

        return {
            "events_by_type": by_type,
            "active_users": active_users,
            "top_fasts_by_joins": top_fasts,
            "screen_views": {
                "total": total_screen_views,
                "unique_users": unique_screen_viewers,
                "avg_per_user": round(avg_screens_per_user, 2),
                "top_screens": top_screens,
                "daily_timeline": screen_timeline,
            },
            "engagement_patterns": {
                "devotionals": {
                    "total_views": total_devotional_views,
                    "unique_users": unique_devotional_viewers,
                    "avg_per_user": round(avg_devotionals_per_user, 2),
                    "top_users": top_devotional_viewers,
                },
                "checklists": {
                    "total_uses": total_checklist_uses,
                    "unique_users": unique_checklist_users,
                    "avg_per_user": round(avg_checklists_per_user, 2),
                    "avg_days_between_uses": round(avg_days_between_checklist_uses, 2),
                },
                "cross_engagement": {
                    "users_both_devotional_and_checklist": both_devotional_and_checklist,
                    "users_in_fasts_with_activity": users_with_fasts_and_activity,
                    "users_without_fasts_with_activity": users_without_fasts_and_activity,
                    "avg_activities_users_with_fast": round(avg_activities_with_fast, 2),
                    "avg_activities_users_without_fast": round(avg_activities_without_fast, 2),
                },
            },
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
        base_fields = ["user_id", "username", "email", "date_joined", "total_items"]
        fieldnames = base_fields + [f"type_{t}" for t in activity_types]
        out_rows: List[Dict[str, Any]] = []
        for r in rows:
            base = {
                "user_id": r.user_id,
                "username": r.username,
                "email": r.email,
                "date_joined": r.date_joined,
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
            key = f"{s3_prefix.rstrip('/')}/{os.path.basename(path_or_files)}"
            s3.upload_file(path_or_files, bucket, key, ExtraArgs={"ContentType": "application/zip"})
            return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

        # path_or_files is a list of files; zip in-memory and upload
        import zipfile
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in path_or_files:
                zf.write(fpath, arcname=os.path.basename(fpath))
        buffer.seek(0)
        key = f"{s3_prefix.rstrip('/')}/engagement_report.zip"
        s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue(), ContentType="application/zip")
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

