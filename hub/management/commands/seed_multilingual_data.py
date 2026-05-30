from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils.translation import activate
from hub.models import Church, Fast, Day, DevotionalSet, Devotional
from learning_resources.models import Video, Article, Recipe
from events.models import Announcement


SEED_FAST_YEAR = 2025
SEED_FAST_START_DATE = date(SEED_FAST_YEAR, 3, 3)
SEED_FAST_FEAST_DATE = date(SEED_FAST_YEAR, 4, 20)


def update_first_or_create(model, lookup, defaults):
    instance = model.objects.filter(**lookup).order_by("pk").first()
    if instance is None:
        values = {**lookup, **defaults}
        return model.objects.create(**values), True

    for field, value in defaults.items():
        setattr(instance, field, value)
    instance.save()
    return instance, False


class Command(BaseCommand):
    help = "Seed initial multilingual data for development (en and hy)"

    def handle(self, *args, **options):
        activate('en')

        church, _ = Church.objects.get_or_create(name="Armenian Apostolic Church")
        feast_date = SEED_FAST_FEAST_DATE
        while (
            Fast.objects.filter(
                church=church, culmination_feast_date=feast_date
            )
            .exclude(name="Great Lent", year=SEED_FAST_YEAR)
            .exists()
        ):
            feast_date += timedelta(days=1)

        # Create or update Fast with translations
        fast, _ = Fast.objects.update_or_create(
            name="Great Lent",
            church=church,
            year=SEED_FAST_YEAR,
            defaults={
                "description": "A period of fasting and prayer before Easter.",
                "culmination_feast": "Easter",
                "culmination_feast_date": feast_date,
            },
        )
        # Armenian translations
        fast.name_hy = "Մեծ Պահք"
        fast.description_hy = "Պահքի և աղոթքի շրջան՝ Զատिकից առաջ։"
        fast.culmination_feast_hy = "Զատիկ"
        fast.save()

        # Create days
        day1, _ = Day.objects.get_or_create(
            date=SEED_FAST_START_DATE, fast=fast, church=church
        )
        fast.save(update_fields=["year"])

        # DevotionalSet with translations
        dset, _ = DevotionalSet.objects.update_or_create(
            title="Daily Devotionals",
            fast=fast,
            defaults={"description": "Short daily reflections"},
        )
        dset.title_hy = "Օրական Նվիրումներ"
        dset.description_hy = "Կարճ ամենօրյա խորհրդածություններ"
        dset.save()

        # Videos EN and HY
        video_en, _ = update_first_or_create(
            Video,
            {
                "category": "devotional",
                "language_code": "en",
                "description": "Introduction to the fast",
            },
            {
                "title": "Day 1 Reflection",
            },
        )
        video_en.title_hy = "Օր 1 Խորհրդածություն"
        video_en.description_hy = "Ներածություն պահքին"
        video_en.save()

        video_hy, _ = update_first_or_create(
            Video,
            {
                "category": "devotional",
                "language_code": "hy",
                "title_en": "Day 1 Reflection (HY)",
            },
            {
                "description_en": "Introduction to the fast",
            },
        )
        video_hy.title_en = "Day 1 Reflection (HY)"
        video_hy.description_en = "Introduction to the fast"
        video_hy.title_hy = "Օր 1 Խորհրդածություն"
        video_hy.description_hy = "Ներածություն պահքին (HY)"
        video_hy.save()

        # Devotionals EN and HY
        devo_en, _ = Devotional.objects.update_or_create(
            day=day1,
            order=1,
            language_code='en',
            defaults={
                "description": "Blessed are those who fast with a pure heart.",
                "video": video_en,
            },
        )
        devo_en.description_hy = "Երափակված են նրանք, ովքեր պահք են պահում մաքուր սրտով։"
        devo_en.save()

        devo_hy, _ = Devotional.objects.update_or_create(
            day=day1,
            order=1,
            language_code='hy',
            defaults={
                "description": "Երափակված են նրանք, ովքեր պահք են պահում մաքուր սրտով։",
                "video": video_hy,
            },
        )
        devo_hy.description_en = "Blessed are those who fast with a pure heart."
        devo_hy.save()

        # Article with translations
        article, _ = update_first_or_create(
            Article,
            {
                "title": "Fasting Basics",
                "body": "Markdown: Fasting is a spiritual discipline...",
            },
            {},
        )
        article.title_hy = "Պահքի հիմունքներ"
        article.body_hy = "Markdown: Պահքն հոգևոր կարգապահություն է..."
        article.save()

        # Recipe with translations
        recipe, _ = update_first_or_create(
            Recipe,
            {
                "title": "Lentil Soup",
                "ingredients": "- Lentils\n- Onion\n- Water",
            },
            {
                "description": "A hearty soup.",
                "time_required": "30 minutes",
                "serves": "4",
                "directions": "Boil and season.",
            },
        )
        recipe.title_hy = "Ոսպի ապուր"
        recipe.description_hy = "Հագեցած ապուր։"
        recipe.time_required_hy = "30 րոպե"
        recipe.serves_hy = "4"
        recipe.ingredients_hy = "- Ոսպ\n- Սոխ\n- Ջուր"
        recipe.directions_hy = "Եփել և համեմել։"
        recipe.save()

        # Announcement and Activity Feed with translations
        announcement, _ = update_first_or_create(
            Announcement,
            {
                "title": "Welcome to Great Lent",
                "description": "Join us in prayer and fasting.",
            },
            {
                "description": "Join us in prayer and fasting.",
                "status": 'published',
            },
        )
        announcement.title_hy = "Բարի գալուստ Մեծ Պահք"
        announcement.description_hy = "Միացե՛ք մեզ աղոթքի և պահքի մեջ։"
        announcement.save()

        # Seed a sample UserActivityFeed title/description via model (no user binding)
        # Typically these are created from events; here we simply ensure translations work
        # Skipping creation of a feed item without a user

        self.stdout.write(self.style.SUCCESS("Seeded multilingual data for en and hy."))
