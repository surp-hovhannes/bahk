"""Test data factory methods for Django tests."""
import datetime
from django.contrib.auth.models import User
from hub.models import Church, Day, Fast, Profile
from notifications.models import PromoEmail


class TestDataFactory:
    """Factory class for creating test data."""
    
    @staticmethod
    def create_user(username=None, email=None, password="testpass123"):
        """Create a test user compatible with EmailBackend authentication."""
        # For EmailBackend compatibility, username should be an email
        if email is None and username is None:
            # Generate a unique email if neither is provided
            import time
            email = f"testuser_{int(time.time()*1000)}@example.com"
            username = email
        elif email is None:
            # If only username provided, use it as email if it's email format
            if "@" in username:
                email = username
            else:
                email = f"{username}@example.com"
                username = email  # Use email as username for EmailBackend
        elif username is None:
            # If only email provided, use email as username
            username = email
        
        return User.objects.create_user(
            username=username,
            email=email,
            password=password
        )
    
    @staticmethod
    def create_church(name=None):
        """Create a test church."""
        if name is None:
            # Generate a unique name if none provided
            import time
            import random
            timestamp = int(time.time() * 1000000)  # More granular timestamp (microseconds)
            random_suffix = random.randint(1000, 9999)
            name = f"Test Church {timestamp}_{random_suffix}"
        return Church.objects.create(name=name)
    
    @staticmethod
    def create_fast(name=None, church=None, description="A test fast"):
        """Create a test fast."""
        if name is None:
            # Generate a unique name if none provided
            import time
            import random
            timestamp = int(time.time() * 1000000)  # More granular timestamp (microseconds) 
            random_suffix = random.randint(1000, 9999)
            name = f"Test Fast {timestamp}_{random_suffix}"
        if church is None:
            church = TestDataFactory.create_church()
        return Fast.objects.create(
            name=name,
            church=church,
            description=description
        )
    
    @staticmethod
    def create_profile(user=None, church=None, **kwargs):
        """Create a test profile."""
        if user is None:
            user = TestDataFactory.create_user()
        if church is None:
            church = TestDataFactory.create_church()
        
        defaults = {
            'user': user,
            'church': church,
            'receive_promotional_emails': True,
            'receive_upcoming_fast_reminders': True,
        }
        defaults.update(kwargs)
        
        return Profile.objects.create(**defaults)
    
    @staticmethod
    def create_day(date=None, church=None):
        """Create a test day."""
        if date is None:
            date = datetime.date.today()
        if church is None:
            church = TestDataFactory.create_church()
        return Day.objects.create(date=date, church=church)
    
    @staticmethod
    def create_complete_fast(church=None, num_days=5, num_participants=2):
        """Create a complete fast with days and participants."""
        if church is None:
            church = TestDataFactory.create_church()
        
        # Create fast
        fast = TestDataFactory.create_fast(church=church)
        
        # Create days
        today = datetime.date.today()
        for i in range(-2, num_days - 2):
            day = TestDataFactory.create_day(
                date=today + datetime.timedelta(days=i),
                church=church
            )
            fast.days.add(day)
        
        # Create participants
        for i in range(num_participants):
            user = TestDataFactory.create_user(username=f"participant{i}")
            profile = TestDataFactory.create_profile(user=user, church=church)
            profile.fasts.add(fast)
        
        return fast
    
    @staticmethod
    def create_promo_email(fast=None):
        """Create a test promotional email."""
        return PromoEmail.objects.create(
            fast=fast,
            title="Test Promo Email",
            subject="Test Promo Email",
            content_html="<div>Test Promo Email</div>",
            content_text="Test Promo Email",
            all_users=True,
            exclude_unsubscribed=False,
        )
    
    @staticmethod
    def create_test_data_set():
        """Create a complete set of test data."""
        # Create churches
        church1 = TestDataFactory.create_church(name="Church 1")
        church2 = TestDataFactory.create_church(name="Church 2")
        
        # Create users and profiles
        user1 = TestDataFactory.create_user(username="user1")
        profile1 = TestDataFactory.create_profile(user=user1, church=church1)
        
        user2 = TestDataFactory.create_user(username="user2")
        profile2 = TestDataFactory.create_profile(user=user2, church=church1)
        
        user3 = TestDataFactory.create_user(username="user3")
        profile3 = TestDataFactory.create_profile(user=user3, church=church2)
        
        # Create fasts
        fast1 = TestDataFactory.create_complete_fast(church=church1, num_participants=0)
        fast2 = TestDataFactory.create_complete_fast(church=church2, num_participants=0)
        
        # Add participants manually to have control
        profile1.fasts.add(fast1)
        profile2.fasts.add(fast1)
        profile3.fasts.add(fast2)
        
        return {
            'churches': [church1, church2],
            'users': [user1, user2, user3],
            'profiles': [profile1, profile2, profile3],
            'fasts': [fast1, fast2],
        }