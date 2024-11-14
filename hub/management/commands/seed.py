"""Generates seed data for app with python manage.py seed."""
from datetime import date, timedelta

from django.core.management.base import BaseCommand

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
        models.User.objects.all().delete()
        models.Church.objects.all().delete()

    def populate_db(self):
        date_tomorrow = date.today() + timedelta(days=1)
        date_day_after_tomorrow = date.today() + timedelta(days=2)
        church1, _ = models.Church.objects.get_or_create(name="Church1")
        church2, _ = models.Church.objects.get_or_create(name="Church2")
        church3, _ = models.Church.objects.get_or_create(name="Church3")

        try:
            fast1 = models.Fast.objects.get(name="Fast 1", church=church1)
        except:
            fast1 = models.Fast.objects.create(
                name="Fast 1", 
                church=church1,
                description="your standard fast", 
                culmination_feast="your standard feast",
                culmination_feast_date=date_tomorrow,
                url="https://stjohnarmenianchurch.com/",
            )
        try:
            fast2 = models.Fast.objects.get(name="Fast 2", church=church2)
        except:
            fast2 = models.Fast.objects.create(
                name="Fast 2", 
                church=church2,
                description="a prayerful fast",
                culmination_feast="a wonderful feast",
                culmination_feast_date=date_day_after_tomorrow
            )
        try:
            fast3 = models.Fast.objects.get(name="Fast 3", church=church3)
        except:
            fast3 = models.Fast.objects.create(name="Fast 3", church=church3, description="fast no feast")

        for fast in [fast1, fast2, fast3]:
            try:
                models.Day.objects.get(date=date.today(), fast=fast, church=fast.church)
            except models.Day.DoesNotExist:
                models.Day.objects.create(date=date.today(), fast=fast, church=fast.church)

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
        