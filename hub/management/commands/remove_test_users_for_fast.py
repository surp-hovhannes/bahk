"""
Management command to remove test users that were created by the create_test_users_for_fast command.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth.models import User
from hub.models import Fast, Profile

class Command(BaseCommand):
    help = "Removes test users that were created for a specific fast"

    def add_arguments(self, parser):
        parser.add_argument(
            '--fast_id', 
            type=int, 
            default=4,
            help='ID of the fast from which to remove test users (default: 4)'
        )
        
        parser.add_argument(
            '--dry_run',
            action='store_true',
            help='Only print the users that would be deleted without actually deleting them'
        )
        
        parser.add_argument(
            '--all_test_users',
            action='store_true',
            help='Remove all users with "testuser" in their username, regardless of fast association'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        fast_id = options['fast_id']
        dry_run = options['dry_run']
        all_test_users = options['all_test_users']
        
        # If removing all test users, we don't need to check the fast
        if not all_test_users:
            # Check if the fast exists
            try:
                fast = Fast.objects.get(id=fast_id)
            except Fast.DoesNotExist:
                raise CommandError(f"Fast with ID {fast_id} does not exist")
        
        # Find test users
        if all_test_users:
            # Find all users with "testuser" in their username
            test_users = User.objects.filter(username__contains="testuser")
            fast_name = "all fasts"
        else:
            # Find users associated with the specific fast through their profiles
            profiles = Profile.objects.filter(fasts__id=fast_id)
            test_users = User.objects.filter(
                profile__in=profiles,
                username__contains="testuser"
            )
            fast_name = fast.name
        
        user_count = test_users.count()
        
        if user_count == 0:
            self.stdout.write(self.style.WARNING(f"No test users found for {fast_name}"))
            return
        
        self.stdout.write(f"Found {user_count} test users for '{fast_name}'")
        
        if dry_run:
            self.stdout.write("Dry run mode. The following users would be deleted:")
            for user in test_users:
                self.stdout.write(f" - {user.username}")
            self.stdout.write(self.style.SUCCESS(f"Dry run completed. {user_count} users would be deleted."))
            return
        
        # Delete the test users (this will also delete their profiles due to CASCADE)
        deleted_count = 0
        for user in test_users:
            username = user.username
            try:
                user.delete()
                deleted_count += 1
                if deleted_count % 10 == 0 or deleted_count == user_count:
                    self.stdout.write(f"Deleted {deleted_count}/{user_count} users...")
            except Exception as e:
                self.stderr.write(f"Error deleting user {username}: {str(e)}")
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully removed {deleted_count} test users for '{fast_name}'"
            )
        ) 