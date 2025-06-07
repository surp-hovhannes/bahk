"""Management command to clean up test media files."""
from django.core.management.base import BaseCommand
from tests.test_utils import cleanup_test_media


class Command(BaseCommand):
    help = 'Clean up test media files and directories'
    
    def handle(self, *args, **options):
        self.stdout.write('Cleaning up test media files...')
        cleanup_test_media()
        self.stdout.write(
            self.style.SUCCESS('Successfully cleaned up test media files.')
        ) 