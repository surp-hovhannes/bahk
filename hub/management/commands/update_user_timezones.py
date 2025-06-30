"""
Management command to update user timezones based on their location.

This command looks up users who have location coordinates (latitude/longitude)
but no timezone set or have UTC as their timezone, and attempts to determine
their timezone based on their geographical location.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from hub.models import Profile
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Update user timezones based on their location coordinates'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--force-all',
            action='store_true',
            dest='force_all', 
            default=False,
            help='Update all users with coordinates, even if they already have non-UTC timezone',
        )
        parser.add_argument(
            '--install-timezonefinder',
            action='store_true',
            dest='install_timezonefinder',
            default=False,
            help='Install timezonefinder library if not present',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force_all = options['force_all']
        install_lib = options['install_timezonefinder']
        
        # Try to import timezonefinder
        try:
            from timezonefinder import TimezoneFinder
        except ImportError:
            if install_lib:
                import subprocess
                import sys
                self.stdout.write("Installing timezonefinder library...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "timezonefinder"])
                try:
                    from timezonefinder import TimezoneFinder
                    self.stdout.write(self.style.SUCCESS("Successfully installed timezonefinder"))
                except ImportError:
                    raise CommandError("Failed to install or import timezonefinder")
            else:
                raise CommandError(
                    "timezonefinder library is required. Install it with: pip install timezonefinder "
                    "or run this command with --install-timezonefinder"
                )

        # Initialize timezone finder
        try:
            tf = TimezoneFinder()
            self.stdout.write("Timezone finder initialized successfully")
        except Exception as e:
            raise CommandError(f"Failed to initialize TimezoneFinder: {e}")

        # Build query to find users to update
        if force_all:
            # Update all users with coordinates
            profiles_to_update = Profile.objects.filter(
                latitude__isnull=False,
                longitude__isnull=False
            ).exclude(
                latitude=0,
                longitude=0
            )
            self.stdout.write(f"Will update all {profiles_to_update.count()} profiles with coordinates")
        else:
            # Only update users with UTC or no timezone set
            profiles_to_update = Profile.objects.filter(
                latitude__isnull=False,
                longitude__isnull=False,
                timezone__in=['UTC', '']
            ).exclude(
                latitude=0,
                longitude=0
            )
            self.stdout.write(f"Will update {profiles_to_update.count()} profiles with UTC/empty timezone")

        if profiles_to_update.count() == 0:
            self.stdout.write(self.style.SUCCESS("No profiles need timezone updates!"))
            return

        # Track statistics
        stats = {
            'total': profiles_to_update.count(),
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'updates': []
        }

        # Process profiles in batches to avoid memory issues
        batch_size = 100
        
        for i in range(0, stats['total'], batch_size):
            batch = profiles_to_update[i:i+batch_size]
            self.stdout.write(f"Processing batch {i//batch_size + 1}: profiles {i+1} to {min(i+batch_size, stats['total'])}")
            
            batch_updates = []
            
            for profile in batch:
                try:
                    # Look up timezone for coordinates
                    timezone_name = tf.timezone_at(lng=profile.longitude, lat=profile.latitude)
                    
                    if timezone_name:
                        old_timezone = profile.timezone
                        
                        # Record the update info
                        update_info = {
                            'profile_id': profile.id,
                            'user_email': profile.user.email,
                            'location': profile.location,
                            'coordinates': (profile.latitude, profile.longitude),
                            'old_timezone': old_timezone,
                            'new_timezone': timezone_name
                        }
                        
                        if not dry_run:
                            # Apply the update
                            profile.timezone = timezone_name
                            batch_updates.append(profile)
                        
                        stats['updates'].append(update_info)
                        stats['successful'] += 1
                        
                        self.stdout.write(
                            f"  User {profile.user.email}: {old_timezone} -> {timezone_name} "
                            f"(Location: {profile.location})"
                        )
                    else:
                        # Could not determine timezone
                        stats['failed'] += 1
                        self.stdout.write(
                            f"  User {profile.user.email}: Could not determine timezone for "
                            f"coordinates ({profile.latitude}, {profile.longitude})"
                        )
                        
                except Exception as e:
                    stats['failed'] += 1
                    logger.error(f"Error processing profile {profile.id}: {e}")
                    self.stdout.write(
                        f"  User {profile.user.email}: Error - {e}"
                    )

            # Bulk update the batch if not dry run
            if not dry_run and batch_updates:
                with transaction.atomic():
                    Profile.objects.bulk_update(batch_updates, ['timezone'])
                self.stdout.write(f"  Saved {len(batch_updates)} timezone updates")

        # Print summary
        self.stdout.write("\n" + "="*50)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were made"))
        
        self.stdout.write(self.style.SUCCESS(
            f"Timezone update completed!\n"
            f"Total profiles processed: {stats['total']}\n"
            f"Successfully updated: {stats['successful']}\n"
            f"Failed: {stats['failed']}\n"
            f"Skipped: {stats['skipped']}"
        ))
        
        if stats['failed'] > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"\n{stats['failed']} profiles could not be updated. "
                    "Check the logs for details."
                )
            )

        # Show some example updates
        if stats['updates'] and len(stats['updates']) > 0:
            self.stdout.write("\nExample updates:")
            for update in stats['updates'][:5]:  # Show first 5
                self.stdout.write(
                    f"  {update['user_email']}: {update['old_timezone']} -> {update['new_timezone']}"
                )
            if len(stats['updates']) > 5:
                self.stdout.write(f"  ... and {len(stats['updates']) - 5} more")