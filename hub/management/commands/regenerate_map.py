"""
Management command to regenerate a participant map for a specific fast.
"""
from django.core.management.base import BaseCommand, CommandError
from hub.models import Fast, FastParticipantMap
from hub.tasks import generate_participant_map
import time


class Command(BaseCommand):
    help = 'Regenerates the participant map for a specific fast'

    def add_arguments(self, parser):
        parser.add_argument('fast_id', type=int, help='ID of the fast to regenerate the map for')
        parser.add_argument('--wait', action='store_true', help='Wait for the map to be generated')

    def handle(self, *args, **options):
        fast_id = options['fast_id']
        wait = options['wait']
        
        try:
            fast = Fast.objects.get(id=fast_id)
        except Fast.DoesNotExist:
            raise CommandError(f'Fast with ID {fast_id} does not exist')
        
        # Delete existing map if it exists
        FastParticipantMap.objects.filter(fast=fast).delete()
        
        self.stdout.write(self.style.SUCCESS(f'Deleted existing map for fast "{fast}"'))
        
        # Trigger map generation
        task = generate_participant_map.delay(fast_id)
        
        self.stdout.write(self.style.SUCCESS(f'Triggered map generation for fast "{fast}" (Task ID: {task.id})'))
        
        if wait:
            self.stdout.write('Waiting for map generation to complete...')
            
            # Wait for the task to complete
            while not task.ready():
                time.sleep(1)
                self.stdout.write('.', ending='')
                self.stdout.flush()
            
            # Get the result
            result = task.get()
            
            if result.get('status') == 'success':
                self.stdout.write(self.style.SUCCESS(
                    f'\nMap generation completed successfully! '
                    f'Map URL: {result.get("map_url")}, '
                    f'Participants: {result.get("participants")}'
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f'\nMap generation failed: {result.get("message")}'
                ))
        else:
            self.stdout.write(self.style.WARNING(
                'Map generation is running in the background. '
                'Check the map URL in a few minutes.'
            )) 