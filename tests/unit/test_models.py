"""Tests models."""
import datetime
import os
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError
from django.test import TestCase, TransactionTestCase
from tests.fixtures.test_data import TestDataFactory

from hub.models import Church, Day, Fast, Profile


class ModelCreationTests(TestCase):
    """Tests for basic model creation."""
    
    def setUp(self):
        """Set up test data."""
        # Mock the invalidate_cache method to avoid Redis dependency
        self.patcher = patch('hub.views.fast.FastListView.invalidate_cache')
        self.mock_invalidate_cache = self.patcher.start()
        
        # Use TestDataFactory for consistent setup
        self.sample_user = TestDataFactory.create_user(username="sample_user@example.com")
        self.another_user = TestDataFactory.create_user(username="another_user@example.com")
        self.sample_church = TestDataFactory.create_church(name="Sample Church")
        self.sample_fast = TestDataFactory.create_fast(
            name="Sample Fast", 
            church=self.sample_church, 
            description="A sample fast."
        )
        self.another_fast = TestDataFactory.create_fast(
            name="Another Fast", 
            church=self.sample_church, 
            description="Another sample fast."
        )
    
    def tearDown(self):
        """Stop the patcher."""
        self.patcher.stop()
    
    def test_create_church(self):
        """Tests creation of a Church model object."""
        name = "Armenian Apostolic Church - Test"  # Add suffix to ensure uniqueness
        church = TestDataFactory.create_church(name=name)
        self.assertIsNotNone(church)
        self.assertEqual(church.name, name)
    
    def test_create_fast(self):
        """Tests creation of a Fast model object."""
        name = "Fast of the Catechumens"
        fast = TestDataFactory.create_fast(name=name, church=self.sample_church)
        self.assertIsNotNone(fast)
        self.assertEqual(fast.name, name)
    
    def test_fast_image_upload(self):
        """Tests image upload for a Fast model object."""
        name = "Test Fast"
        church = TestDataFactory.create_church(name="Test Church")
        image_path = os.path.join(settings.BASE_DIR, 'hub', 'static', 'images', 'img.jpg')
        
        # Check if image exists, if not skip test
        if not os.path.exists(image_path):
            self.skipTest(f"Test image not found at {image_path}")
            
        with open(image_path, 'rb') as img_file:
            image = SimpleUploadedFile(
                name='img.jpg', 
                content=img_file.read(), 
                content_type='image/jpeg'
            )
        fast = Fast.objects.create(name=name, church=church, image=image)
        self.assertIsNotNone(fast)
        self.assertEqual(fast.name, name)
        self.assertTrue(fast.image)
    
    def test_create_user_profile(self):
        """Tests creation of a profile for a user."""
        profile = TestDataFactory.create_profile(user=self.sample_user)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.user, self.sample_user)
        self.assertEqual(self.sample_user.profile, profile)
    
    def test_profile_image_upload(self):
        """Tests image upload for a Profile model object."""
        image_path = os.path.join(settings.BASE_DIR, 'hub', 'static', 'images', 'img.jpg')
        
        # Check if image exists, if not skip test
        if not os.path.exists(image_path):
            self.skipTest(f"Test image not found at {image_path}")
            
        with open(image_path, 'rb') as img_file:
            image = SimpleUploadedFile(
                name='img.jpg', 
                content=img_file.read(), 
                content_type='image/jpeg'
            )
        profile = TestDataFactory.create_profile(user=self.sample_user, profile_image=image)
        
        self.assertTrue(profile.profile_image.name.startswith('profile_images/'))
        self.assertTrue(profile.profile_image.name.endswith('.jpg'))
    
    def test_create_day(self):
        """Tests creation of a Day model object for today."""
        date = datetime.date.today()
        day = TestDataFactory.create_day(date=date)
        self.assertIsNotNone(day)
        self.assertEqual(day.date, date)
    
    def test_create_duplicate_days(self):
        """Tests that duplicate days can be created (Days are not unique by date alone)."""
        date = datetime.date.today()
        day1 = TestDataFactory.create_day(date=date)
        # Days are not unique by date alone, so this should succeed
        day2 = TestDataFactory.create_day(date=date)
        self.assertIsNotNone(day2)
        self.assertEqual(day2.date, date)


class CompleteModelTests(TestCase):
    """Tests for complete model creation with relationships."""
    
    def setUp(self):
        """Set up test data."""
        # Mock the invalidate_cache method to avoid Redis dependency
        self.patcher = patch('hub.views.fast.FastListView.invalidate_cache')
        self.mock_invalidate_cache = self.patcher.start()
        
        # Use TestDataFactory for consistent setup
        self.sample_user = TestDataFactory.create_user(username="sample_user@example.com")
        self.another_user = TestDataFactory.create_user(username="another_user@example.com")
        self.sample_church = TestDataFactory.create_church(name="Sample Church")
        self.sample_fast = TestDataFactory.create_fast(
            name="Sample Fast", 
            church=self.sample_church, 
            description="A sample fast."
        )
        self.another_fast = TestDataFactory.create_fast(
            name="Another Fast", 
            church=self.sample_church, 
            description="Another sample fast."
        )
        self.sample_profile = TestDataFactory.create_profile(user=self.sample_user)
        self.another_profile = TestDataFactory.create_profile(user=self.another_user)
    
    def tearDown(self):
        """Stop the patcher."""
        self.patcher.stop()
    
    def test_create_complete_user_profile(self):
        """Tests creation of full user profile."""
        # Create a new user for this test using TestDataFactory
        user = TestDataFactory.create_user(username="test_complete_user@example.com")
        profile = TestDataFactory.create_profile(
            user=user, 
            church=self.sample_church,
        )
        profile.fasts.set([self.sample_fast, self.another_fast])
        
        self.assertEqual(profile.user, user)
        self.assertEqual(user.profile, profile)
        self.assertEqual(profile.church, self.sample_church)
        self.assertEqual(
            set(profile.fasts.all()), 
            {self.sample_fast, self.another_fast}
        )
        self.assertIn(profile, self.sample_fast.profiles.all())
        self.assertIn(profile, self.another_fast.profiles.all())
        self.assertIn(profile, self.sample_church.profiles.all())
    
    def test_create_complete_fast(self):
        """Tests creation of a full fast with church and days."""
        name = "Completely Specified Fast"
        today = TestDataFactory.create_day(date=datetime.date.today())
        tomorrow = TestDataFactory.create_day(
            date=datetime.date.today() + datetime.timedelta(days=1)
        )
        fast = TestDataFactory.create_fast(name=name, church=self.sample_church)
        fast.profiles.set([self.sample_profile, self.another_profile])
        
        # Update days to reference the fast
        today.fast = fast
        today.save()
        tomorrow.fast = fast
        tomorrow.save()
        
        self.assertEqual(fast.name, name)
        self.assertEqual(fast.church, self.sample_church)
        self.assertEqual(
            set(fast.profiles.all()), 
            {self.sample_profile, self.another_profile}
        )
        self.assertIn(fast, self.sample_profile.fasts.all())
        self.assertIn(fast, self.another_profile.fasts.all())
        
        # Check the forward relationship from Fast to Day
        self.assertEqual(set(fast.days.all()), {today, tomorrow})
        
        # Check the reverse relationship from Day to Fast
        self.assertEqual(today.fast, fast)
        self.assertEqual(tomorrow.fast, fast)
    
    def test_create_complete_church(self):
        """Tests creation of a completely specified church with fasts and user profiles."""
        name = "Completely Specified Church"
        church = TestDataFactory.create_church(name=name)
        
        # Update profiles to belong to this church
        self.sample_profile.church = church
        self.sample_profile.save()
        self.another_profile.church = church
        self.another_profile.save()
        
        self.assertEqual(self.sample_profile.church, church)
        self.assertEqual(self.another_profile.church, church)
        
        # Update fasts to belong to this church
        self.sample_fast.church = church
        self.sample_fast.save(update_fields=["church"])
        self.another_fast.church = church
        self.another_fast.save(update_fields=["church"])
        
        self.assertEqual(
            set(church.fasts.all()), 
            {self.sample_fast, self.another_fast}
        )


class ModelConstraintTests(TransactionTestCase):
    """Tests for model constraints."""
    
    def setUp(self):
        """Set up test data."""
        # Mock the invalidate_cache method to avoid Redis dependency
        self.patcher = patch('hub.views.fast.FastListView.invalidate_cache')
        self.mock_invalidate_cache = self.patcher.start()
    
    def tearDown(self):
        """Stop the patcher."""
        self.patcher.stop()
    
    def test_constraint_unique_fast_name_church(self):
        """Tests that two fasts with the same name, church, and year cannot be created."""
        fast_name = "fast"
        church = TestDataFactory.create_church(name="church")
        year = 2024
        
        # Create first fast with explicit year using TestDataFactory
        fast = Fast.objects.create(name=fast_name, church=church, year=year)
        
        # Try to create duplicate with same name, church, and year
        with self.assertRaises(IntegrityError):
            duplicate_fast = Fast.objects.create(
                name=fast_name, 
                church=church, 
                year=year,
                description="now there's a description"
            )
