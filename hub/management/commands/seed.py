"""Generates seed data for app with python manage.py seed."""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

import hub.models as models


EMAILS1 = ["user1a@email.com", "user1b@email.com"]
EMAILS2 = ["user2@email.com"]
EMAILS3 = ["user3@email.com"]
PASSWORD = "default123"


class Command(BaseCommand):
    help = "Clears existing data and populates the database with seed data"

    @transaction.atomic
    def handle(self, *args, **options):
        self.clear_data()
        self.populate_db()
        self.stdout.write(self.style.SUCCESS("Database populated with seed data successfully."))

    def clear_data(self):
        """Clear all existing data and reset sequences."""
        models.Day.objects.all().delete()
        models.Fast.objects.all().delete()
        models.Profile.objects.all().delete()
        models.User.objects.all().delete()
        models.Church.objects.all().delete()

    def populate_db(self):
        churches = [
            models.Church.objects.create(name="Armenian Apostolic Church"),
            models.Church.objects.create(name="Church2"),
            models.Church.objects.create(name="Church3"),
        ]

        fasts = [
            models.Fast.objects.create(
                name="Fast 1",
                church=churches[0],
                description="your standard fast",
                culmination_feast="your standard feast",
                culmination_feast_date=date.today() + timedelta(days=1),
                url="https://stjohnarmenianchurch.com/"
            ),
            models.Fast.objects.create(
                name="Fast 2", 
                church=churches[1],
                description="a prayerful fast",
                culmination_feast="a wonderful feast",
                culmination_feast_date=date.today() + timedelta(days=2)
            ),
            models.Fast.objects.create(name="Fast 3", church=churches[2], description="fast no feast"),
        ]

        for n, fast in enumerate(fasts):
            for i in range(n + 1):  # number of fast is number of days it lasts
                day = models.Day.objects.create(date=date.today() + timedelta(days=i), fast=fast, church=fast.church)
                fast.save(update_fields=["year"])  # saving fast with day(s) updates the year field

        self._create_users(EMAILS1, churches[0], fasts=[fasts[0]])
        self._create_users(EMAILS2, churches[1], fasts=[fasts[1]])
        self._create_users(EMAILS3, churches[2])

    @transaction.atomic        
    def _create_users(self, emails, church, fasts=None):
        users = []
        profiles = []
        for email in zip(emails):
            user = models.User.objects.create_user(username=email, email=email, password=PASSWORD)
            users.append(user)
            profile = models.Profile.objects.create(user=user, church=church)
            profiles.append(profile)

        if fasts is not None:
            for profile in profiles:
                profile.fasts.set(fasts)
                profile.save()
        