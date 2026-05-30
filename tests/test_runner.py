"""Custom test runner with media cleanup."""
from django.test.runner import DiscoverRunner
from .test_utils import cleanup_test_media, setup_test_media


class MediaCleanupTestRunner(DiscoverRunner):
    """Test runner that cleans up media files after tests."""
    
    def setup_test_environment(self, **kwargs):
        """Set up test environment and ensure media directory exists."""
        super().setup_test_environment(**kwargs)
        setup_test_media()
    
    def teardown_test_environment(self, **kwargs):
        """Clean up test environment and remove media files."""
        if not cleanup_test_media():
            raise RuntimeError("Test media cleanup failed or was refused")
        super().teardown_test_environment(**kwargs)
