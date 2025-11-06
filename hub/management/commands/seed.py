"""Generates seed data for app with python manage.py seed."""
from datetime import date, timedelta, datetime
from django.utils import timezone

from django.core.management.base import BaseCommand
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist

import hub.models as models
from notifications.models import DeviceToken, PromoEmail
from app_management.models import Changelog
from learning_resources.models import Video, Article, Recipe, Bookmark
from events.models import EventType

# Default prompt template for LLM context generation (Readings)
READING_PROMPT_TEMPLATE = """You are a biblical scholar and theologian providing contextual understanding for scripture readings. 

When provided with a biblical passage reference, provide concise but meaningful context by:
1. Summarizing the key themes and events leading up to the passage
2. Explaining the historical and cultural context
3. Identifying important theological concepts or themes
4. Connecting the passage to broader biblical narratives when relevant

Keep your response focused, informative, and appropriate for both new and experienced readers of scripture. Limit responses to 2-3 paragraphs."""

# Default prompt template for feast context generation
FEAST_PROMPT_TEMPLATE = """You are a knowledgeable guide on Christian feast days, saints, and church history.

When asked about a feast, provide context that helps readers understand:
1. Why the saints or events are commemorated together (if multiple)
2. The historical and spiritual significance of each saint or event
3. The theological themes and lessons from their lives or the feast
4. How this feast connects to broader Christian tradition

Important: Your response must be valid JSON with exactly two fields:
- "short_text": A 2-sentence summary of the feast
- "text": A detailed explanation with multiple paragraphs covering the points above

Return ONLY the JSON object. Do not wrap it in markdown code blocks. Do not add any text before or after the JSON."""

# Sample content for seeding
SAMPLE_CONTEXT = """This passage comes during a pivotal moment in John's Gospel, where Jesus's public ministry is reaching its climax. The context leading up to this passage shows Jesus performing miraculous signs and teaching about the nature of God's love for humanity.

The historical setting is first-century Palestine under Roman occupation, where Jewish communities were struggling with questions of identity and hope. Jesus's words here speak to universal themes of divine love, sacrifice, and the promise of eternal life that transcend cultural and temporal boundaries.

This passage encapsulates one of the central theological themes of Christianity - that God's love is demonstrated through sacrificial action, and that this love extends to all people regardless of their background or status."""

EMAILS1 = ["user1a@email.com", "user1b@email.com"]
NAMES1 = ["User", "User"]
EMAILS2 = ["user2@email.com"]
NAMES2 = ["user O'Usersen-McUser"]
EMAILS3 = ["user3@email.com"]
NAMES3 = [None]
PASSWORD = "default123"


class Command(BaseCommand):
    help = "Clears existing data and populates the database with seed data for all models"

    @transaction.atomic
    def handle(self, *args, **options):
        self.clear_data()
        self.populate_db()
        self.stdout.write(self.style.SUCCESS("Database populated with comprehensive seed data successfully."))

    def clear_data(self):
        """Clear all existing data and reset sequences."""
        from django.db import connection
        
        def table_exists(table_name):
            """Check if a table exists in the database."""
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=%s",
                    [table_name]
                )
                return cursor.fetchone() is not None
        
        def safe_delete(model_class, model_name, table_name):
            """Safely delete all objects from a model, checking if table exists first."""
            if table_exists(table_name):
                model_class.objects.all().delete()
            else:
                self.stdout.write(self.style.WARNING(f"Table {table_name} doesn't exist yet, skipping..."))
        
        # Clear in reverse dependency order
        safe_delete(models.FeastContext, "FeastContext", "hub_feastcontext")
        safe_delete(models.Feast, "Feast", "hub_feast")
        safe_delete(models.ReadingContext, "ReadingContext", "hub_readingcontext")
        safe_delete(models.Reading, "Reading", "hub_reading")
        safe_delete(models.Devotional, "Devotional", "hub_devotional")
        safe_delete(models.DevotionalSet, "DevotionalSet", "hub_devotionalset")
        safe_delete(models.FastParticipantMap, "FastParticipantMap", "hub_fastparticipantmap")
        safe_delete(models.Day, "Day", "hub_day")
        safe_delete(models.Fast, "Fast", "hub_fast")
        safe_delete(models.Profile, "Profile", "hub_profile")
        safe_delete(models.User, "User", "auth_user")
        safe_delete(models.Church, "Church", "hub_church")
        safe_delete(models.LLMPrompt, "LLMPrompt", "hub_llmprompt")
        safe_delete(models.GeocodingCache, "GeocodingCache", "hub_geocodingcache")
        
        # Clear other apps
        safe_delete(DeviceToken, "DeviceToken", "notifications_devicetoken")
        safe_delete(PromoEmail, "PromoEmail", "notifications_promoemail")
        safe_delete(Changelog, "Changelog", "app_management_changelog")

        # Clear learning resources (bookmarks first due to foreign keys)
        safe_delete(Bookmark, "Bookmark", "learning_resources_bookmark")
        safe_delete(Video, "Video", "learning_resources_video")
        safe_delete(Article, "Article", "learning_resources_article")
        safe_delete(Recipe, "Recipe", "learning_resources_recipe")

        # Clear events data
        safe_delete(EventType, "EventType", "events_eventtype")

    def populate_db(self):
        # Create Churches
        churches = [
            models.Church.objects.create(name="Armenian Apostolic Church", pk=1),
        ]

        # Create LLM Prompts
        llm_prompts = [
            # Reading prompts
            models.LLMPrompt.objects.create(
                model="gpt-4o-mini",
                role="You are a helpful assistant providing concise biblical context.",
                prompt=READING_PROMPT_TEMPLATE,
                applies_to="readings",
                active=True,
            ),
            models.LLMPrompt.objects.create(
                model="claude-3-5-sonnet-20241022",
                role="You are a biblical scholar and theologian.",
                prompt="Provide scholarly biblical context for the given passage.",
                applies_to="readings",
                active=False,
            ),
            # Feast prompts
            models.LLMPrompt.objects.create(
                model="gpt-4o-mini",
                role="You are a knowledgeable guide on Christian feast days and saints.",
                prompt=FEAST_PROMPT_TEMPLATE,
                applies_to="feasts",
                active=True,
            ),
            models.LLMPrompt.objects.create(
                model="claude-haiku-4-5-20251001",
                role="You are a church historian and hagiographer.",
                prompt=FEAST_PROMPT_TEMPLATE,
                applies_to="feasts",
                active=False,
            ),
        ]

        # Create Fasts
        fasts = [
            models.Fast.objects.create(
                name="Lenten Fast",
                church=churches[0],
                description="The Great Lent preparation for Easter",
                culmination_feast="Easter Sunday",
                culmination_feast_date=date.today() + timedelta(days=40),
                url="https://stjohnarmenianchurch.com/lent"
            ),
            models.Fast.objects.create(
                name="Fast of the Catechumens", 
                church=churches[0],
                description="Preparation for the Nativity of Christ",
                culmination_feast="Christmas Day",
                culmination_feast_date=date.today() + timedelta(days=28)
            ),
            models.Fast.objects.create(
                name="Assumption Fast", 
                church=churches[0],
                description="Fast leading to the Assumption of the Virgin Mary",
                culmination_feast="Feast of the Assumption",
                culmination_feast_date=date.today() + timedelta(days=14)
            ),
        ]

        # Create Days for each Fast with non-overlapping dates
        all_days = []
        day_offset = 0  # Track the starting day offset for each fast
        
        for n, fast in enumerate(fasts):
            fast_days = []
            num_days = (n + 1) * 7  # Different durations for each fast (7, 14, 21)
            
            for i in range(num_days):
                day = models.Day.objects.create(
                    date=date.today() + timedelta(days=day_offset + i), 
                    fast=fast, 
                    church=fast.church
                )
                fast_days.append(day)
                all_days.append(day)
            
            day_offset += num_days  # Move offset forward for next fast
            fast.save(update_fields=["year"])  # saving fast with day(s) updates the year field

        # Create Users and Profiles
        users_and_profiles = []
        users_and_profiles.extend(self._create_users(EMAILS1, NAMES1, churches[0], fasts=[fasts[0]]))
        users_and_profiles.extend(self._create_users(EMAILS2, NAMES2, churches[0], fasts=[fasts[1]]))
        users_and_profiles.extend(self._create_users(EMAILS3, NAMES3, churches[0]))

        # Create Learning Resources
        videos = self._create_videos()
        articles = self._create_articles()
        recipes = self._create_recipes()

        # Create Devotional Sets and Devotionals
        devotional_sets = self._create_devotional_sets_and_devotionals(all_days, videos, fasts)

        # Create Bookmarks
        users = [up[0] for up in users_and_profiles]
        self._create_bookmarks(users, videos, articles, recipes, devotional_sets)

        # Create Readings and Reading Contexts
        readings = self._create_readings(all_days)
        self._create_reading_contexts(readings, llm_prompts)

        # Create Feasts and Feast Contexts
        feasts = self._create_feasts(all_days)
        self._create_feast_contexts(feasts, llm_prompts)

        # Create Fast Participant Maps
        self._create_fast_participant_maps(fasts)

        # Create Geocoding Cache entries
        self._create_geocoding_cache()

        # Create Notification models
        self._create_device_tokens(users)
        self._create_promo_emails(churches, fasts, users)

        # Create App Management models
        self._create_changelogs()

        # Initialize Event Types
        self._init_event_types()

        self.stdout.write(self.style.SUCCESS("All models populated with seed data."))

    @transaction.atomic        
    def _create_users(self, emails, names, church, fasts=None):
        users_and_profiles = []
        for email, name in zip(emails, names):
            user = models.User.objects.create_user(username=email, email=email, password=PASSWORD)
            profile = models.Profile.objects.create(
                user=user, 
                name=name, 
                church=church,
                location=f"City in {church.name} region",
                latitude=40.7128 + len(users_and_profiles) * 0.1,  # Vary coordinates
                longitude=-74.0060 + len(users_and_profiles) * 0.1,
                receive_upcoming_fast_reminders=True,
                receive_promotional_emails=True
            )
            users_and_profiles.append((user, profile))

        if fasts is not None:
            for user, profile in users_and_profiles:
                profile.fasts.set(fasts)
                profile.save()
        
        return users_and_profiles

    def _create_videos(self):
        """Create sample videos for learning resources."""
        videos = [
            Video.objects.create(
                title="Morning Prayer Guide",
                description="A comprehensive guide to traditional morning prayers",
                category="morning_prayers",
            ),
            Video.objects.create(
                title="Evening Prayer Reflection",
                description="Peaceful evening prayer session with reflection",
                category="evening_prayers",
            ),
            Video.objects.create(
                title="Daily Devotional - Hope",
                description="A short devotional on the theme of hope in scripture",
                category="devotional",
            ),
            Video.objects.create(
                title="Fasting Practices Tutorial",
                description="Understanding traditional fasting practices and their spiritual significance",
                category="tutorial",
            ),
        ]
        return videos

    def _create_articles(self):
        """Create sample articles."""
        articles = [
            Article.objects.create(
                title="The Spiritual Significance of Fasting",
                body="# Understanding Fasting\n\nFasting has been a cornerstone of spiritual practice across many traditions...\n\n## Historical Context\n\nThe practice of fasting dates back thousands of years...",
            ),
            Article.objects.create(
                title="Prayer in Daily Life",
                body="# Incorporating Prayer\n\nPrayer is not just for designated times, but can be woven throughout our daily activities...\n\n## Practical Tips\n\n1. Start with short prayers\n2. Find quiet moments\n3. Use traditional prayers as guides",
            ),
        ]
        return articles

    def _create_recipes(self):
        """Create sample fasting-friendly recipes."""
        recipes = [
            Recipe.objects.create(
                title="Traditional Lentil Soup",
                description="A hearty and nutritious soup perfect for fasting periods",
                time_required="45 minutes",
                serves="4-6 people",
                ingredients="* 1 cup red lentils\n* 2 onions, diced\n* 3 cloves garlic\n* 4 cups vegetable broth\n* 1 tsp cumin\n* Salt and pepper to taste",
                directions="1. Sauté onions and garlic\n2. Add lentils and broth\n3. Simmer for 25 minutes\n4. Season with cumin, salt, and pepper\n5. Serve hot",
            ),
            Recipe.objects.create(
                title="Vegetable Pilaf",
                description="A flavorful rice dish with mixed vegetables",
                time_required="30 minutes",
                serves="4 people",
                ingredients="* 1 cup basmati rice\n* 2 cups vegetable broth\n* 1 cup mixed vegetables\n* 1 onion, diced\n* 2 tbsp olive oil\n* Herbs and spices",
                directions="1. Heat oil in a pan\n2. Sauté onion until golden\n3. Add rice and stir\n4. Add broth and vegetables\n5. Cover and simmer for 18 minutes",
            ),
        ]
        return recipes

    def _create_bookmarks(self, users, videos, articles, recipes, devotional_sets):
        """Create sample bookmarks for users."""
        from django.contrib.contenttypes.models import ContentType
        import random

        # Sample bookmark notes based on content type
        video_notes = [
            "Great for morning prayers",
            "Very inspiring content",
            "Want to watch this again",
            "Shared with my prayer group",
            "Excellent spiritual guidance"
        ]

        article_notes = [
            "Thought-provoking insights",
            "Helpful for Bible study",
            "Want to reference this later",
            "Great practical advice",
            "Important spiritual reading"
        ]

        recipe_notes = [
            "Perfect for fasting period",
            "Family loves this recipe",
            "Great for community meals",
            "Easy to prepare",
            "Traditional and delicious"
        ]

        devotional_notes = [
            "Following this devotional series",
            "Great for spiritual growth",
            "Doing this with my family",
            "Perfect timing for me",
            "Very meaningful content"
        ]

        # Combine all content
        all_content = list(videos) + list(articles) + list(recipes) + list(devotional_sets)

        # Create bookmarks for each user
        for user in users:
            # Each user bookmarks 3-7 random items
            num_bookmarks = random.randint(3, min(7, len(all_content)))
            bookmarked_content = random.sample(all_content, num_bookmarks)

            for content in bookmarked_content:
                # Select appropriate note based on content type
                if isinstance(content, Video):
                    note = random.choice(video_notes)
                elif isinstance(content, Article):
                    note = random.choice(article_notes)
                elif isinstance(content, Recipe):
                    note = random.choice(recipe_notes)
                elif hasattr(content, 'title') and 'devotional' in content.title.lower():
                    note = random.choice(devotional_notes)
                else:
                    note = "Bookmarked for later reference"

                # Create bookmark
                Bookmark.objects.create(
                    user=user,
                    content_type=ContentType.objects.get_for_model(content),
                    object_id=content.id,
                    note=note
                )

        total_bookmarks = Bookmark.objects.count()
        self.stdout.write(f"Created {total_bookmarks} bookmarks across {len(users)} users")

    def _create_devotional_sets_and_devotionals(self, days, videos, fasts):
        """Create devotional sets and individual devotionals."""
        devotional_sets = [
            models.DevotionalSet.objects.create(
                title="Lenten Journey",
                description="A 40-day journey through Lent with daily reflections",
                fast=fasts[0]  # Lenten Fast
            ),
            models.DevotionalSet.objects.create(
                title="Advent Reflections",
                description="Daily reflections for the Advent season",
                fast=fasts[1]  # Fast of the Catechumens
            ),
        ]

        # Create devotionals for some days
        for i, day in enumerate(days[:6]):  # Create devotionals for first 6 days
            video = videos[i % len(videos)]
            
            models.Devotional.objects.create(
                day=day,
                description=f"Daily reflection for {day.date.strftime('%B %d')}",
                video=video,
                order=(i % 3) + 1,
            )

        return devotional_sets

    def _create_readings(self, days):
        """Create Bible readings for days."""
        readings = []
        bible_books = ["Genesis", "Exodus", "Matthew", "Mark", "Luke", "John", "Romans", "Psalms"]
        
        for i, day in enumerate(days[:10]):  # Create readings for first 10 days
            book = bible_books[i % len(bible_books)]
            chapter = (i % 5) + 1
            start_verse = (i % 10) + 1
            end_verse = start_verse + (i % 5) + 1
            
            reading = models.Reading.objects.create(
                day=day,
                book=book,
                start_chapter=chapter,
                start_verse=start_verse,
                end_chapter=chapter,
                end_verse=end_verse,
            )
            readings.append(reading)
        
        return readings

    def _create_reading_contexts(self, readings, llm_prompts):
        """Create reading contexts for readings."""
        # Filter to get only reading prompts
        reading_prompts = [p for p in llm_prompts if p.applies_to == "readings"]
        
        for i, reading in enumerate(readings):
            models.ReadingContext.objects.create(
                reading=reading,
                text=f"{SAMPLE_CONTEXT}\n\nThis specific context is for {reading.passage_reference}.",
                prompt=reading_prompts[i % len(reading_prompts)] if reading_prompts else None,
                thumbs_up=i % 3,
                thumbs_down=max(0, (i % 4) - 2),
                active=True,
            )

    def _create_feasts(self, days):
        """Create sample feasts for some days."""
        feasts = []
        feast_names = [
            ("Feast of the Nativity", "Ծննդյան տոն"),
            ("Feast of the Holy Archangels", "Սուրբ հրեշտակապետների տոն"),
            ("Saint Stephen the Proto-Martyr", "Սուրբ Ստեփաննոս Նախավկա"),
            ("Feast of Epiphany", "Աստվածայայտնություն"),
            ("Presentation of Jesus at the Temple", "Տեառնընդառաջ"),
        ]
        
        for i, day in enumerate(days[:5]):  # Create feasts for first 5 days
            name_en, name_hy = feast_names[i % len(feast_names)]
            
            feast = models.Feast.objects.create(
                day=day,
                name=name_en,
            )
            feast.name_hy = name_hy
            feast.save(update_fields=['i18n'])
            feasts.append(feast)
        
        return feasts

    def _create_feast_contexts(self, feasts, llm_prompts):
        """Create feast contexts for feasts."""
        # Filter to get only feast prompts
        feast_prompts = [p for p in llm_prompts if p.applies_to == "feasts"]
        
        sample_short_text = "This feast commemorates important saints and events in Christian tradition. It serves as a reminder of God's work through faithful servants."
        sample_text = """This feast day celebrates significant figures in Christian history who devoted their lives to serving God and the Church.

**Historical Significance:**
The saints commemorated on this day represent different periods and regions of early Christianity, demonstrating the universal nature of Christian witness across time and geography. Their lives exemplify various aspects of Christian devotion, from martyrdom to pastoral leadership.

**Spiritual Lessons:**
These saints teach us about perseverance in faith, the importance of spiritual leadership, and the power of witness in the face of adversity. Their example continues to inspire Christians today to live lives of dedication and service."""
        
        for i, feast in enumerate(feasts):
            models.FeastContext.objects.create(
                feast=feast,
                text=f"{sample_text}\n\nThis specific context is for {feast.name}.",
                short_text=f"{sample_short_text}",
                prompt=feast_prompts[i % len(feast_prompts)] if feast_prompts else None,
                thumbs_up=i % 2,
                thumbs_down=max(0, (i % 3) - 1),
                active=True,
            )

    def _create_fast_participant_maps(self, fasts):
        """Create participant maps for fasts."""
        for i, fast in enumerate(fasts):
            models.FastParticipantMap.objects.create(
                fast=fast,
                participant_count=50 + (i * 25),
                format="svg",
            )

    def _create_geocoding_cache(self):
        """Create geocoding cache entries."""
        locations = [
            ("New York, NY", 40.7128, -74.0060),
            ("Los Angeles, CA", 34.0522, -118.2437),
            ("Chicago, IL", 41.8781, -87.6298),
            ("Houston, TX", 29.7604, -95.3698),
            ("Phoenix, AZ", 33.4484, -112.0740),
        ]
        
        for location_text, lat, lng in locations:
            models.GeocodingCache.objects.create(
                location_text=location_text,
                latitude=lat,
                longitude=lng,
            )

    def _create_device_tokens(self, users):
        """Create device tokens for users."""
        device_types = [DeviceToken.IOS, DeviceToken.ANDROID, DeviceToken.WEB]
        
        for i, user in enumerate(users[:4]):  # Create tokens for first 4 users
            DeviceToken.objects.create(
                user=user,
                token=f"ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx{i:02d}]",
                device_type=device_types[i % len(device_types)],
                is_active=True,
            )

    def _create_promo_emails(self, churches, fasts, users):
        """Create promotional emails."""
        promo_emails = [
            PromoEmail.objects.create(
                title="Welcome to Fast and Pray",
                subject="Welcome to our spiritual community",
                content_html="<h1>Welcome!</h1><p>We're excited to have you join our community...</p>",
                content_text="Welcome! We're excited to have you join our community...",
                status=PromoEmail.SENT,
                all_users=True,
                sent_at=timezone.now() - timedelta(days=1),
            ),
            PromoEmail.objects.create(
                title="Lenten Fast Reminder",
                subject="Lenten Fast begins soon",
                content_html="<h2>Prepare for Lent</h2><p>The Lenten fast will begin in one week...</p>",
                content_text="Prepare for Lent. The Lenten fast will begin in one week...",
                status=PromoEmail.SCHEDULED,
                church_filter=churches[0],
                joined_fast=fasts[0],
                scheduled_for=timezone.now() + timedelta(days=3),
            )
        ]

        # Add selected users to promotional emails
        if users:
            # No longer have a third email to add users to
            pass

    def _create_changelogs(self):
        """Create changelog entries."""
        changelogs = [
            Changelog.objects.create(
                title="Initial Release",
                description="# Version 1.0.0\n\n## Features\n- User registration and profiles\n- Fast participation\n- Daily readings\n- Prayer resources",
                version="1.0.0",
            ),
            Changelog.objects.create(
                title="Enhanced Notifications",
                description="# Version 1.1.0\n\n## New Features\n- Push notifications for fast reminders\n- Email preferences\n- Device token management\n\n## Bug Fixes\n- Fixed user profile image uploads",
                version="1.1.0",
            ),
            Changelog.objects.create(
                title="Learning Resources Added",
                description="# Version 1.2.0\n\n## Major Updates\n- Video library\n- Articles and recipes\n- Devotional content\n- Reading contexts with AI-generated insights",
                version="1.2.0",
            ),
        ]

    def _init_event_types(self):
        """Initialize default event types for the events app."""
        self.stdout.write("Initializing event types...")

        created_types = EventType.get_or_create_default_types()

        if created_types:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Created {len(created_types)} new event types:'
                )
            )
            for event_type in created_types:
                self.stdout.write(f'  - {event_type.name} ({event_type.code})')
        else:
            self.stdout.write(
                self.style.WARNING('All default event types already exist.')
            )

        # Display summary
        all_types = EventType.objects.all().order_by('category', 'name')
        self.stdout.write(f'\nEvent types initialized: {all_types.count()} total types')

        self.stdout.write(
            self.style.SUCCESS('Event types initialization complete!')
        )