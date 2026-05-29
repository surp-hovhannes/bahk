from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from hub.models import Church, Fast


class RegenerateCommandErrorTests(TestCase):
    def test_regenerate_feast_contexts_requires_target(self):
        with self.assertRaisesMessage(CommandError, "Please specify either --all"):
            call_command("regenerate_feast_contexts")

    def test_regenerate_feast_contexts_missing_feast_raises(self):
        with self.assertRaisesMessage(CommandError, "Feast with ID 999 not found"):
            call_command("regenerate_feast_contexts", feast_id=999)

    def test_regenerate_map_wait_failure_raises(self):
        church = Church.objects.create(name="Test Church")
        fast = Fast.objects.create(name="Test Fast", church=church)
        task = Mock()
        task.id = "task-1"
        task.ready.return_value = True
        task.get.return_value = {"status": "error", "message": "render failed"}

        with patch(
            "hub.management.commands.regenerate_map.generate_participant_map.delay",
            return_value=task,
        ):
            with self.assertRaisesMessage(
                CommandError, "Map generation failed: render failed"
            ):
                call_command("regenerate_map", fast.id, wait=True)
