"""
Management command to clean up old activity feed items based on retention policies.
"""

from django.core.management.base import BaseCommand
from events.models import UserActivityFeed
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count


class Command(BaseCommand):
    help = 'Clean up old activity feed items based on retention policies'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force cleanup even if it would delete many items'
        )
        parser.add_argument(
            '--older-than-days',
            type=int,
            help='Override retention policy and delete items older than X days'
        )
        parser.add_argument(
            '--activity-type',
            type=str,
            help='Only clean up specific activity type'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']
        older_than_days = options['older_than_days']
        activity_type = options['activity_type']

        self.stdout.write(
            self.style.SUCCESS('Starting activity feed cleanup...')
        )

        if older_than_days:
            # Override retention policy
            cutoff_date = timezone.now() - timedelta(days=older_than_days)
            query = UserActivityFeed.objects.filter(
                created_at__lt=cutoff_date,
                is_read=True
            )
            
            if activity_type:
                query = query.filter(activity_type=activity_type)
            
            if dry_run:
                count = query.count()
                self.stdout.write(
                    f'Would delete {count} items older than {older_than_days} days'
                )
            else:
                count = query.delete()[0]
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {count} items older than {older_than_days} days')
                )
        else:
            # Use retention policy
            total_deleted = UserActivityFeed.cleanup_old_items(dry_run=dry_run)
            
            if dry_run:
                self.stdout.write(
                    self.style.WARNING(f'DRY RUN: Would delete {total_deleted} items total')
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f'Successfully deleted {total_deleted} items total')
                )

        # Show current table stats
        self.show_table_stats()

    def show_table_stats(self):
        """Show current table statistics."""
        total_items = UserActivityFeed.objects.count()
        unread_items = UserActivityFeed.objects.filter(is_read=False).count()
        read_items = total_items - unread_items
        
        # Get oldest and newest items
        oldest = UserActivityFeed.objects.order_by('created_at').first()
        newest = UserActivityFeed.objects.order_by('-created_at').first()
        
        self.stdout.write('\n' + '='*50)
        self.stdout.write('CURRENT TABLE STATISTICS:')
        self.stdout.write('='*50)
        self.stdout.write(f'Total items: {total_items:,}')
        self.stdout.write(f'Unread items: {unread_items:,}')
        self.stdout.write(f'Read items: {read_items:,}')
        
        if oldest and newest:
            self.stdout.write(f'Oldest item: {oldest.created_at.date()} ({oldest.activity_type})')
            self.stdout.write(f'Newest item: {newest.created_at.date()} ({newest.activity_type})')
        
        # Show breakdown by activity type
        self.stdout.write('\nBreakdown by activity type:')
        type_counts = UserActivityFeed.objects.values('activity_type').annotate(
            count=Count('id')
        ).order_by('-count')
        
        for item in type_counts:
            self.stdout.write(f'  {item["activity_type"]}: {item["count"]:,}')
        
        self.stdout.write('='*50) 