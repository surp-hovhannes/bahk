from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import activate
from hub.models import Church, Fast, Day, DevotionalSet, Devotional
from learning_resources.models import Video, Article, Recipe
from events.models import Announcement, UserActivityFeed


class Command(BaseCommand):
    help = "Seed initial multilingual data for development (en and hy)"

    def handle(self, *args, **options):
        activate('en')

        church, _ = Church.objects.get_or_create(name="Armenian Apostolic Church")

        # Create Fast with translations
        fast = Fast.objects.create(
            name="Great Lent",
            description="A period of fasting and prayer before Easter.",
            culmination_feast="Easter",
            culmination_feast_date=timezone.now().date() + timezone.timedelta(days=40),
            church=church,
        )
        # Armenian translations
        fast.name_hy = "Մեծ Պահք"
        fast.description_hy = "Պահքի և աղոթքի շրջան՝ Զատिकից առաջ։"
        fast.culmination_feast_hy = "Զատիկ"
        fast.save()

        # Create days
        base_date = timezone.now().date()
        day1 = Day.objects.create(date=base_date, fast=fast, church=church)

        # DevotionalSet with translations
        dset = DevotionalSet.objects.create(
            title="Daily Devotionals",
            description="Short daily reflections",
            fast=fast,
        )
        dset.title_hy = "Օրական Նվիրումներ"
        dset.description_hy = "Կարճ ամենօրյա խորհրդածություններ"
        dset.save()

        # Videos EN and HY
        video_en = Video.objects.create(
            title="Day 1 Reflection",
            description="Introduction to the fast",
            category='devotional',
            language_code='en',
        )
        video_en.title_hy = "Օր 1 Խորհրդածություն"
        video_en.description_hy = "Ներածություն պահքին"
        video_en.save()

        video_hy = Video.objects.create(
            title="Օր 1 Խորհրդածություն",
            description="Ներածություն պահքին (HY)",
            category='devotional',
            language_code='hy',
        )
        video_hy.title_en = "Day 1 Reflection (HY)"
        video_hy.description_en = "Introduction to the fast"
        video_hy.save()

        # Devotionals EN and HY
        devo_en = Devotional.objects.create(
            day=day1,
            description="Blessed are those who fast with a pure heart.",
            video=video_en,
            order=1,
            language_code='en',
        )
        devo_en.description_hy = "Երափակված են նրանք, ովքեր պահք են պահում մաքուր սրտով։"
        devo_en.save()

        devo_hy = Devotional.objects.create(
            day=day1,
            description="Երափակված են նրանք, ովքեր պահք են պահում մաքուր սրտով։",
            video=video_hy,
            order=1,
            language_code='hy',
        )
        devo_hy.description_en = "Blessed are those who fast with a pure heart."
        devo_hy.save()

        # Article with translations
        article = Article.objects.create(
            title="Fasting Basics",
            body="Markdown: Fasting is a spiritual discipline...",
        )
        article.title_hy = "Պահքի հիմունքներ"
        article.body_hy = "Markdown: Պահքն հոգևոր կարգապահություն է..."
        article.save()

        # Recipe with translations
        recipe = Recipe.objects.create(
            title="Lentil Soup",
            description="A hearty soup.",
            time_required="30 minutes",
            serves="4",
            ingredients="- Lentils\n- Onion\n- Water",
            directions="Boil and season.",
        )
        recipe.title_hy = "Ոսպի ապուր"
        recipe.description_hy = "Հագեցած ապուր։"
        recipe.time_required_hy = "30 րոպե"
        recipe.serves_hy = "4"
        recipe.ingredients_hy = "- Ոսպ\n- Սոխ\n- Ջուր"
        recipe.directions_hy = "Եփել և համեմել։"
        recipe.save()

        # Announcement and Activity Feed with translations
        announcement = Announcement.objects.create(
            title="Welcome to Great Lent",
            description="Join us in prayer and fasting.",
            status='published',
        )
        announcement.title_hy = "Բարի գալուստ Մեծ Պահք"
        announcement.description_hy = "Միացե՛ք մեզ աղոթքի և պահքի մեջ։"
        announcement.save()

        # Seed a sample UserActivityFeed title/description via model (no user binding)
        # Typically these are created from events; here we simply ensure translations work
        # Skipping creation of a feed item without a user

        self.stdout.write(self.style.SUCCESS("Seeded multilingual data for en and hy."))

