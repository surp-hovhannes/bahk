"""
Management command to initialize default event types.
This should be run after migrations to set up the default event types.
"""

from django.core.management.base import BaseCommand
from events.models import EventType


class Command(BaseCommand):
    help = 'Initialize default event types for the events app'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force recreation of event types (will update existing ones)',
        )

    def handle(self, *args, **options):
        self.stdout.write('Initializing event types...')
        
        created_types = EventType.get_or_create_default_types()
        
        if created_types:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Created {len(created_types)} new event types:'
                )
            )
            for event_type in created_types:
                self.stdout.write(f'  - {event_type.name} ({event_type.code})')
        else:
            self.stdout.write(
                self.style.WARNING('All default event types already exist.')
            )
        
        # Display all current event types
        all_types = EventType.objects.all().order_by('category', 'name')
        self.stdout.write(f'\nCurrent event types ({all_types.count()}):\n')
        
        current_category = None
        for event_type in all_types:
            if current_category != event_type.category:
                current_category = event_type.category
                self.stdout.write(f'\n{current_category.upper()}:')
            
            status = "✓" if event_type.is_active else "✗"
            analytics = "📊" if event_type.track_in_analytics else ""
            target = "🎯" if event_type.requires_target else ""
            
            self.stdout.write(
                f'  {status} {event_type.name} ({event_type.code}) {analytics}{target}'
            )
        
        self.stdout.write(
            self.style.SUCCESS('\nEvent types initialization complete!')
        )