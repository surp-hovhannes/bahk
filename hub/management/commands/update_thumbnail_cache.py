from django.core.management.base import BaseCommand
from hub.models import Profile, Fast
from icons.models import Icon
from learning_resources.models import Video, Article

MODELS = {
    'profile': (Profile, 'profile_image'),
    'fast': (Fast, 'image'),
    'video': (Video, 'thumbnail'),
    'article': (Article, 'image'),
    'icon': (Icon, 'image'),
}


class Command(BaseCommand):
    help = 'Updates cached thumbnail URLs for models with images'

    def add_arguments(self, parser):
        parser.add_argument(
            '--model',
            choices=list(MODELS.keys()),
            help='Only update thumbnails for this model (default: all models)',
        )

    def handle(self, *args, **kwargs):
        selected = kwargs['model']
        targets = {selected: MODELS[selected]} if selected else MODELS

        for name, (model_class, image_field) in targets.items():
            qs = model_class.objects.filter(**{f'{image_field}__isnull': False})
            self.stdout.write(f"Updating {qs.count()} {name} thumbnails...")
            for obj in qs:
                try:
                    obj.save()
                    self.stdout.write(f"  ✓ {name} {obj.id}")
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  ✗ {name} {obj.id}: {e}"))

        self.stdout.write(self.style.SUCCESS("Finished updating thumbnail caches"))
