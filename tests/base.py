"""Base test classes and utilities for the test suite."""
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APITestCase
from tests.fixtures.test_data import TestDataFactory


class BaseTestCase(TestCase):
    """Base test case with common functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Set up class-level test data."""
        super().setUpClass()
        cls.factory = TestDataFactory
    
    def create_user(self, **kwargs):
        """Convenience method to create a user."""
        return self.factory.create_user(**kwargs)
    
    def create_church(self, **kwargs):
        """Convenience method to create a church."""
        return self.factory.create_church(**kwargs)
    
    def create_fast(self, **kwargs):
        """Convenience method to create a fast."""
        return self.factory.create_fast(**kwargs)
    
    def create_profile(self, **kwargs):
        """Convenience method to create a profile."""
        return self.factory.create_profile(**kwargs)


class BaseAPITestCase(APITestCase):
    """Base API test case with common functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Set up class-level test data."""
        super().setUpClass()
        cls.factory = TestDataFactory
    
    def create_user(self, **kwargs):
        """Convenience method to create a user."""
        return self.factory.create_user(**kwargs)
    
    def create_church(self, **kwargs):
        """Convenience method to create a church."""
        return self.factory.create_church(**kwargs)
    
    def create_fast(self, **kwargs):
        """Convenience method to create a fast."""
        return self.factory.create_fast(**kwargs)
    
    def create_profile(self, **kwargs):
        """Convenience method to create a profile."""
        return self.factory.create_profile(**kwargs)
    
    def create_video(self, **kwargs):
        """Convenience method to create a video."""
        return self.factory.create_video(**kwargs)
    
    def create_article(self, **kwargs):
        """Convenience method to create an article."""
        return self.factory.create_article(**kwargs)
    
    def create_recipe(self, **kwargs):
        """Convenience method to create a recipe."""
        return self.factory.create_recipe(**kwargs)
    
    def create_devotional_set(self, **kwargs):
        """Convenience method to create a devotional set."""
        return self.factory.create_devotional_set(**kwargs)
    
    def create_bookmark(self, **kwargs):
        """Convenience method to create a bookmark."""
        return self.factory.create_bookmark(**kwargs)
    
    def authenticate(self, user=None):
        """Authenticate the test client with a user."""
        if user is None:
            user = self.create_user()
        self.client.force_authenticate(user=user)
        return user


class BaseTransactionTestCase(TransactionTestCase):
    """Base transaction test case for tests that need transactions."""
    
    @classmethod
    def setUpClass(cls):
        """Set up class-level test data."""
        super().setUpClass()
        cls.factory = TestDataFactory
    
    def create_user(self, **kwargs):
        """Convenience method to create a user."""
        return self.factory.create_user(**kwargs)
    
    def create_church(self, **kwargs):
        """Convenience method to create a church."""
        return self.factory.create_church(**kwargs)
    
    def create_fast(self, **kwargs):
        """Convenience method to create a fast."""
        return self.factory.create_fast(**kwargs)
    
    def create_profile(self, **kwargs):
        """Convenience method to create a profile."""
        return self.factory.create_profile(**kwargs)
    
    def create_video(self, **kwargs):
        """Convenience method to create a video."""
        return self.factory.create_video(**kwargs)
    
    def create_article(self, **kwargs):
        """Convenience method to create an article."""
        return self.factory.create_article(**kwargs)
    
    def create_recipe(self, **kwargs):
        """Convenience method to create a recipe."""
        return self.factory.create_recipe(**kwargs)
    
    def create_devotional_set(self, **kwargs):
        """Convenience method to create a devotional set."""
        return self.factory.create_devotional_set(**kwargs)
    
    def create_bookmark(self, **kwargs):
        """Convenience method to create a bookmark."""
        return self.factory.create_bookmark(**kwargs)