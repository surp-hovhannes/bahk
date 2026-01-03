"""Tests for prayers app."""
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from hub.models import Church, Fast
from learning_resources.models import Bookmark
from prayers.models import Prayer, PrayerSet, PrayerSetMembership
from tests.fixtures.test_data import TestDataFactory


class PrayerModelTests(TestCase):
    """Test cases for Prayer model."""
    
    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name='Test Church')
        self.fast = Fast.objects.create(
            name='Test Fast',
            church=self.church
        )
    
    def test_create_prayer(self):
        """Test creating a basic prayer."""
        prayer = Prayer.objects.create(
            title='Test Prayer',
            text='This is a test prayer text.',
            category='morning',
            church=self.church
        )
        self.assertEqual(prayer.title, 'Test Prayer')
        self.assertEqual(prayer.category, 'morning')
        self.assertEqual(prayer.church, self.church)
        self.assertIsNone(prayer.fast)
        self.assertIsNone(prayer.video)
    
    def test_create_prayer_with_fast(self):
        """Test creating a prayer associated with a fast."""
        prayer = Prayer.objects.create(
            title='Lenten Prayer',
            text='Prayer for the fast.',
            category='general',
            church=self.church,
            fast=self.fast
        )
        self.assertEqual(prayer.fast, self.fast)
    
    def test_create_prayer_with_video(self):
        """Test creating a prayer associated with a video."""
        video = TestDataFactory.create_video(title='Prayer Video')
        prayer = Prayer.objects.create(
            title='Prayer with Video',
            text='This prayer has an associated video.',
            category='morning',
            church=self.church,
            video=video
        )
        self.assertEqual(prayer.video, video)
        # Verify reverse relationship works
        self.assertIn(prayer, video.prayers.all())
    
    def test_prayer_tags(self):
        """Test adding tags to prayers."""
        prayer = Prayer.objects.create(
            title='Tagged Prayer',
            text='Prayer with tags.',
            category='general',
            church=self.church
        )
        prayer.tags.add('daily', 'morning', 'thanksgiving')
        
        self.assertEqual(prayer.tags.count(), 3)
        tag_names = [tag.name for tag in prayer.tags.all()]
        self.assertIn('daily', tag_names)
        self.assertIn('morning', tag_names)
        self.assertIn('thanksgiving', tag_names)
    
    def test_prayer_translations(self):
        """Test prayer translation fields."""
        prayer = Prayer.objects.create(
            title='Lord Have Mercy',
            text='Lord have mercy on us.',
            category='general',
            church=self.church
        )
        # Set Armenian translation
        prayer.title_hy = 'Տեր ողորմյա'
        prayer.text_hy = 'Տեր ողորմյա մեզ։'
        prayer.save()
        
        # Retrieve and verify
        prayer_retrieved = Prayer.objects.get(pk=prayer.pk)
        self.assertEqual(prayer_retrieved.title_hy, 'Տեր ողորմյա')
        self.assertEqual(prayer_retrieved.text_hy, 'Տեր ողորմյա մեզ։')
    
    def test_prayer_str(self):
        """Test string representation of prayer."""
        prayer = Prayer.objects.create(
            title='Morning Prayer',
            text='Prayer text.',
            category='morning',
            church=self.church
        )
        self.assertEqual(str(prayer), 'Morning Prayer')


class PrayerSetModelTests(TestCase):
    """Test cases for PrayerSet model."""
    
    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name='Test Church')
    
    def test_create_prayer_set(self):
        """Test creating a prayer set."""
        prayer_set = PrayerSet.objects.create(
            title='Morning Prayers',
            description='Collection of morning prayers',
            category='morning',
            church=self.church
        )
        self.assertEqual(prayer_set.title, 'Morning Prayers')
        self.assertEqual(prayer_set.category, 'morning')
        self.assertEqual(prayer_set.church, self.church)
    
    def test_prayer_set_default_category(self):
        """Test that prayer set defaults to 'general' category."""
        prayer_set = PrayerSet.objects.create(
            title='General Prayers',
            church=self.church
        )
        self.assertEqual(prayer_set.category, 'general')
    
    def test_prayer_set_with_prayers(self):
        """Test adding prayers to a prayer set."""
        prayer_set = PrayerSet.objects.create(
            title='Daily Prayers',
            description='Daily prayer collection',
            church=self.church
        )
        
        # Create prayers
        prayer1 = Prayer.objects.create(
            title='Prayer 1',
            text='First prayer',
            category='morning',
            church=self.church
        )
        prayer2 = Prayer.objects.create(
            title='Prayer 2',
            text='Second prayer',
            category='morning',
            church=self.church
        )
        
        # Add prayers to set with ordering
        PrayerSetMembership.objects.create(
            prayer_set=prayer_set,
            prayer=prayer1,
            order=1
        )
        PrayerSetMembership.objects.create(
            prayer_set=prayer_set,
            prayer=prayer2,
            order=2
        )
        
        self.assertEqual(prayer_set.prayers.count(), 2)
        
        # Test ordering
        memberships = prayer_set.memberships.order_by('order')
        self.assertEqual(memberships[0].prayer, prayer1)
        self.assertEqual(memberships[1].prayer, prayer2)
    
    def test_prayer_set_translations(self):
        """Test prayer set translation fields."""
        prayer_set = PrayerSet.objects.create(
            title='Morning Prayers',
            description='Collection of morning prayers',
            church=self.church
        )
        # Set Armenian translation
        prayer_set.title_hy = 'Առավոտյան աղոթքներ'
        prayer_set.description_hy = 'Առավոտյան աղոթքների հավաքածու'
        prayer_set.save()
        
        # Retrieve and verify
        retrieved = PrayerSet.objects.get(pk=prayer_set.pk)
        self.assertEqual(retrieved.title_hy, 'Առավոտյան աղոթքներ')
        self.assertEqual(retrieved.description_hy, 'Առավոտյան աղոթքների հավաքածու')
    
    def test_prayer_set_str(self):
        """Test string representation of prayer set."""
        prayer_set = PrayerSet.objects.create(
            title='Evening Prayers',
            church=self.church
        )
        self.assertEqual(str(prayer_set), 'Evening Prayers')


class PrayerSetMembershipModelTests(TestCase):
    """Test cases for PrayerSetMembership model."""
    
    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name='Test Church')
        self.prayer_set = PrayerSet.objects.create(
            title='Test Set',
            church=self.church
        )
        self.prayer = Prayer.objects.create(
            title='Test Prayer',
            text='Test text',
            category='general',
            church=self.church
        )
    
    def test_create_membership(self):
        """Test creating a prayer set membership."""
        membership = PrayerSetMembership.objects.create(
            prayer_set=self.prayer_set,
            prayer=self.prayer,
            order=1
        )
        self.assertEqual(membership.prayer_set, self.prayer_set)
        self.assertEqual(membership.prayer, self.prayer)
        self.assertEqual(membership.order, 1)
    
    def test_membership_unique_constraint(self):
        """Test that a prayer can't be added twice to the same set."""
        PrayerSetMembership.objects.create(
            prayer_set=self.prayer_set,
            prayer=self.prayer,
            order=1
        )
        
        # Attempting to add the same prayer again should fail
        with self.assertRaises(Exception):
            PrayerSetMembership.objects.create(
                prayer_set=self.prayer_set,
                prayer=self.prayer,
                order=2
            )
    
    def test_membership_str(self):
        """Test string representation of membership."""
        membership = PrayerSetMembership.objects.create(
            prayer_set=self.prayer_set,
            prayer=self.prayer,
            order=1
        )
        expected = f'{self.prayer.title} in {self.prayer_set.title} (order: 1)'
        self.assertEqual(str(membership), expected)


class PrayerAPITests(APITestCase):
    """Test cases for Prayer API endpoints."""
    
    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name='API Test Church')
        self.fast = Fast.objects.create(
            name='API Test Fast',
            church=self.church
        )
        
        self.prayer1 = Prayer.objects.create(
            title='Morning Prayer',
            text='Prayer in the morning',
            category='morning',
            church=self.church
        )
        self.prayer1.tags.add('daily', 'morning')
        
        self.prayer2 = Prayer.objects.create(
            title='Evening Prayer',
            text='Prayer in the evening',
            category='evening',
            church=self.church,
            fast=self.fast
        )
        self.prayer2.tags.add('daily', 'evening')
    
    def test_prayer_list(self):
        """Test listing prayers."""
        url = reverse('prayers:prayer-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
    
    def test_prayer_detail(self):
        """Test retrieving a single prayer."""
        url = reverse('prayers:prayer-detail', kwargs={'pk': self.prayer1.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Morning Prayer')
        self.assertEqual(response.data['category'], 'morning')
        self.assertIn('daily', response.data['tags'])
        self.assertIn('morning', response.data['tags'])
    
    def test_prayer_filter_by_church(self):
        """Test filtering prayers by church."""
        url = reverse('prayers:prayer-list')
        response = self.client.get(url, {'church': self.church.pk})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
    
    def test_prayer_filter_by_category(self):
        """Test filtering prayers by category."""
        url = reverse('prayers:prayer-list')
        response = self.client.get(url, {'category': 'morning'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Morning Prayer')
    
    def test_prayer_filter_by_tags(self):
        """Test filtering prayers by tags."""
        url = reverse('prayers:prayer-list')
        response = self.client.get(url, {'tags': 'morning'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
    
    def test_prayer_filter_by_fast(self):
        """Test filtering prayers by fast."""
        url = reverse('prayers:prayer-list')
        response = self.client.get(url, {'fast': self.fast.pk})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Evening Prayer')
    
    def test_prayer_search(self):
        """Test searching prayers."""
        url = reverse('prayers:prayer-list')
        response = self.client.get(url, {'search': 'morning'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Morning Prayer')


class PrayerSetAPITests(APITestCase):
    """Test cases for PrayerSet API endpoints."""
    
    def setUp(self):
        """Set up test data."""
        self.church = Church.objects.create(name='API Test Church')
        
        self.prayer_set = PrayerSet.objects.create(
            title='Daily Prayers',
            description='Collection of daily prayers',
            church=self.church
        )
        
        # Create and add prayers to the set
        prayer1 = Prayer.objects.create(
            title='Prayer 1',
            text='First prayer',
            category='morning',
            church=self.church
        )
        prayer2 = Prayer.objects.create(
            title='Prayer 2',
            text='Second prayer',
            category='evening',
            church=self.church
        )
        
        PrayerSetMembership.objects.create(
            prayer_set=self.prayer_set,
            prayer=prayer1,
            order=1
        )
        PrayerSetMembership.objects.create(
            prayer_set=self.prayer_set,
            prayer=prayer2,
            order=2
        )
    
    def test_prayer_set_list(self):
        """Test listing prayer sets."""
        url = reverse('prayers:prayer-set-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Daily Prayers')
        self.assertEqual(response.data['results'][0]['prayer_count'], 2)
    
    def test_prayer_set_detail(self):
        """Test retrieving a single prayer set with prayers."""
        url = reverse('prayers:prayer-set-detail', kwargs={'pk': self.prayer_set.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Daily Prayers')
        self.assertEqual(response.data['prayer_count'], 2)
        
        # Check that prayers are included and ordered correctly
        prayers = response.data['prayers']
        self.assertEqual(len(prayers), 2)
        self.assertEqual(prayers[0]['prayer']['title'], 'Prayer 1')
        self.assertEqual(prayers[1]['prayer']['title'], 'Prayer 2')
        self.assertEqual(prayers[0]['order'], 1)
        self.assertEqual(prayers[1]['order'], 2)
    
    def test_prayer_set_filter_by_church(self):
        """Test filtering prayer sets by church."""
        url = reverse('prayers:prayer-set-list')
        response = self.client.get(url, {'church': self.church.pk})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
    
    def test_prayer_set_search(self):
        """Test searching prayer sets."""
        url = reverse('prayers:prayer-set-list')
        response = self.client.get(url, {'search': 'daily'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Daily Prayers')
    
    def test_prayer_set_filter_by_category(self):
        """Test filtering prayer sets by category."""
        # Create additional prayer sets with different categories
        PrayerSet.objects.create(
            title='Morning Prayer Set',
            category='morning',
            church=self.church
        )
        PrayerSet.objects.create(
            title='Evening Prayer Set',
            category='evening',
            church=self.church
        )
        
        url = reverse('prayers:prayer-set-list')
        response = self.client.get(url, {'category': 'morning'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Morning Prayer Set')
        self.assertEqual(response.data['results'][0]['category'], 'morning')


class PrayerBookmarkTests(APITestCase):
    """Test cases for Prayer and PrayerSet bookmarking functionality."""
    
    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.church = Church.objects.create(name='Test Church')
        
        # Create test prayer
        self.prayer = Prayer.objects.create(
            title='Morning Prayer',
            text='Lord, bless this day.',
            category='morning',
            church=self.church
        )
        
        # Create test prayer set
        self.prayer_set = PrayerSet.objects.create(
            title='Daily Prayers',
            description='A collection of daily prayers',
            church=self.church
        )
        
        # Add prayer to prayer set
        PrayerSetMembership.objects.create(
            prayer_set=self.prayer_set,
            prayer=self.prayer,
            order=1
        )
    
    def test_create_prayer_bookmark(self):
        """Test creating a bookmark for a prayer."""
        self.client.force_authenticate(user=self.user)
        url = reverse('bookmark-create')
        data = {
            'content_type': 'prayer',
            'object_id': self.prayer.id,
            'note': 'Beautiful morning prayer'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['content_type_name'], 'prayer')
        self.assertEqual(response.data['object_id'], self.prayer.id)
        self.assertEqual(response.data['note'], 'Beautiful morning prayer')
        
        # Verify bookmark was created in database
        bookmark = Bookmark.objects.get(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Prayer),
            object_id=self.prayer.id
        )
        self.assertIsNotNone(bookmark)
    
    def test_create_prayerset_bookmark(self):
        """Test creating a bookmark for a prayer set."""
        self.client.force_authenticate(user=self.user)
        url = reverse('bookmark-create')
        data = {
            'content_type': 'prayerset',
            'object_id': self.prayer_set.id,
            'note': 'Great collection'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['content_type_name'], 'prayerset')
        self.assertEqual(response.data['object_id'], self.prayer_set.id)
        
        # Verify bookmark was created in database
        bookmark = Bookmark.objects.get(
            user=self.user,
            content_type=ContentType.objects.get_for_model(PrayerSet),
            object_id=self.prayer_set.id
        )
        self.assertIsNotNone(bookmark)
    
    def test_prayer_is_bookmarked_field_authenticated(self):
        """Test that is_bookmarked field shows correctly for authenticated users."""
        # Create bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Prayer),
            object_id=self.prayer.id
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('prayers:prayer-detail', kwargs={'pk': self.prayer.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_bookmarked'])
    
    def test_prayer_is_bookmarked_field_unauthenticated(self):
        """Test that is_bookmarked field returns False for unauthenticated users."""
        url = reverse('prayers:prayer-detail', kwargs={'pk': self.prayer.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_bookmarked'])
    
    def test_prayerset_is_bookmarked_field(self):
        """Test that is_bookmarked field shows correctly for prayer sets."""
        # Create bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(PrayerSet),
            object_id=self.prayer_set.id
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('prayers:prayer-set-detail', kwargs={'pk': self.prayer_set.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_bookmarked'])
    
    def test_prayerset_list_is_bookmarked_field(self):
        """Test that is_bookmarked field shows correctly in prayer set list view."""
        # Create bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(PrayerSet),
            object_id=self.prayer_set.id
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('prayers:prayer-set-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['results'][0]['is_bookmarked'])
    
    def test_delete_prayer_bookmark(self):
        """Test deleting a prayer bookmark."""
        # Create bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Prayer),
            object_id=self.prayer.id
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('bookmark-delete', kwargs={
            'content_type': 'prayer',
            'object_id': self.prayer.id
        })
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        
        # Verify bookmark was deleted
        bookmark_exists = Bookmark.objects.filter(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Prayer),
            object_id=self.prayer.id
        ).exists()
        self.assertFalse(bookmark_exists)
    
    def test_duplicate_bookmark_prevention(self):
        """Test that users cannot bookmark the same prayer twice."""
        # Create first bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Prayer),
            object_id=self.prayer.id
        )
        
        # Try to create duplicate bookmark
        self.client.force_authenticate(user=self.user)
        url = reverse('bookmark-create')
        data = {
            'content_type': 'prayer',
            'object_id': self.prayer.id
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already bookmarked', str(response.data))
    
    def test_bookmark_check_endpoint_prayer(self):
        """Test checking if a prayer is bookmarked."""
        # Create bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Prayer),
            object_id=self.prayer.id
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('bookmark-check', kwargs={
            'content_type': 'prayer',
            'object_id': self.prayer.id
        })
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_bookmarked'])
    
    def test_bookmark_list_includes_prayers(self):
        """Test that bookmark list endpoint includes bookmarked prayers."""
        # Create prayer bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Prayer),
            object_id=self.prayer.id,
            note='My favorite prayer'
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('bookmark-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        
        bookmark_data = response.data['results'][0]
        self.assertEqual(bookmark_data['content_type_name'], 'prayer')
        self.assertEqual(bookmark_data['object_id'], self.prayer.id)
        self.assertEqual(bookmark_data['note'], 'My favorite prayer')
        
        # Check content representation
        content = bookmark_data['content']
        self.assertEqual(content['id'], self.prayer.id)
        self.assertEqual(content['type'], 'prayer')
        self.assertEqual(content['title'], 'Morning Prayer')
        self.assertIn('Lord, bless this day.', content['description'])
    
    def test_bookmark_list_includes_prayersets(self):
        """Test that bookmark list endpoint includes bookmarked prayer sets."""
        # Create prayer set bookmark
        Bookmark.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(PrayerSet),
            object_id=self.prayer_set.id,
            note='Essential prayers'
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('bookmark-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        
        bookmark_data = response.data['results'][0]
        self.assertEqual(bookmark_data['content_type_name'], 'prayerset')
        self.assertEqual(bookmark_data['object_id'], self.prayer_set.id)
        
        # Check content representation
        content = bookmark_data['content']
        self.assertEqual(content['id'], self.prayer_set.id)
        self.assertEqual(content['type'], 'prayerset')
        self.assertEqual(content['title'], 'Daily Prayers')
        self.assertEqual(content['description'], 'A collection of daily prayers')
    
    def test_bookmark_requires_authentication(self):
        """Test that creating bookmarks requires authentication."""
        url = reverse('bookmark-create')
        data = {
            'content_type': 'prayer',
            'object_id': self.prayer.id
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

