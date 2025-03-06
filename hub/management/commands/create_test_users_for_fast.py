"""
Management command to generate test users with valid profiles and add them to a specified fast.
"""
import os
import random
import uuid
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth.models import User
from hub.models import Fast, Profile, Church
from django.utils import timezone
from faker import Faker

class Command(BaseCommand):
    help = "Creates test users with valid profiles and adds them to a specified fast"

    def add_arguments(self, parser):
        parser.add_argument(
            '--fast_id', 
            type=int, 
            default=4,
            help='ID of the fast to add users to (default: 4)'
        )
        
        parser.add_argument(
            '--count', 
            type=int, 
            default=75,
            help='Number of test users to create (default: 75)'
        )
        
        # This parameter is now optional
        parser.add_argument(
            '--church_id', 
            type=int, 
            help='ID of the church to assign to profiles (optional, defaults to the fast\'s church)'
        )

    @transaction.atomic
    def handle(self, *args, **options):
        fast_id = options['fast_id']
        count = options['count']
        
        # Check if the fast exists
        try:
            fast = Fast.objects.get(id=fast_id)
        except Fast.DoesNotExist:
            raise CommandError(f"Fast with ID {fast_id} does not exist")
        
        # Get church_id from options or from the fast's association
        church_id = options.get('church_id')
        if church_id:
            # If church_id is provided, verify it exists
            try:
                church = Church.objects.get(id=church_id)
            except Church.DoesNotExist:
                raise CommandError(f"Church with ID {church_id} does not exist")
        else:
            # If church_id is not provided, use the fast's church
            if fast.church:
                church = fast.church
                church_id = church.id
                self.stdout.write(f"Using church '{church.name}' (ID: {church_id}) from fast")
            else:
                raise CommandError(f"Fast with ID {fast_id} does not have an associated church, and no church_id was provided")
            
        # Initialize faker
        fake = Faker()
        
        # List of realistic locations
        locations = [
            "Los Angeles, CA", "New York, NY", "Chicago, IL", "Houston, TX", 
            "Philadelphia, PA", "Phoenix, AZ", "San Antonio, TX", "San Diego, CA",
            "Dallas, TX", "San Jose, CA", "Boston, MA", "Austin, TX", "Detroit, MI",
            "Seattle, WA", "Denver, CO", "Portland, OR", "Atlanta, GA", "Miami, FL",
            "Washington, DC", "San Francisco, CA", "Nashville, TN", "Minneapolis, MN",
            "New Orleans, LA", "Charlotte, NC", "Pittsburgh, PA", "Baltimore, MD",
            "Cleveland, OH", "Orlando, FL", "Salt Lake City, UT", "Las Vegas, NV"
        ]
        
        # Generate a unique random prefix for this batch of users
        random_prefix = str(uuid.uuid4())[:8]
        
        # Create users
        created_users = 0
        self.stdout.write(f"Creating {count} test users for fast '{fast.name}' (ID: {fast_id}) in church '{church.name}' (ID: {church_id})...")
        
        for i in range(count):
            username = f"testuser_{random_prefix}_{fast_id}_{i+1}@example.com"
            email = username
            password = "testuser123"
            name = fake.name()
            location = random.choice(locations)
            
            # Create user
            try:
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password
                )
                
                # Create profile with only fields that exist in the current model
                profile = Profile.objects.create(
                    user=user,
                    name=name,
                    church=church,
                    location=location,
                    receive_upcoming_fast_reminders=False,
                    receive_upcoming_fast_push_notifications=True,
                    receive_ongoing_fast_push_notifications=True,
                    receive_daily_fast_push_notifications=False,
                    include_weekly_fasts_in_notifications=False
                )
                
                # Add the user to the fast
                profile.fasts.add(fast)
                profile.save()
                
                created_users += 1
                
                if (i+1) % 10 == 0 or i+1 == count:
                    self.stdout.write(f"Created {i+1} users...")
                
            except Exception as e:
                self.stderr.write(f"Error creating user {username}: {str(e)}")
                
        self.stdout.write(self.style.SUCCESS(f"Successfully created {created_users} test users for fast '{fast.name}' (ID: {fast_id})")) 