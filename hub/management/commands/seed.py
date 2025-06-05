"""Generates seed data for app with python manage.py seed."""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist

import hub.models as models

# Default prompt template for LLM context generation
PROMPT_TEMPLATE = """You are a biblical scholar and theologian providing contextual understanding for scripture readings. 

When provided with a biblical passage reference, provide concise but meaningful context by:
1. Summarizing the key themes and events leading up to the passage
2. Explaining the historical and cultural context
3. Identifying important theological concepts or themes
4. Connecting the passage to broader biblical narratives when relevant

Keep your response focused, informative, and appropriate for both new and experienced readers of scripture. Limit responses to 2-3 paragraphs."""


EMAILS1 = ["user1a@email.com", "user1b@email.com"]
NAMES1 = ["User", "User"]
EMAILS2 = ["user2@email.com"]
NAMES2 = ["user O'Usersen-McUser"]
EMAILS3 = ["user3@email.com"]
NAMES3 = [None]
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
            models.Church.objects.create(name="Armenian Apostolic Church", pk=1),
            models.Church.objects.create(name="Church2", pk=2),
            models.Church.objects.create(name="Church3", pk=3),
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

        self._create_users(EMAILS1, NAMES1, churches[0], fasts=[fasts[0]])
        self._create_users(EMAILS2, NAMES2, churches[1], fasts=[fasts[1]])
        self._create_users(EMAILS3, NAMES3, churches[2])

        # Seed a default LLMPrompt if none exists
        try:
            # Check if any LLMPrompt exists at all first
            models.LLMPrompt.objects.first()
            self.stdout.write("LLMPrompt(s) already exist, skipping seeding.")
        except ObjectDoesNotExist:
             # Or use filter(active=True).exists() if you only want to skip if an *active* one exists
             # if not models.LLMPrompt.objects.filter(active=True).exists():
             self.stdout.write("No LLMPrompt found, seeding a default active prompt.")
             models.LLMPrompt.objects.create(
                 model="gpt-4o-mini", # Or your preferred default model
                 role="You are a helpful assistant providing concise biblical context.",
                 prompt=PROMPT_TEMPLATE,
                 active=True,
                 description="Default prompt seeded by management command.",
             )

    @transaction.atomic        
    def _create_users(self, emails, names, church, fasts=None):
        users = []
        profiles = []
        for email, name in zip(emails, names):
            user = models.User.objects.create_user(username=email, email=email, password=PASSWORD)
            users.append(user)
            profile = models.Profile.objects.create(user=user, name=name, church=church)
            profiles.append(profile)

        if fasts is not None:
            for profile in profiles:
                profile.fasts.set(fasts)
                profile.save()
        