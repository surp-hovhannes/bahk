"""Tests for feast icon matching functionality."""
from datetime import date
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.test.utils import tag
from django.db.models.signals import post_save
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from hub.models import Church, Day, Feast
from hub.tasks.icon_tasks import (
    match_icon_to_feast_task,
    _match_icons_with_llm,
    _simple_match_icons,
)
from hub.signals import handle_feast_save
from icons.models import Icon


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
@tag('slow', 'integration')
class FeastIconMatchingTaskTests(TestCase):
    """Tests for the icon matching Celery task."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)
        cache.clear()

    def _create_feast_without_signal(self, **kwargs):
        """Create Feast fixtures without eager post-save task side effects."""
        post_save.disconnect(handle_feast_save, sender=Feast)
        try:
            return Feast.objects.create(**kwargs)
        finally:
            post_save.connect(handle_feast_save, sender=Feast)

    @patch('hub.signals.match_icon_to_feast_task.delay')
    def test_create_feast_without_signal_does_not_enqueue_icon_task(self, mock_delay):
        """Test that direct task fixtures are created without signal side effects."""
        day = Day.objects.create(date=self.test_date, church=self.church)

        feast = self._create_feast_without_signal(
            church=day.church,
            name="Christmas",
        )

        self.assertIsNotNone(feast.id)
        mock_delay.assert_not_called()

    def test_match_icon_task_skips_if_icon_exists(self):
        """Test that task skips if icon is already set."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='test_icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Test Icon",
            church=self.church,
            image=test_image
        )
        feast = self._create_feast_without_signal(
            church=day.church,
            name="Christmas",
            icon=icon,
        )

        # Mock the matching function to ensure it's not called
        with patch('hub.tasks.icon_tasks._match_icons_with_llm') as mock_match:
            match_icon_to_feast_task(feast.id)
            # Matching should not be called since icon is already set
            mock_match.assert_not_called()

        # Icon should remain unchanged
        feast.refresh_from_db()
        self.assertEqual(feast.icon, icon)

    def test_match_icon_task_handles_missing_feast(self):
        """Test that task handles non-existent feast gracefully."""
        with patch('hub.tasks.icon_tasks.logger') as mock_logger:
            match_icon_to_feast_task(99999)
            mock_logger.error.assert_called()

    def test_match_icon_task_with_no_icons(self):
        """Test that task handles case when no icons exist for church."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = self._create_feast_without_signal(
            church=day.church,
            name="Christmas",
        )

        # No icons created for this church
        with patch('hub.tasks.icon_tasks.logger') as mock_logger:
            match_icon_to_feast_task(feast.id)
            mock_logger.info.assert_called()

        feast.refresh_from_db()
        self.assertIsNone(feast.icon)

    def test_match_icon_task_with_high_confidence_match(self):
        """Test that task saves icon when high confidence match is found."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='nativity.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Nativity Icon",
            church=self.church,
            image=test_image
        )
        feast = self._create_feast_without_signal(
            church=day.church,
            name="Nativity of Christ",
        )

        # Mock the matching function to return high confidence match
        with patch('hub.tasks.icon_tasks._match_icons_with_llm') as mock_match:
            mock_match.return_value = [
                {'id': icon.id, 'confidence': 'high'}
            ]
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        self.assertEqual(feast.icon, icon)

    def test_match_icon_task_with_medium_confidence_match(self):
        """Test that task does not save icon when confidence is below threshold."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='generic.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Generic Icon",
            church=self.church,
            image=test_image
        )
        feast = self._create_feast_without_signal(
            church=day.church,
            name="Christmas",
        )

        # Mock the matching function to return medium confidence match
        with patch('hub.tasks.icon_tasks._match_icons_with_llm') as mock_match:
            mock_match.return_value = [
                {'id': icon.id, 'confidence': 'medium'}
            ]
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        # Icon should not be saved since threshold is 'high'
        self.assertIsNone(feast.icon)

    def test_match_icon_task_with_low_confidence_match(self):
        """Test that task does not save icon when confidence is low."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='unrelated.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Unrelated Icon",
            church=self.church,
            image=test_image
        )
        feast = self._create_feast_without_signal(
            church=day.church,
            name="Christmas",
        )

        # Mock the matching function to return low confidence match
        with patch('hub.tasks.icon_tasks._match_icons_with_llm') as mock_match:
            mock_match.return_value = [
                {'id': icon.id, 'confidence': 'low'}
            ]
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        # Icon should not be saved since threshold is 'high'
        self.assertIsNone(feast.icon)

    def test_match_icon_task_with_no_matches(self):
        """Test that task handles case when no matches are found."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='unrelated.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        Icon.objects.create(
            title="Unrelated Icon",
            church=self.church,
            image=test_image
        )
        feast = self._create_feast_without_signal(
            church=day.church,
            name="Christmas",
        )

        # Mock the matching function to return no matches
        with patch('hub.tasks.icon_tasks._match_icons_with_llm') as mock_match:
            mock_match.return_value = []
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        self.assertIsNone(feast.icon)

    def test_simple_match_icons_function(self):
        """Test the simple icon matching fallback function."""
        test_image1 = SimpleUploadedFile(
            name='nativity.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        test_image2 = SimpleUploadedFile(
            name='easter.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon1 = Icon.objects.create(
            title="Nativity Scene",
            church=self.church,
            image=test_image1
        )
        icon2 = Icon.objects.create(
            title="Easter Icon",
            church=self.church,
            image=test_image2
        )
        icons = [icon1, icon2]

        # Test exact title match
        result = _simple_match_icons(icons, "Nativity Scene", max_results=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], icon1.id)

        # Test partial match
        result = _simple_match_icons(icons, "Nativity", max_results=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], icon1.id)

        # Test no match
        result = _simple_match_icons(icons, "Christmas", max_results=1)
        self.assertEqual(len(result), 0)


class FeastIconMatchingScopeTests(TestCase):
    """Tests for church scoping in icon matching."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.other_church = Church.objects.create(name="Other Church")
        self.test_date = date(2025, 12, 25)
        self.test_image = SimpleUploadedFile(
            name='icon.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )

    def _create_feast_without_signal(self, **kwargs):
        """Create Feast fixtures without eager post-save task side effects."""
        post_save.disconnect(handle_feast_save, sender=Feast)
        try:
            return Feast.objects.create(**kwargs)
        finally:
            post_save.connect(handle_feast_save, sender=Feast)

    def test_match_icon_task_rejects_icon_from_another_church(self):
        """Task must not assign a matched icon outside the feast church."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = self._create_feast_without_signal(
            church=day.church,
            name="Nativity of Christ",
        )
        Icon.objects.create(
            title="Local Nativity Icon",
            church=self.church,
            image=self.test_image,
        )
        other_icon = Icon.objects.create(
            title="Other Church Nativity Icon",
            church=self.other_church,
            image=SimpleUploadedFile(
                name='other_icon.jpg',
                content=b'fake image content',
                content_type='image/jpeg'
            ),
        )

        with patch('hub.tasks.icon_tasks._match_icons_with_llm') as mock_match:
            mock_match.return_value = [
                {'id': other_icon.id, 'confidence': 'high'}
            ]
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        self.assertIsNone(feast.icon)

    @override_settings(OPENAI_API_KEY='test-key')
    @patch('hub.tasks.icon_tasks.openai_chat_completion')
    def test_llm_parser_filters_out_of_scope_icon_ids(self, mock_completion):
        """LLM parser should only return IDs from the provided icon list."""
        icon = Icon.objects.create(
            title="Local Nativity Icon",
            church=self.church,
            image=self.test_image,
        )
        other_icon = Icon.objects.create(
            title="Other Church Nativity Icon",
            church=self.other_church,
            image=SimpleUploadedFile(
                name='other_icon_parser.jpg',
                content=b'fake image content',
                content_type='image/jpeg'
            ),
        )
        mock_choice = mock_completion.return_value.choices[0]
        mock_choice.message.content = (
            f'[{{"id": {other_icon.id}, "confidence": "high"}}, '
            f'{{"id": {icon.id}, "confidence": "high"}}]'
        )

        matches = _match_icons_with_llm([icon], "Nativity", max_results=2)

        self.assertEqual(matches, [{'id': icon.id, 'confidence': 'high'}])


@tag('slow', 'integration')
class FeastIconMatchingSignalTests(TestCase):
    """Tests for the feast icon matching signal handler."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    @patch('hub.signals.match_icon_to_feast_task.delay')
    def test_signal_triggers_on_feast_creation(self, mock_task_delay):
        """Test that signal triggers icon matching task when feast is created."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        
        # Disconnect the signal temporarily to avoid actual task execution
        post_save.disconnect(handle_feast_save, sender=Feast)
        
        feast = Feast.objects.create(
            church=day.church,
            name="Test Feast",
        )

        # Manually trigger the signal handler
        handle_feast_save(sender=Feast, instance=feast, created=True)
        
        # Reconnect signal
        post_save.connect(handle_feast_save, sender=Feast)

        # Verify task was called
        mock_task_delay.assert_called_once_with(feast.id)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    @patch('hub.signals.match_icon_to_feast_task.delay')
    def test_signal_does_not_trigger_on_update(self, mock_task_delay):
        """Test that signal does not trigger icon matching on feast update."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        
        # Disconnect the signal temporarily
        post_save.disconnect(handle_feast_save, sender=Feast)
        
        feast = Feast.objects.create(
            church=day.church,
            name="Test Feast",
        )

        # Update the feast (created=False)
        feast.name = "Updated Feast"
        feast.save()

        # Manually trigger the signal handler with created=False
        handle_feast_save(sender=Feast, instance=feast, created=False)
        
        # Reconnect signal
        post_save.connect(handle_feast_save, sender=Feast)

        # Verify task was NOT called
        mock_task_delay.assert_not_called()


@tag('slow', 'integration')
class FeastIconModelTests(TestCase):
    """Tests for the icon field on Feast model."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)

    def test_feast_icon_field_exists(self):
        """Test that icon field exists on Feast model."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
        )

        # Icon should be None by default
        self.assertIsNone(feast.icon)

    def test_feast_icon_field_can_be_set(self):
        """Test that icon field can be set."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='nativity.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Nativity Icon",
            church=self.church,
            image=test_image
        )
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
            icon=icon,
        )

        feast.refresh_from_db()
        self.assertEqual(feast.icon, icon)

    def test_feast_icon_field_nullable(self):
        """Test that icon field can be None/null."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        icon = Icon.objects.create(
            title="Nativity Icon",
            church=self.church,
            image="nativity.jpg"
        )
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
            icon=icon,
        )

        # Clear icon
        feast.icon = None
        feast.save()
        feast.refresh_from_db()
        self.assertIsNone(feast.icon)

    def test_feast_icon_set_null_on_delete(self):
        """Test that icon field is set to None when icon is deleted."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='nativity.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Nativity Icon",
            church=self.church,
            image=test_image
        )
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
            icon=icon,
        )

        # Delete the icon
        icon.delete()

        # Feast icon should be None (SET_NULL)
        feast.refresh_from_db()
        self.assertIsNone(feast.icon)

    def test_feast_icon_related_name(self):
        """Test that feasts can be accessed through icon.feasts."""
        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='nativity.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Nativity Icon",
            church=self.church,
            image=test_image
        )
        feast1 = Feast.objects.create(
            church=day.church,
            name="Christmas",
            icon=icon,
        )

        # Access feasts through icon
        self.assertEqual(icon.feasts.count(), 1)
        self.assertIn(feast1, icon.feasts.all())


@tag('slow', 'integration')
class FeastIconAPITests(TestCase):
    """Tests for API response including icon."""

    def setUp(self):
        self.church = Church.objects.get(pk=Church.get_default_pk())
        self.test_date = date(2025, 12, 25)
        cache.clear()

    def test_feast_api_includes_icon_when_present(self):
        """Test that API response includes icon when icon is set."""
        from rest_framework.test import APIClient
        from rest_framework import status

        day = Day.objects.create(date=self.test_date, church=self.church)
        test_image = SimpleUploadedFile(
            name='nativity.jpg',
            content=b'fake image content',
            content_type='image/jpeg'
        )
        icon = Icon.objects.create(
            title="Nativity Icon",
            church=self.church,
            image=test_image
        )
        # Seed under the name the engine gives this date: the view resolves the commemoration
        # through the engine, so an invented name would simply not be the row it looks up.
        from hub.services.feast_service import get_feast_for_date
        feast = Feast.objects.create(
            church=self.church,
            name=get_feast_for_date(self.test_date, self.church)["name_en"],
            icon=icon,
        )

        client = APIClient()
        response = client.get(
            f'/api/feasts/?date={self.test_date.strftime("%Y-%m-%d")}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('feast', response.data)
        self.assertIn('icon', response.data['feast'])
        self.assertIsNotNone(response.data['feast']['icon'])
        self.assertEqual(response.data['feast']['icon']['id'], icon.id)

    def test_feast_api_includes_null_icon_when_not_present(self):
        """Test that API response includes null icon when icon is not set."""
        from rest_framework.test import APIClient
        from rest_framework import status

        day = Day.objects.create(date=self.test_date, church=self.church)
        feast = Feast.objects.create(
            church=day.church,
            name="Christmas",
        )

        client = APIClient()
        response = client.get(
            f'/api/feasts/?date={self.test_date.strftime("%Y-%m-%d")}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('feast', response.data)
        self.assertIn('icon', response.data['feast'])
        self.assertIsNone(response.data['feast']['icon'])
