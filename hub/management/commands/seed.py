"""Generates seed data for app with python manage.py seed."""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.core.management import call_command

import hub.models as models


USERNAMES1 = ["user1a", "user1b"]
EMAILS1 = ["user1a@email.com", "user1b@email.com"]
USERNAMES2 = ["user2"]
EMAILS2 = ["user2@email.com"]
USERNAMES3 = ["user3"]
EMAILS3 = ["user3@email.com"]
PASSWORD = "default123"


class Command(BaseCommand):
    help = "Clears "
    def add_arguments(self, parser):
        parser.add_argument('--clear_existing', action="store_true", 
                            help="If true, clears existing data before populating with seed data")

    def handle(self, *args, **options):
        clear_existing = options["clear_existing"]
        
        if clear_existing:
            self.stdout.write("Clearing data...")
            self.clear_data()
            self.stdout.write("Data cleared.")

        self.populate_db()
        self.stdout.write(self.style.SUCCESS("Database populated with seed successfully."))

    def clear_data(self):
        """Clear all existing data and reset sequences."""
        models.Day.objects.all().delete()
        models.Fast.objects.all().delete()
        models.Profile.objects.all().delete()
        models.User.objects.all().delete()
        models.Church.objects.all().delete()

        # Reset sequences using Django's built-in command
        call_command('reset_sequences', '--noinput')

    def populate_db(self):
        date_tomorrow = date.today() + timedelta(days=1)
        date_day_after_tomorrow = date.today() + timedelta(days=2)
        church1, _ = models.Church.objects.get_or_create(name="Church1")
        church2, _ = models.Church.objects.get_or_create(name="Church2")
        church3, _ = models.Church.objects.get_or_create(name="Church3")

        fast1, _ = models.Fast.objects.get_or_create(
            name="Fast 1", 
            church=church1,
            description="your standard fast", 
            culmination_feast="your standard feast",
            culmination_feast_date=date_tomorrow,
            url="https://stjohnarmenianchurch.com/"
        )
        fast2, _ = models.Fast.objects.get_or_create(
            name="Fast 2", 
            church=church2,
            description="a prayerful fast",
            culmination_feast="a wonderful feast",
            culmination_feast_date=date_day_after_tomorrow
        )
        fast3, _ = models.Fast.objects.get_or_create(name="Fast 3", church=church3, description="fast no feast")

        for fast in [fast1, fast2, fast3]:
            models.Day.objects.get_or_create(date=date.today(), fast=fast, church=fast.church)

        self._create_users(USERNAMES1, EMAILS1, church1, [fast1])
        self._create_users(USERNAMES2, EMAILS2, church2, [fast2])
        self._create_users(USERNAMES3, EMAILS3, church3)
        
    def _create_users(self, usernames, emails, church, fasts=None, password=PASSWORD):
        for username, email in zip(usernames, emails):
            user, created = models.User.objects.get_or_create(
                username=username,
                email=email,
            )
            if created:
                user.set_password(password)
                user.save()
            profile, _ = models.Profile.objects.get_or_create(user=user, church=church)
            if fasts is not None:
                profile.fasts.set(fasts)
        