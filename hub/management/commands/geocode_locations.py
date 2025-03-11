"""
Command to geocode user locations using AWS Location Service

This management command allows administrators to geocode all user locations
in the database, either all at once or only those without coordinates.
"""
from django.core.management.base import BaseCommand, CommandError
from hub.tasks import batch_geocode_profiles
from hub.models import Profile


class Command(BaseCommand):
    help = 'Geocode user locations using AWS Location Service'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            dest='all',
            default=False,
            help='Update all user locations, even those with existing coordinates',
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='async_task',
            default=False,
            help='Run as an asynchronous Celery task',
        )

    def handle(self, *args, **options):
        update_all = options['all']
        async_task = options['async_task']
        
        # Show count of profiles needing geocoding
        if update_all:
            count = Profile.objects.filter(location__isnull=False).exclude(location='').count()
            self.stdout.write(f"Will geocode all {count} profiles with locations")
        else:
            count = Profile.objects.filter(
                location__isnull=False, 
                latitude__isnull=True, 
                longitude__isnull=True
            ).exclude(location='').count()
            self.stdout.write(f"Will geocode {count} profiles without coordinates")
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No profiles need geocoding!"))
            return
        
        if async_task:
            # Run as Celery task
            self.stdout.write("Starting asynchronous geocoding task...")
            task = batch_geocode_profiles.delay(update_all=update_all)
            self.stdout.write(self.style.SUCCESS(f"Geocoding task started! Task ID: {task.id}"))
            self.stdout.write("Check the Celery logs for progress updates.")
        else:
            # Run synchronously
            self.stdout.write("Starting geocoding process...")
            result = batch_geocode_profiles(update_all=update_all)
            
            if result['status'] == 'success':
                stats = result['stats']
                self.stdout.write(self.style.SUCCESS(
                    f"Geocoding completed successfully! "
                    f"Processed {stats['total']} profiles: "
                    f"{stats['successful']} successful, "
                    f"{stats['failed']} failed, "
                    f"{stats['cached']} from cache."
                ))
            else:
                self.stdout.write(self.style.ERROR(f"Geocoding failed: {result['message']}")) 