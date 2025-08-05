"""
Management command to populate user activity feeds with historical data.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from events.models import Event, UserActivityFeed
from events.models import EventType

User = get_user_model()


class Command(BaseCommand):
    help = 'Populate user activity feeds with historical event data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days-back',
            type=int,
            default=30,
            help='Number of days back to populate (default: 30)'
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='Specific user ID to populate (if not provided, populates all users)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without actually creating items'
        )

    def handle(self, *args, **options):
        days_back = options['days_back']
        user_id = options['user_id']
        dry_run = options['dry_run']

        # Calculate date range
        end_date = timezone.now()
        start_date = end_date - timedelta(days=days_back)

        self.stdout.write(
            self.style.SUCCESS(
                f'Populating activity feeds for events from {start_date.date()} to {end_date.date()}'
            )
        )

        # Get users to process
        if user_id:
            try:
                users = [User.objects.get(id=user_id)]
                self.stdout.write(f'Processing user: {users[0].username}')
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'User with ID {user_id} not found')
                )
                return
        else:
            users = User.objects.filter(is_active=True)
            self.stdout.write(f'Processing {users.count()} users')

        total_created = 0
        total_skipped = 0

        for user in users:
            self.stdout.write(f'Processing user: {user.username}')
            
            # Get user's events in the date range
            events = Event.objects.filter(
                user=user,
                timestamp__gte=start_date,
                timestamp__lte=end_date
            ).select_related('event_type', 'content_type')

            user_created = 0
            user_skipped = 0

            for event in events:
                # Check if feed item already exists
                existing_feed_item = UserActivityFeed.objects.filter(
                    user=user,
                    event=event
                ).exists()

                if existing_feed_item:
                    user_skipped += 1
                    continue

                if not dry_run:
                    # Create feed item
                    feed_item = UserActivityFeed.create_from_event(event, user)
                    if feed_item:
                        user_created += 1
                else:
                    # Just count what would be created
                    # Check if this event type would create a feed item
                    activity_type_mapping = {
                        EventType.USER_JOINED_FAST: 'fast_join',
                        EventType.USER_LEFT_FAST: 'fast_leave',
                        EventType.FAST_BEGINNING: 'fast_start',
                        EventType.DEVOTIONAL_AVAILABLE: 'devotional_available',
                        EventType.FAST_PARTICIPANT_MILESTONE: 'milestone',
                    }
                    if event.event_type.code in activity_type_mapping:
                        user_created += 1

            total_created += user_created
            total_skipped += user_skipped

            self.stdout.write(
                f'  Created: {user_created}, Skipped: {user_skipped}'
            )

        # Summary
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'DRY RUN: Would create {total_created} activity feed items'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully created {total_created} activity feed items'
                )
            )
            self.stdout.write(f'Skipped {total_skipped} existing items') 