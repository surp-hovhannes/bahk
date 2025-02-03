from django.core.management.base import BaseCommand
from django.utils import timezone
from hub.models import Profile, Fast
from learning_resources.models import Video, Article

class Command(BaseCommand):
    help = 'Updates cached thumbnail URLs for all models with images'

    def handle(self, *args, **kwargs):
        # Update Profile thumbnails
        profiles = Profile.objects.filter(profile_image__isnull=False)
        self.stdout.write(f"Updating {profiles.count()} profile thumbnails...")
        for profile in profiles:
            try:
                profile.save()  # This will trigger thumbnail caching
                self.stdout.write(f"✓ Updated thumbnail for profile {profile.id}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Error updating profile {profile.id}: {str(e)}"))

        # Update Fast thumbnails
        fasts = Fast.objects.filter(image__isnull=False)
        self.stdout.write(f"Updating {fasts.count()} fast thumbnails...")
        for fast in fasts:
            try:
                fast.save()
                self.stdout.write(f"✓ Updated thumbnail for fast {fast.id}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Error updating fast {fast.id}: {str(e)}"))

        # Update Video thumbnails
        videos = Video.objects.filter(thumbnail__isnull=False)
        self.stdout.write(f"Updating {videos.count()} video thumbnails...")
        for video in videos:
            try:
                video.save()
                self.stdout.write(f"✓ Updated thumbnail for video {video.id}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Error updating video {video.id}: {str(e)}"))

        # Update Article thumbnails
        articles = Article.objects.filter(image__isnull=False)
        self.stdout.write(f"Updating {articles.count()} article thumbnails...")
        for article in articles:
            try:
                article.save()
                self.stdout.write(f"✓ Updated thumbnail for article {article.id}")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"✗ Error updating article {article.id}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("Finished updating all thumbnail caches")) 