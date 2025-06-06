from django.test import TestCase
from hub.tasks import add


class AddTaskTests(TestCase):
    """Tests for the add Celery task."""
    
    def test_add_task(self):
        """Test that the add task returns correct result."""
        result = add.delay(4, 6)
        self.assertEqual(result.get(timeout=10), 10)