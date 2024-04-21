"""Generates seed data for app with python manage.py seed."""
from datetime import date

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
        models.Day.objects.all().delete()

    def populate_db(self):
        church1, _ = models.Church.objects.get_or_create(name="Church1")
        church2, _ = models.Church.objects.get_or_create(name="Church2")
        church3, _ = models.Church.objects.get_or_create(name="Church3")

        fast1, _ = models.Fast.objects.get_or_create(name="Fast 1", church=church1)
        fast2, _ = models.Fast.objects.get_or_create(name="Fast 2", church=church2)
        fast3, _ = models.Fast.objects.get_or_create(name="Fast 3", church=church3)


        today, _ = models.Day.objects.get_or_create(date=date.today())
        today.fasts.set([fast1, fast2, fast3])

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

        