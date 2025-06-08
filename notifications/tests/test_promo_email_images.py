"""Tests for PromoEmailImage model and functionality."""
import os
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from notifications.models import PromoEmailImage


class PromoEmailImageTests(TestCase):
    """Tests for the PromoEmailImage model."""
    
    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
    
    def create_test_image(self):
        """Create a simple test image file."""
        # Create a simple 1x1 pixel PNG image
        image_data = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13'
            b'\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\nIDATx\x9cc```'
            b'\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        return SimpleUploadedFile(
            name='test_image.png',
            content=image_data,
            content_type='image/png'
        )
    
    def test_promo_email_image_creation(self):
        """Test creating a PromoEmailImage instance."""
        image_file = self.create_test_image()
        
        promo_image = PromoEmailImage.objects.create(
            name='Test Image',
            image=image_file,
            description='A test image for promotional emails',
            uploaded_by=self.user
        )
        
        self.assertEqual(promo_image.name, 'Test Image')
        self.assertEqual(promo_image.description, 'A test image for promotional emails')
        self.assertEqual(promo_image.uploaded_by, self.user)
        self.assertTrue(promo_image.image.name.startswith('promo_email_images/'))
        self.assertIsNotNone(promo_image.created_at)
        self.assertIsNotNone(promo_image.updated_at)
    
    def test_promo_email_image_str_method(self):
        """Test the string representation of PromoEmailImage."""
        image_file = self.create_test_image()
        
        promo_image = PromoEmailImage.objects.create(
            name='Company Logo',
            image=image_file,
            uploaded_by=self.user
        )
        
        self.assertEqual(str(promo_image), 'Company Logo')
    
    def test_get_absolute_url_development(self):
        """Test get_absolute_url method in development environment."""
        image_file = self.create_test_image()
        
        promo_image = PromoEmailImage.objects.create(
            name='Test Image',
            image=image_file,
            uploaded_by=self.user
        )
        
        url = promo_image.get_absolute_url()
        
        # In test environment, should include the image URL
        self.assertTrue(url.endswith('.png'))
        self.assertIn('promo_email_images/', url)
    
    def test_promo_email_image_without_uploaded_by(self):
        """Test creating PromoEmailImage without uploaded_by field."""
        image_file = self.create_test_image()
        
        promo_image = PromoEmailImage.objects.create(
            name='Anonymous Image',
            image=image_file,
            description='Image uploaded anonymously'
        )
        
        self.assertIsNone(promo_image.uploaded_by)
        self.assertEqual(promo_image.name, 'Anonymous Image')
    
    def test_promo_email_image_ordering(self):
        """Test that PromoEmailImages are ordered by created_at descending."""
        image_file1 = self.create_test_image()
        image_file2 = self.create_test_image()
        
        # Create first image
        image1 = PromoEmailImage.objects.create(
            name='First Image',
            image=image_file1,
            uploaded_by=self.user
        )
        
        # Create second image
        image2 = PromoEmailImage.objects.create(
            name='Second Image',
            image=image_file2,
            uploaded_by=self.user
        )
        
        # Check ordering (most recent first)
        images = list(PromoEmailImage.objects.all())
        self.assertEqual(images[0], image2)  # Second image should be first
        self.assertEqual(images[1], image1)  # First image should be second
    
    def test_promo_email_image_verbose_names(self):
        """Test the verbose names of the model."""
        meta = PromoEmailImage._meta
        self.assertEqual(meta.verbose_name, "Promo Email Image")
        self.assertEqual(meta.verbose_name_plural, "Promo Email Images")