"""Models for notifications app."""
import logging
import re

from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

User = get_user_model()

# Create your models here.

class DeviceToken(models.Model):
    IOS = 'ios'
    ANDROID = 'android'
    WEB = 'web'
    DEVICE_TYPES = [
        (IOS, 'iOS'),
        (ANDROID, 'Android'),
        (WEB, 'Web'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='device_tokens', null=True)
    token = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=10, choices=DEVICE_TYPES, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)

    def clean(self):
        pattern = r'^ExponentPushToken\[[a-zA-Z0-9_-]+\]$'
        if not bool(re.match(pattern, self.token)):
            raise ValidationError('Invalid Expo push token format')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.token

    class Meta:
        ordering = ['-created_at']

class PromoEmail(models.Model):
    DRAFT = 'draft'
    SCHEDULED = 'scheduled'
    SENDING = 'sending'
    SENT = 'sent'
    CANCELED = 'canceled'
    FAILED = 'failed'
    
    STATUS_CHOICES = [
        (DRAFT, 'Draft'),
        (SCHEDULED, 'Scheduled'),
        (SENDING, 'Sending'),
        (SENT, 'Sent'),
        (CANCELED, 'Canceled'),
        (FAILED, 'Failed'),
    ]
    
    title = models.CharField(max_length=200)
    subject = models.CharField(max_length=200)
    content_html = models.TextField()
    content_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    sent_at = models.DateTimeField(null=True, blank=True)
    fast = models.ForeignKey('hub.Fast', null=True, blank=True, on_delete=models.SET_NULL, related_name='promo_emails')
    
    # Targeting options
    all_users = models.BooleanField(default=False, help_text="Send to all users")
    church_filter = models.ForeignKey('hub.Church', null=True, blank=True, on_delete=models.SET_NULL)
    joined_fast = models.ForeignKey(
        'hub.Fast', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL,
        help_text="Send to users who joined this fast"
    )
    exclude_unsubscribed = models.BooleanField(
        default=True, 
        help_text="Exclude users who have unsubscribed from promotional emails"
    )
    selected_users = models.ManyToManyField(
        User,
        blank=True,
        related_name='selected_for_promo_emails',
        help_text="Select specific users to send this email to. Overrides filters if any users are selected."
    )
    
    def get_target_audience_users(self):
        """
        Returns a QuerySet of User objects based on the targeting options
        (all_users, church_filter, joined_fast, exclude_unsubscribed).
        """
        # Import here to avoid circular dependency if Profile imports PromoEmail
        from hub.models import Profile 
        User = get_user_model() 

        users_qs = User.objects.none() # Start with empty queryset

        if self.all_users:
            # If all_users is checked, start with all active users
            users_qs = User.objects.filter(is_active=True)
        else:
            # Filter based on profiles if specific filters are set
            profiles = Profile.objects.filter(user__is_active=True) # Ensure we only consider active users via profile
            if self.church_filter:
                profiles = profiles.filter(church=self.church_filter)
            if self.joined_fast:
                # Assuming Profile has a ManyToManyField 'fasts' to hub.Fast
                profiles = profiles.filter(fasts=self.joined_fast)
            
            # If neither church nor fast filter is active, but all_users is false,
            # it implies no one should be targeted by filters (unless specific users are selected later).
            # However, if *some* filter was applied, get the corresponding users.
            if self.church_filter or self.joined_fast:
                user_ids = profiles.values_list('user_id', flat=True)
                users_qs = User.objects.filter(pk__in=user_ids)
            # If no filters are set and not all_users, the queryset remains empty

        if self.exclude_unsubscribed:
            # Further filter the current user set based on the receive_promotional_emails flag on Profile
            # We need to filter the users_qs, not Profile.objects.all()
            subscribed_user_ids = Profile.objects.filter(
                user__in=users_qs, # Filter profiles belonging to the current user set
                receive_promotional_emails=True
            ).values_list('user_id', flat=True)
            users_qs = users_qs.filter(pk__in=subscribed_user_ids)
            
        return users_qs.distinct()

    def __str__(self):
        return self.title
    
    def clean(self):
        """Validate the model fields."""
        super().clean()
        # No specific validation needed for selected_users here, handled by ManyToManyField
    
    def recipient_count(self):
        """Get count of recipients based on selected users or filters"""
        # Check self.pk because ManyToMany relations require the instance to have an ID
        if self.pk and self.selected_users.exists():
            return self.selected_users.count()

        # Otherwise, calculate count based on filters
        # Use the dedicated method to get the filtered users
        return self.get_target_audience_users().count()
    
    def send_preview(self, email_address):
        """Send a preview of this email to a specified email address
        
        Args:
            email_address: Email address to send preview to
        
        Returns:
            dict: Result of the preview operation
        """
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string
        from django.utils.html import strip_tags
        from django.conf import settings
        from django.urls import reverse
        
        logger = logging.getLogger(__name__)
        
        if not email_address:
            return {'success': False, 'error': 'No email address provided'}
        
        try:
            # Use admin as the preview user
            from django.contrib.auth import get_user_model
            User = get_user_model()
            admin_user = User.objects.filter(is_staff=True).first()
            
            # Create unsubscribe URL for preview
            unsubscribe_token = f"0:preview" # Use a placeholder token for preview
            unsubscribe_url = f"{settings.BACKEND_URL}{reverse('notifications:unsubscribe')}?token={unsubscribe_token}"
            
            # Prepare email content with preview markers
            context = {
                'user': admin_user,
                'title': self.title,
                'email_content': f"[PREVIEW] {self.content_html}",
                'unsubscribe_url': unsubscribe_url,
                'site_url': settings.FRONTEND_URL
            }
            
            from_email = f"Fast and Pray <{settings.EMAIL_HOST_USER}>"
            
            html_content = render_to_string('email/promotional_email.html', context)
            text_content = self.content_text or strip_tags(html_content)
            
            # Create and send email with [PREVIEW] in subject
            email = EmailMultiAlternatives(
                f"[PREVIEW] {self.subject}",
                text_content,
                from_email,
                [email_address]
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
            
            logger.info(f"Sent preview email for '{self.title}' to {email_address}")
            
            return {'success': True, 'to': email_address}
            
        except Exception as e:
            logger.error(f"Failed to send preview email: {str(e)}")
            return {'success': False, 'error': str(e)}
