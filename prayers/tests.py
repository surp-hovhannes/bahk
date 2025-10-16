"""Tests for prayers app."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from hub.models import Church, Fast
from prayers.models import Prayer, PrayerSet, PrayerSetMembership


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
            church=self.church
        )
        self.assertEqual(prayer_set.title, 'Morning Prayers')
        self.assertEqual(prayer_set.church, self.church)
    
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

