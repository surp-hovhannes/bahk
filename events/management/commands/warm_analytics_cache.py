"""
Management command to warm analytics caches.
Run this periodically (e.g. via cron) to ensure fast dashboard loading.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Warm analytics caches for common date ranges'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force cache refresh even if data already cached',
        )
        parser.add_argument(
            '--ranges',
            type=str,
            default='7,30,90',
            help='Comma-separated list of day ranges to warm (default: 7,30,90)',
        )

    def handle(self, *args, **options):
        from events.analytics_cache import AnalyticsCacheWarmer, AnalyticsCacheService
        from events.analytics_optimizer import AnalyticsQueryOptimizer
        
        force = options['force']
        ranges = [int(x.strip()) for x in options['ranges'].split(',')]
        
        self.stdout.write(f"Warming analytics cache for ranges: {ranges}")
        
        now = timezone.now()
        warmed_count = 0
        skipped_count = 0
        
        for days in ranges:
            try:
                end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timezone.timedelta(days=1)
                start_of_window = end_of_today - timezone.timedelta(days=days)
                
                # Check if already cached (unless force is True)
                if not force:
                    cached = AnalyticsCacheService.get_daily_aggregates(start_of_window, days)
                    if cached:
                        self.stdout.write(f"  {days} days: Already cached, skipping")
                        skipped_count += 1
                        continue
                
                # Generate and cache data
                self.stdout.write(f"  {days} days: Generating data...")
                start_time = timezone.now()
                
                daily_aggregates = AnalyticsQueryOptimizer.get_daily_event_aggregates(
                    start_of_window, days
                )
                
                end_time = timezone.now()
                duration = (end_time - start_time).total_seconds()
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {days} days: Completed in {duration:.2f}s"
                    )
                )
                warmed_count += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  {days} days: Failed - {str(e)}")
                )
                logger.error(f"Cache warming failed for {days} days: {e}", exc_info=True)
        
        summary = f"Cache warming completed: {warmed_count} warmed, {skipped_count} skipped"
        self.stdout.write(self.style.SUCCESS(summary))
        logger.info(summary)
