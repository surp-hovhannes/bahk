"""
Management command to generate realistic analytics data for testing dashboards.
"""

import random
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction

from events.models import Event, EventType
from hub.models import Profile, Church, Fast

User = get_user_model()


class Command(BaseCommand):
    help = 'Generate realistic analytics data for dashboard testing'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Number of days back to generate data for (default: 30)'
        )
        parser.add_argument(
            '--users',
            type=int,
            default=50,
            help='Number of test users to create (default: 50)'
        )
        parser.add_argument(
            '--events-per-day',
            type=int,
            default=200,
            help='Average number of events per day (default: 200)'
        )
        parser.add_argument(
            '--clear-existing',
            action='store_true',
            help='Clear existing analytics events before generating new ones'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be generated without actually creating data'
        )
    
    def handle(self, *args, **options):
        days = options['days']
        num_users = options['users']
        events_per_day = options['events_per_day']
        clear_existing = options['clear_existing']
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be created'))
        
        # Initialize event types
        EventType.get_or_create_default_types()
        
        if clear_existing and not dry_run:
            self.stdout.write('Clearing existing analytics events...')
            analytics_events = Event.objects.filter(
                event_type__category='analytics'
            )
            deleted_count = analytics_events.count()
            analytics_events.delete()
            self.stdout.write(f'Deleted {deleted_count} existing analytics events')
        
        # Get or create test church
        church, created = Church.objects.get_or_create(
            name='Analytics Test Church'
        )
        if created and not dry_run:
            self.stdout.write(f'Created test church: {church.name}')
        
        # Create test users
        test_users = []
        if not dry_run:
            with transaction.atomic():
                for i in range(num_users):
                    username = f'analytics_user_{i+1}'
                    email = f'analytics{i+1}@test.com'
                    
                    user, created = User.objects.get_or_create(
                        username=username,
                        defaults={
                            'email': email,
                            'first_name': f'User{i+1}',
                            'last_name': 'Test',
                            'is_active': True
                        }
                    )
                    
                    if created:
                        # Create profile
                        Profile.objects.get_or_create(
                            user=user,
                            defaults={'church': church}
                        )
                    
                    test_users.append(user)
        else:
            self.stdout.write(f'Would create {num_users} test users')
        
        # Define realistic analytics event patterns
        event_patterns = {
            EventType.APP_OPEN: {
                'weight': 30,  # Most common
                'platforms': ['ios', 'android', 'web'],
                'app_versions': ['1.0.0', '1.0.1', '1.1.0', '1.2.0'],
                'peak_hours': [8, 9, 12, 18, 19, 20, 21]  # Morning, lunch, evening
            },
            EventType.SCREEN_VIEW: {
                'weight': 40,  # Very common
                'screens': [
                    'fasts_list', 'fast_detail', 'profile_view', 'settings',
                    'devotional_view', 'checklist', 'notifications',
                    'learning_resources', 'community', 'calendar'
                ],
                'peak_hours': [8, 9, 12, 18, 19, 20, 21]
            },
            EventType.SESSION_START: {
                'weight': 25,  # Common
                'peak_hours': [8, 9, 12, 18, 19, 20, 21]
            },
            EventType.SESSION_END: {
                'weight': 20,  # Less common (some sessions don't end cleanly)
                'duration_ranges': [(30, 300), (300, 900), (900, 1800), (1800, 3600)],  # 30s-1h
                'peak_hours': [9, 13, 19, 22]  # Slightly offset from session start
            }
        }
        
        # Generate events
        total_events = 0
        now = timezone.now()
        
        for day_offset in range(days):
            current_date = now - timedelta(days=day_offset)
            
            # Vary events per day (weekends less active)
            day_of_week = current_date.weekday()
            if day_of_week >= 5:  # Weekend
                daily_events = int(events_per_day * 0.6)
            else:  # Weekday
                daily_events = events_per_day
            
            # Add some randomness
            daily_events = int(daily_events * random.uniform(0.7, 1.3))
            
            if not dry_run:
                self.generate_day_events(
                    current_date, daily_events, test_users, event_patterns
                )
            
            total_events += daily_events
            
            if day_offset % 7 == 0 or dry_run:
                self.stdout.write(
                    f'{"Would generate" if dry_run else "Generated"} '
                    f'{daily_events} events for {current_date.strftime("%Y-%m-%d")}'
                )
        
        # Summary
        self.stdout.write(
            self.style.SUCCESS(
                f'\n{"Would generate" if dry_run else "Generated"} {total_events:,} analytics events '
                f'over {days} days for {num_users} users'
            )
        )
        
        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nYou can now view the populated analytics dashboard at:\n'
                    f'Admin → Events → App Analytics Dashboard'
                )
            )
    
    def generate_day_events(self, date, num_events, users, event_patterns):
        """Generate events for a specific day."""
        events_to_create = []
        
        # Create weighted list of event types
        event_types = []
        for event_type_code, pattern in event_patterns.items():
            event_types.extend([event_type_code] * pattern['weight'])
        
        for _ in range(num_events):
            # Pick random event type
            event_type_code = random.choice(event_types)
            pattern = event_patterns[event_type_code]
            
            # Pick random user
            user = random.choice(users)
            
            # Generate realistic timestamp
            if 'peak_hours' in pattern:
                # Bias towards peak hours
                if random.random() < 0.7:  # 70% chance of peak hour
                    hour = random.choice(pattern['peak_hours'])
                else:
                    hour = random.randint(0, 23)
            else:
                hour = random.randint(0, 23)
            
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            
            timestamp = date.replace(
                hour=hour, minute=minute, second=second, microsecond=0
            )
            
            # Generate event-specific data
            data = self.generate_event_data(event_type_code, pattern)
            
            # Create the event
            try:
                event = Event.create_event(
                    event_type_code=event_type_code,
                    user=user,
                    title=self.generate_event_title(event_type_code, data),
                    data=data
                )
                
                # Update timestamp manually (Event.create_event doesn't accept timestamp)
                event.timestamp = timestamp
                event.save(update_fields=['timestamp'])
                
                events_to_create.append(event)
                
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f'Failed to create event: {e}')
                )
        
        return len(events_to_create)
    
    def generate_event_data(self, event_type_code, pattern):
        """Generate realistic data for each event type."""
        data = {}
        
        if event_type_code == EventType.APP_OPEN:
            data['platform'] = random.choice(pattern['platforms'])
            data['app_version'] = random.choice(pattern['app_versions'])
            if random.random() < 0.3:  # 30% chance of additional data
                data['device_model'] = random.choice([
                    'iPhone 12', 'iPhone 13', 'Samsung Galaxy S21', 
                    'Google Pixel 6', 'OnePlus 9'
                ])
        
        elif event_type_code == EventType.SCREEN_VIEW:
            data['screen'] = random.choice(pattern['screens'])
            if random.random() < 0.2:  # 20% chance of additional context
                data['previous_screen'] = random.choice(pattern['screens'])
            if random.random() < 0.1:  # 10% chance of time spent
                data['time_spent_seconds'] = random.randint(5, 300)
        
        elif event_type_code == EventType.SESSION_START:
            data['session_id'] = f'session_{random.randint(100000, 999999)}'
            if random.random() < 0.4:  # 40% chance of referrer
                data['referrer'] = random.choice([
                    'direct', 'push_notification', 'app_icon', 'widget'
                ])
        
        elif event_type_code == EventType.SESSION_END:
            data['session_id'] = f'session_{random.randint(100000, 999999)}'
            duration_range = random.choice(pattern['duration_ranges'])
            data['duration_seconds'] = random.randint(duration_range[0], duration_range[1])
            data['requests'] = random.randint(1, 20)
        
        return data
    
    def generate_event_title(self, event_type_code, data):
        """Generate a realistic title for each event."""
        if event_type_code == EventType.APP_OPEN:
            platform = data.get('platform', 'unknown')
            return f'App opened on {platform}'
        
        elif event_type_code == EventType.SCREEN_VIEW:
            screen = data.get('screen', 'unknown')
            return f'Viewed {screen} screen'
        
        elif event_type_code == EventType.SESSION_START:
            return 'User session started'
        
        elif event_type_code == EventType.SESSION_END:
            duration = data.get('duration_seconds', 0)
            return f'User session ended ({duration}s)'
        
        return f'Analytics event: {event_type_code}'
