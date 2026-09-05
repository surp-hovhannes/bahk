"""Tests for feast icon matching functionality."""
from datetime import date
from types import SimpleNamespace
from unittest import TestCase as SimpleTestCase
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.test.utils import tag
from django.db.models.signals import post_save
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from hub.models import Church, Day, Feast
from hub.services.icon_match_service import IconMatchOutcome
from hub.services.icon_matching import (
    CANDIDATE_LIMIT_PER_TIER,
    IconMatchRequest,
    generate_icon_candidates,
    validate_and_rank_decision,
)
from hub.tasks.icon_tasks import (
    match_icon_to_feast_task,
)
from hub.signals import handle_feast_save
from icons.models import Icon


def fake_icon(icon_id, title, *tags):
    return SimpleNamespace(id=icon_id, title=title, tags=tags)


def approving_payload(candidate):
    rationale = {
        'direct_exact': 'explicit_subject',
        'related_specific': 'specific_related_subject',
        'thematic': 'defensible_theme',
    }[candidate.match_tier]
    confidence = {
        'direct_exact': 'high',
        'related_specific': 'medium',
        'thematic': 'low',
    }[candidate.match_tier]
    return {
        'decision': {
            'id': candidate.icon_id,
            'match_tier': candidate.match_tier,
            'confidence': confidence,
            'matched_concepts': list(candidate.matched_concepts),
            'evidence_refs': list(candidate.evidence_refs),
            'rationale_code': rationale,
        }
    }


class IconMatchHierarchyTests(SimpleTestCase):
    def test_baptist_direct_candidates_exclude_incidental_transfiguration_tags(self):
        icons = [
            fake_icon(498, 'Transfiguration1', 'john'),
            fake_icon(259, 'Transfiguration of Christ', 'john-apostle'),
            fake_icon(377, 'St John the Baptist'),
            fake_icon(36, 'St. John the Baptist'),
        ]
        request = IconMatchRequest(
            kind='feast',
            primary_text='Sts. John the Forerunner (Baptist) and Job the Righteous',
            auto_assign_policy='feast_strict',
        )

        candidates = generate_icon_candidates(icons, request)

        self.assertEqual({candidate.icon_id for candidate in candidates}, {36, 377})
        self.assertTrue(all(candidate.match_tier == 'related_specific' for candidate in candidates))
        self.assertTrue(all(not candidate.complete_coverage for candidate in candidates))

    def test_composite_requires_all_concrete_subjects_for_direct_exact(self):
        candidates = generate_icon_candidates(
            [
                fake_icon(1, 'St John the Baptist'),
                fake_icon(2, 'St John the Baptist and Job the Righteous'),
            ],
            IconMatchRequest(
                kind='feast',
                primary_text='Sts. John the Forerunner (Baptist) and Job the Righteous',
            ),
        )

        by_id = {candidate.icon_id: candidate for candidate in candidates}
        self.assertEqual(by_id[1].match_tier, 'related_specific')
        self.assertEqual(by_id[2].match_tier, 'direct_exact')
        self.assertTrue(by_id[2].complete_coverage)

    def test_direct_cap_cannot_drop_full_composite_candidate(self):
        icons = [
            fake_icon(index, 'St John the Baptist')
            for index in range(1, CANDIDATE_LIMIT_PER_TIER + 5)
        ]
        icons.append(fake_icon(999, 'St John the Baptist and Job the Righteous'))
        candidates = generate_icon_candidates(
            icons,
            IconMatchRequest(
                kind='feast',
                primary_text='John the Baptist and Job the Righteous',
            ),
        )

        direct = [candidate for candidate in candidates if candidate.match_tier == 'direct_exact']
        self.assertEqual([candidate.icon_id for candidate in direct], [999])

    def test_unregistered_saint_and_event_match_from_metadata_vocabulary(self):
        for request_text, title in (
            ('Saint Vartan the Warrior', 'St. Vartan the Warrior'),
            ('Feast of the Presentation of the Lord', 'Presentation of the Lord'),
        ):
            with self.subTest(request_text=request_text):
                candidates = generate_icon_candidates(
                    [fake_icon(1, title)],
                    IconMatchRequest(kind='feast', primary_text=request_text),
                )
                self.assertEqual(candidates[0].match_tier, 'direct_exact')

    def test_registered_and_unregistered_composite_is_additive(self):
        candidates = generate_icon_candidates(
            [
                fake_icon(1, 'St John the Baptist'),
                fake_icon(2, 'St John the Baptist and Job the Righteous'),
            ],
            IconMatchRequest(
                kind='feast',
                primary_text='John the Forerunner and Job the Righteous',
            ),
        )
        by_id = {candidate.icon_id: candidate for candidate in candidates}
        self.assertEqual(by_id[1].match_tier, 'related_specific')
        self.assertEqual(by_id[2].match_tier, 'direct_exact')

    def test_bare_john_does_not_match_qualified_or_incidental_john_metadata(self):
        candidates = generate_icon_candidates(
            [
                fake_icon(1, 'St John the Apostle'),
                fake_icon(2, 'St John the Baptist'),
                fake_icon(3, 'St John the Evangelist'),
                fake_icon(4, 'Transfiguration', 'john'),
                fake_icon(5, 'St John (Apostle)'),
                fake_icon(6, 'St John (Baptist)'),
                fake_icon(7, 'St John (Evangelist)'),
            ],
            IconMatchRequest(kind='feast', primary_text='Saint John'),
        )

        self.assertEqual(candidates, [])

    def test_parenthetical_identity_qualifiers_are_preserved_for_exact_matching(self):
        cases = (
            ('Saint John the Apostle', 'St John (Apostle)'),
            ('Saint John the Baptist', 'St John (Baptist)'),
            ('Saint John the Evangelist', 'St John (Evangelist)'),
            ('Saint Vartan the Martyr', 'St Vartan (Martyr)'),
            ('King Tiridates', 'Tiridates (King)'),
        )
        for request_text, title in cases:
            with self.subTest(request_text=request_text):
                candidates = generate_icon_candidates(
                    [fake_icon(1, title)],
                    IconMatchRequest(kind='feast', primary_text=request_text),
                )

                self.assertEqual(candidates[0].match_tier, 'direct_exact')

    def test_single_word_event_title_can_still_match_exactly(self):
        candidates = generate_icon_candidates(
            [fake_icon(1, 'Nativity')],
            IconMatchRequest(kind='feast', primary_text='Nativity'),
        )

        self.assertEqual(candidates[0].match_tier, 'direct_exact')

    def test_direct_duplicate_selection_is_high_and_input_order_stable(self):
        icons = [
            fake_icon(377, 'St John the Baptist'),
            fake_icon(36, 'St. John the Baptist'),
            fake_icon(333, 'St John the Baptist 1'),
        ]
        request = IconMatchRequest(kind='feast', primary_text='John the Baptist')

        first_candidates = generate_icon_candidates(icons, request)
        second_candidates = generate_icon_candidates(reversed(icons), request)
        payload = approving_payload(next(item for item in first_candidates if item.icon_id == 377))

        first = validate_and_rank_decision(payload, first_candidates, max_results=3)
        second = validate_and_rank_decision(payload, second_candidates, max_results=3)

        self.assertEqual(first, second)
        self.assertEqual([item['id'] for item in first], [36, 377, 333])
        self.assertTrue(all(item['confidence'] == 'high' for item in first))

    def test_python_rejects_lower_tier_when_direct_candidate_exists(self):
        icons = [
            fake_icon(36, 'St. John the Baptist'),
            fake_icon(900, 'The Prodigal Son', 'repentance'),
        ]
        request = IconMatchRequest(
            kind='content',
            primary_text='John the Baptist penitential prayer',
        )
        candidates = generate_icon_candidates(icons, request)
        thematic = next(item for item in candidates if item.match_tier == 'thematic')

        self.assertEqual(validate_and_rank_decision(approving_payload(thematic), candidates), [])

    def test_holy_translators_relates_to_mesrop_only_without_direct_icon(self):
        request = IconMatchRequest(kind='feast', primary_text='Feast of the Holy Translators')
        related = generate_icon_candidates(
            [fake_icon(408, 'Unknown Armenian painter. St. Mesrop Mashtots')],
            request,
        )
        self.assertEqual(related[0].match_tier, 'related_specific')
        self.assertEqual(related[0].matched_concepts, ('mesrop_mashtots',))

        with_direct = generate_icon_candidates(
            [
                fake_icon(408, 'St. Mesrop Mashtots'),
                fake_icon(901, 'The Holy Translators'),
            ],
            request,
        )
        self.assertEqual(with_direct[0].match_tier, 'direct_exact')

    def test_penitential_content_relates_thematically_to_prodigal_son(self):
        candidates = generate_icon_candidates(
            [fake_icon(902, 'The Prodigal Son')],
            IconMatchRequest(
                kind='content',
                primary_text='A penitential prayer',
                context_terms=('repentance',),
            ),
        )

        self.assertEqual(candidates[0].match_tier, 'thematic')
        self.assertEqual(candidates[0].evidence_refs, ('theme:repentance',))

    def test_invalid_schema_ids_and_tier_confidence_pairs_fail_closed(self):
        candidates = generate_icon_candidates(
            [fake_icon(36, 'St. John the Baptist')],
            IconMatchRequest(kind='feast', primary_text='John the Baptist'),
        )
        valid = approving_payload(candidates[0])
        invalid_id = {**valid, 'decision': {**valid['decision'], 'id': 999}}
        invalid_pair = {
            **valid,
            'decision': {**valid['decision'], 'confidence': 'medium'},
        }
        unknown_ref = {
            **valid,
            'decision': {**valid['decision'], 'evidence_refs': ['tag:john']},
        }

        for payload in ({'matches': []}, invalid_id, invalid_pair, unknown_ref):
            with self.subTest(payload=payload):
                self.assertEqual(validate_and_rank_decision(payload, candidates), [])

    def test_identity_boundaries_and_unicode(self):
        cases = (
            ('James (Nisibis)', 'James (Jerusalem)', (), False),
            ('James (Nisibis)', 'James of Nisibis', (), True),
            ('John the Baptist (Nisibis)', 'John the Baptist', (), False),
            ('John the Baptist', 'John the Baptist (Nisibis)', ('John the Baptist',), False),
            ('John the Baptist', 'John the Apostle', ('John the Baptist',), False),
            ('John the Baptist', 'John the Baptist Unknown Saint', (), False),
            ('John the Baptist Unknown Saint', 'John the Baptist', (), False),
            ('Սուրբ Հոբ', 'Սուրբ Հոբ', (), True),
            ('李', '李', (), True),
            ('John the Baptist and 李', 'John the Baptist', (), False),
            ('John the Baptist and Սուրբ Հոբ', 'John the Baptist', (), False),
            ('John the Baptist and Սուրբ Հոբ', 'John the Baptist and Սուրբ Հոբ', (), True),
            ('Vardan the Warrior', 'Vartan the Warrior', (), True),
            ('Vardan (Mamikonian)', 'Vartan Mamikonian', (), True),
            ('Vardan (Nisibis)', 'Vartan (Jerusalem)', (), False),
            ('Vardan the Warrior', 'Vartan', (), False),
            ('Vardan (Nisibis)', 'Vartan (Jerusalem)', ('Vardan Nisibis',), False),
        )
        for request_text, title, tags, assignable in cases:
            with self.subTest(request=request_text, title=title):
                candidates = generate_icon_candidates(
                    [fake_icon(1, title, *tags)],
                    IconMatchRequest(kind='feast', primary_text=request_text),
                )
                self.assertEqual(any(c.match_tier == 'direct_exact' for c in candidates), assignable)
                if candidates and not assignable:
                    self.assertFalse(candidates[0].complete_coverage)

    def test_event_eligibility_and_portrait_fallback(self):
        request = IconMatchRequest(kind='feast', primary_text='Beheading of John the Baptist')
        for title, tags, expected in (
            ('John the Baptist', (), 'depiction:subject_portrait'),
            ('Decollation of John the Forerunner', (), 'depiction:event_exact'),
            ('Nativity of John the Baptist', ('John the Baptist',), None),
            ('John the Baptist', ('Nativity of John the Baptist',), None),
            ('Nativity', ('John the Baptist',), None),
            ('Unknown miracle', ('John the Baptist',), None),
            ('John the Apostle', (), None),
        ):
            with self.subTest(title=title, tags=tags):
                candidates = generate_icon_candidates([fake_icon(1, title, *tags)], request)
                if expected:
                    self.assertEqual(candidates[0].match_tier, 'direct_exact')
                    self.assertIn(expected, candidates[0].evidence_refs)
                else:
                    self.assertEqual(candidates, [])

    def test_unknown_event_modifiers_never_become_portrait_fallback(self):
        for request_text, title, tags in (
            ('Miracle of John the Baptist', 'John the Baptist', ()),
            ('Beheading of John the Baptist', 'Miracle of John the Baptist', ('John the Baptist',)),
            ('Beheading of John the Baptist (Unknown)', 'John the Baptist', ()),
        ):
            with self.subTest(request=request_text, title=title):
                self.assertEqual(generate_icon_candidates(
                    [fake_icon(1, title, *tags)],
                    IconMatchRequest(kind='feast', primary_text=request_text),
                ), [])

    def test_portrait_fallback_requires_whole_depiction_support(self):
        request = IconMatchRequest(kind='feast', primary_text='Beheading of John the Baptist')
        for title, tags in (
            ('John the Baptist, Preaching in the Wilderness', ()),
            ('John the Baptist', ('Preaching in the Wilderness',)),
            ('John the Baptist', ('John of Nisibis',)),
            ('John the Baptist, Unknown miracle', ('John the Baptist',)),
        ):
            with self.subTest(title=title, tags=tags):
                self.assertEqual(generate_icon_candidates([fake_icon(1, title, *tags)], request), [])

        candidates = generate_icon_candidates(
            [fake_icon(1, 'John the Baptist', 'desert', 'prophet', 'John the Forerunner')], request)
        self.assertEqual(candidates[0].match_tier, 'direct_exact')
        self.assertIn('depiction:subject_portrait', candidates[0].evidence_refs)

    def test_contradictory_events_across_metadata_fail_before_ranking(self):
        request = IconMatchRequest(kind='feast', primary_text='Beheading of John the Baptist')
        for title, tags in (
            ('Nativity', ('Beheading of John the Baptist',)),
            ('Nativity of Christ', ('Beheading of John the Baptist',)),
            ('John the Baptist, Nativity', ('Beheading of John the Baptist',)),
            ('John the Baptist', ('Beheading of John the Baptist', 'Nativity')),
            ('Beheading of John the Baptist', ('Birth of Christ',)),
        ):
            with self.subTest(title=title, tags=tags):
                contradictory = fake_icon(1, title, *tags)
                self.assertEqual(generate_icon_candidates([contradictory], request), [])
                # Invalid exact evidence must not suppress the valid portrait.
                candidates = generate_icon_candidates(
                    [contradictory, fake_icon(2, 'John the Baptist')], request)
                self.assertEqual([candidate.icon_id for candidate in candidates], [2])
                self.assertIn('depiction:subject_portrait', candidates[0].evidence_refs)

    def test_event_composites_require_every_subject(self):
        request = IconMatchRequest(kind='feast', primary_text='Beheading of John the Baptist and Job the Righteous')
        for title in ('John the Baptist', 'Beheading of John the Baptist'):
            candidates = generate_icon_candidates([fake_icon(1, title)], request)
            self.assertEqual(candidates[0].match_tier, 'related_specific')
        for tags in ((), ('John the Baptist', 'Job the Righteous')):
            with self.subTest(tags=tags):
                candidates = generate_icon_candidates(
                    [fake_icon(1, 'John the Baptist and Job the Righteous', *tags)], request)
                self.assertEqual(candidates[0].match_tier, 'direct_exact')
                self.assertIn('depiction:subject_portrait', candidates[0].evidence_refs)

    def test_event_preference_precedes_caps_quality_and_model_choice(self):
        request = IconMatchRequest(kind='feast', primary_text='Beheading of John the Baptist')
        portraits = [fake_icon(i, 'John the Baptist') for i in range(1, 30)]
        event = fake_icon(999, 'Z depiction', 'Decollation of John the Baptist')
        for icons in (portraits + [event], [event] + portraits[::-1]):
            with self.subTest(order=icons[0].id):
                candidates = generate_icon_candidates(icons, request)
                self.assertEqual([c.icon_id for c in candidates], [999])
        portrait_candidates = generate_icon_candidates(portraits[:1], request)
        # Validation also enforces preference for uncapped/mixed candidate input,
        # even if the provider approves the lower-specificity portrait.
        results = validate_and_rank_decision(
            approving_payload(portrait_candidates[0]), portrait_candidates + candidates, max_results=3)
        self.assertEqual([r['id'] for r in results], [999])
        self.assertEqual(results[0]['rationale_code'], 'explicit_event')
        payload = approving_payload(portrait_candidates[0])
        payload['decision']['rationale_code'] = 'explicit_event'
        results = validate_and_rank_decision(payload, portrait_candidates)
        self.assertEqual(results[0]['rationale_code'], 'explicit_subject')



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

    @patch('hub.services.icon_match_service.OpenAIIconProvider')
    def test_real_matcher_assigns_event_then_portrait_when_event_unavailable(self, provider_factory):
        from hub.tests.icon_match_fixtures import FixtureProvider, analysis_for, candidate
        portrait = Icon.objects.create(title='John the Baptist', church=self.church, image='portrait.jpg')
        event = Icon.objects.create(title='Decollation of John the Baptist', church=self.church, image='event.jpg')
        feast = self._create_feast_without_signal(church=self.church, name='Beheading of John the Baptist')
        provider = FixtureProvider(analysis_for('John the Baptist', 'Beheading'), [
            candidate(portrait.id, portrait.title, 'subject_portrait'),
            candidate(event.id, event.title, 'exact_event'),
        ])
        provider_factory.return_value = provider
        match_icon_to_feast_task(feast.id)
        feast.refresh_from_db()
        self.assertEqual(feast.icon_id, event.id)
        Feast.objects.filter(pk=feast.pk).update(icon=None)
        event.delete()
        match_icon_to_feast_task(feast.id)
        feast.refresh_from_db()
        self.assertEqual(feast.icon_id, portrait.id)
        self.assertEqual(len(provider.calls), 6)

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
        with patch('hub.tasks.icon_tasks.match_icons') as mock_match:
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

        with patch('hub.tasks.icon_tasks.match_icons') as mock_match:
            mock_match.return_value = IconMatchOutcome(status="complete", matches=[
                {'id': icon.id, 'match_tier': 'direct_exact', 'confidence': 'high', 'auto_assignable': True}
            ])
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
        with patch('hub.tasks.icon_tasks.match_icons') as mock_match:
            mock_match.return_value = IconMatchOutcome(status="complete", matches=[
                {'id': icon.id, 'match_tier': 'related_specific', 'confidence': 'medium'}
            ])
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
        with patch('hub.tasks.icon_tasks.match_icons') as mock_match:
            mock_match.return_value = IconMatchOutcome(status="complete", matches=[
                {'id': icon.id, 'match_tier': 'thematic', 'confidence': 'low'}
            ])
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
        with patch('hub.tasks.icon_tasks.match_icons') as mock_match:
            mock_match.return_value = IconMatchOutcome(status="complete", matches=[])
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        self.assertIsNone(feast.icon)

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

        with patch('hub.tasks.icon_tasks.match_icons') as mock_match:
            mock_match.return_value = IconMatchOutcome(status="complete", matches=[
                {'id': other_icon.id, 'match_tier': 'direct_exact', 'confidence': 'high', 'auto_assignable': True}
            ])
            match_icon_to_feast_task(feast.id)

        feast.refresh_from_db()
        self.assertIsNone(feast.icon)

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
