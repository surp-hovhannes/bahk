"""Tests for intention → prayer recommendations (issue #450)."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from hub.intention_recommendations import tags_for_intention
from hub.models import FastIntention
from prayers.models import Prayer
from tests.fixtures.test_data import TestDataFactory

User = get_user_model()


class TagsForIntentionTest(TestCase):
    def test_matches_common_intentions(self):
        self.assertEqual(tags_for_intention('peace for my anxious heart'),
                         ['peace', 'protection', 'trust'])
        self.assertEqual(tags_for_intention('healing for my sick mother'),
                         ['deliverance', 'protection', 'hope'])
        self.assertEqual(tags_for_intention('grateful for my family'),
                         ['doxology', 'adoration', 'intercession', 'guardian', 'love'])
        self.assertEqual(tags_for_intention('repentance for my sins'),
                         ['repentance', 'forgiveness', 'confession'])
        self.assertEqual(tags_for_intention('growing closer to Christ'),
                         ['faith', 'enlightenment', 'holiness'])
        self.assertEqual(tags_for_intention('clarity and wisdom for my next step'),
                         ['guidance', 'wisdom', 'light'])
        self.assertEqual(tags_for_intention('patience amid temptation'),
                         ['deliverance', 'purification', 'surrender'])

    def test_no_match_returns_empty(self):
        self.assertEqual(tags_for_intention('disciplining my body'), [])


class RecommendedPrayersViewTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name='Rec Church')
        self.user = TestDataFactory.create_user(
            username='rec@example.com', email='rec@example.com')
        self.profile = TestDataFactory.create_profile(
            user=self.user, church=self.church)
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('intention-recommend-prayers')

    def _prayer(self, title, tags, church=None, fast=None):
        prayer = Prayer.objects.create(
            title=title, text='text', church=church or self.church, fast=fast)
        prayer.tags.add(*tags)
        return prayer

    def test_ranks_by_tag_overlap_within_fast_church(self):
        FastIntention.objects.create(
            user=self.user, fast=self.fast, text='peace and healing')
        best = self._prayer('both', ['peace', 'deliverance'])
        second = self._prayer('one', ['peace'])
        self._prayer('other church', ['peace', 'deliverance'],
                     church=TestDataFactory.create_church())

        res = self.client.get(self.url, {'fast': self.fast.id})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual([p['id'] for p in res.data], [best.id, second.id])

    def test_fast_linked_prayer_boosted(self):
        FastIntention.objects.create(
            user=self.user, fast=self.fast, text='family')
        boosted = self._prayer('fast prayer', ['guardian'], fast=self.fast)
        self._prayer('plain prayer', ['guardian'])

        res = self.client.get(self.url, {'fast': self.fast.id})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data[0]['id'], boosted.id)

    def test_no_keyword_match_returns_empty_list(self):
        FastIntention.objects.create(
            user=self.user, fast=self.fast, text='a nice walk')
        self._prayer('any', ['peace'])

        res = self.client.get(self.url, {'fast': self.fast.id})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data, [])

    def test_no_intention_returns_404(self):
        res = self.client.get(self.url, {'fast': self.fast.id})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_blank_intention_returns_404(self):
        FastIntention.objects.create(
            user=self.user, fast=self.fast, text='')
        res = self.client.get(self.url, {'fast': self.fast.id})
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_invalid_fast_id_returns_400(self):
        res = self.client.get(self.url, {'fast': 'abc'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(user=None)
        res = self.client.get(self.url, {'fast': self.fast.id})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class LLMTagsForIntentionTest(TestCase):
    def setUp(self):
        from hub.models import LLMPrompt
        self.prompt = LLMPrompt.objects.create(
            model='claude-haiku-4-5-20251001',
            role='You tag spiritual intentions.',
            prompt='Pick relevant prayer tags.',
            applies_to='intentions',
            active=True,
        )
        church = TestDataFactory.create_church()
        prayer = Prayer.objects.create(title='p', text='t', church=church)
        prayer.tags.add('peace', 'trust')

    def _mock_service(self, raw):
        from unittest.mock import MagicMock
        service = MagicMock()
        service.client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=raw)])
        return service

    def test_returns_validated_tags(self):
        from unittest.mock import patch
        with patch('hub.services.llm_service.get_llm_service',
                   return_value=self._mock_service('["peace", "bogus-tag"]')):
            from hub.intention_recommendations import llm_tags_for_intention
            self.assertEqual(llm_tags_for_intention('calm my heart'), ['peace'])

    def test_openai_branch(self):
        from unittest.mock import MagicMock, patch
        self.prompt.model = 'gpt-4.1-nano'
        self.prompt.save()
        service = MagicMock()
        service.client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='["trust"]'))])
        with patch('hub.services.llm_service.get_llm_service',
                   return_value=service):
            from hub.intention_recommendations import llm_tags_for_intention
            self.assertEqual(llm_tags_for_intention('keep me steady'), ['trust'])

    def test_garbage_response_returns_empty(self):
        from unittest.mock import patch
        with patch('hub.services.llm_service.get_llm_service',
                   return_value=self._mock_service('no json here')):
            from hub.intention_recommendations import llm_tags_for_intention
            self.assertEqual(llm_tags_for_intention('anything'), [])

    def test_no_active_prompt_returns_none(self):
        self.prompt.active = False
        self.prompt.save()
        from hub.intention_recommendations import llm_tags_for_intention
        self.assertIsNone(llm_tags_for_intention('anything'))


class StoredLLMTagsTest(TestCase):
    def setUp(self):
        self.church = TestDataFactory.create_church(name='LLM Church')
        self.user = TestDataFactory.create_user(
            username='llm@example.com', email='llm@example.com')
        self.profile = TestDataFactory.create_profile(
            user=self.user, church=self.church)
        self.fast = TestDataFactory.create_fast(church=self.church)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('intention-recommend-prayers')

    def test_endpoint_uses_stored_llm_tags(self):
        intention = FastIntention.objects.create(
            user=self.user, fast=self.fast,
            text='walking with purpose',  # matches no curated keyword
            matched_tags=['faith'])
        prayer = Prayer.objects.create(
            title='Faith prayer', text='t', church=self.church)
        prayer.tags.add('faith')

        res = self.client.get(self.url, {'fast': self.fast.id})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual([p['id'] for p in res.data], [prayer.id])

    def test_task_stores_llm_tags(self):
        from unittest.mock import patch
        from hub.tasks.llm_tasks import tag_intention_prayers
        intention = FastIntention.objects.create(
            user=self.user, fast=self.fast, text='some intention')
        with patch('hub.intention_recommendations.llm_tags_for_intention',
                   return_value=['peace']):
            tag_intention_prayers(intention.id)
        intention.refresh_from_db()
        self.assertEqual(intention.matched_tags, ['peace'])

    def test_put_resets_and_retags(self):
        from unittest.mock import patch
        intention = FastIntention.objects.create(
            user=self.user, fast=self.fast, text='old text',
            matched_tags=['faith'])
        self.profile.fasts.add(self.fast)
        url = reverse('fast-intention', args=[self.fast.id])
        with patch('hub.views.fast.tag_intention_prayers') as mock_task:
            res = self.client.put(url, {'text': 'new intention text'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_task.delay.assert_called_once_with(intention.id)
        intention.refresh_from_db()
        self.assertIsNone(intention.matched_tags)

    def test_read_without_prompt_does_not_enqueue_tagging(self):
        from unittest.mock import patch
        intention = FastIntention.objects.create(
            user=self.user, fast=self.fast, text='peace')

        with patch('hub.tasks.llm_tasks.tag_intention_prayers') as mock_task:
            from hub.intention_recommendations import tags_for_recommendation
            tags_for_recommendation(intention)

        mock_task.delay.assert_not_called()

    @override_settings(CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
    })
    def test_repeated_reads_enqueue_tagging_once(self):
        from unittest.mock import patch
        from hub.models import LLMPrompt
        LLMPrompt.objects.create(
            model='claude-haiku-4-5-20251001',
            role='Tag intentions.',
            prompt='Pick relevant tags.',
            applies_to='intentions',
            active=True,
        )
        intention = FastIntention.objects.create(
            user=self.user, fast=self.fast, text='peace')

        with patch('hub.tasks.llm_tasks.tag_intention_prayers') as mock_task:
            from hub.intention_recommendations import tags_for_recommendation
            tags_for_recommendation(intention)
            tags_for_recommendation(intention)

        mock_task.delay.assert_called_once_with(intention.id)

    def test_rejoin_with_changed_text_clears_stored_tags(self):
        from unittest.mock import patch
        intention = FastIntention.objects.create(
            user=self.user, fast=self.fast, text='old intention',
            matched_tags=['faith'], is_active=False)
        join_url = reverse('fast-join')

        with patch('hub.views.fast.tag_intention_prayers'):
            res = self.client.put(join_url, {
                'fast_id': self.fast.id,
                'intention_text': 'new intention',
            }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        intention.refresh_from_db()
        self.assertEqual(intention.text, 'new intention')
        self.assertIsNone(intention.matched_tags)
