"""Serializers for handling API requests."""
import datetime
import re
import logging
from django.utils import timezone  # Add missing timezone import

from better_profanity import profanity
from django.contrib.auth.models import Group, User
from django.db.models.signals import post_save
from django.dispatch import receiver
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_str
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.conf import settings

from hub import models
from learning_resources.serializers import VideoSerializer
from hub.mixins import ThumbnailCacheMixin


class ProfileSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    thumbnail = serializers.SerializerMethodField()

    def get_thumbnail(self, obj):
        if obj.profile_image:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'profile_image', 'profile_image_thumbnail')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.profile_image_thumbnail.url
            except:
                return None
        return None

    class Meta:
        model = models.Profile
        fields = ['user_id', 'email', 'name', 'profile_image', 'thumbnail',
                 'location', 'church', 'receive_upcoming_fast_reminders', 
                 'receive_upcoming_fast_push_notifications', 
                 'receive_ongoing_fast_push_notifications',
                 'receive_daily_fast_push_notifications',
                 'include_weekly_fasts_in_notifications']


class ProfileRegistrationSerializer(serializers.ModelSerializer):
    church = serializers.PrimaryKeyRelatedField(queryset=models.Church.objects.all(), required=True)

    class Meta:
        model = models.Profile
        fields = ('name', 'church',)

    def validate(self, attrs):
        name = attrs.get('name')
        if name is not None and profanity.contains_profanity(name):
            raise serializers.ValidationError({'name': f'The name entered is suspected of profanity.'})
        return attrs


class ProfileImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Profile
        fields = ['profile_image']

class UserSerializer(serializers.HyperlinkedModelSerializer):
    profile = ProfileSerializer(read_only=True)  # Include profile information

    class Meta:
        model = User
        fields = ["url", "email", "groups", "profile"]

class GroupSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Group
        fields = ["url", "name"]


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)
    profile = ProfileRegistrationSerializer()

    class Meta:
        model = User
        fields = ('password', 'password2', 'email', 'profile',)

    def validate(self, attrs):
        super().validate(attrs)
        email = attrs['email']
        s1, s2 = email.split('@')
        s2, s3 = s2.split('.')
        if any(profanity.contains_profanity(s) for s in [s1, s2, s3]):
            raise serializers.ValidationError({'email': f'The email entered is suspected of profanity.'})
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})

        return attrs

    def create(self, validated_data):
        user = User.objects.create(
            username=validated_data['email'],
            email=validated_data['email'],
        )
        user.set_password(validated_data['password'])
        user.save()
        profile = validated_data.pop('profile')
        church = profile.pop('church')
        name = profile.pop('name')
        models.Profile.objects.create(user=user, name=name, church=church)

        # Generate tokens
        refresh = RefreshToken.for_user(user)
        tokens = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }

        # Add tokens to the response
        user.tokens = tokens
        return user

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        profile = models.Profile.objects.get(user=instance)
        representation['church'] = profile.church.id
        
        # Include tokens in the response
        if hasattr(instance, 'tokens'):
            representation['tokens'] = instance.tokens

        return representation


class ChurchSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Church
        fields = ["id", "name"]


# Add signal to handle thumbnail caching
@receiver(post_save, sender=models.Fast)
def update_thumbnail_cache(sender, instance, **kwargs):
    """Update thumbnail cache in the background after save."""
    if instance.image and not instance.cached_thumbnail_url:
        try:
            url = instance.image_thumbnail.url
            # Use update to avoid recursive signal triggering
            models.Fast.objects.filter(id=instance.id).update(
                cached_thumbnail_url=url,
                cached_thumbnail_updated=timezone.now()
            )
        except Exception as e:
            logging.error(f"Error caching thumbnail URL for Fast {instance.id}: {e}")


class FastSerializer(serializers.ModelSerializer, ThumbnailCacheMixin):
    church = ChurchSerializer()
    participant_count = serializers.SerializerMethodField()
    countdown = serializers.SerializerMethodField()
    days_to_feast = serializers.SerializerMethodField()
    start_date = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()
    joined = serializers.SerializerMethodField()
    has_passed = serializers.SerializerMethodField()
    next_fast_date = serializers.SerializerMethodField()
    total_number_of_days = serializers.SerializerMethodField()
    current_day_number = serializers.SerializerMethodField()
    modal_id = serializers.ReadOnlyField()
    thumbnail = serializers.SerializerMethodField()

    def get_church(self, obj):
        return ChurchSerializer(obj.church, context=self.context).data if obj.church else None

    def get_joined(self, obj):
        # Check if 'request' is present in the context
        request = self.context.get('request')
        
        # If there's no request or the user is not authenticated, return False
        if request is None or not request.user.is_authenticated:
            return False
        
        # If the user is authenticated, check if they have joined the fast
        return request.user.profile.fasts.filter(id=obj.id).exists()

    def get_participant_count(self, obj):
        return obj.profiles.count()
    
    def get_countdown(self, obj):
        current_date = timezone.localdate(timezone=self.context.get('tz'))
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - current_date).days
            if days_to_feast < 0:
                return f"{obj.culmination_feast} has passed"
            return f"<span class='days_to_finish'>{days_to_feast}</span> day{'' if days_to_feast == 1 else 's'} until {obj.culmination_feast}"
        
        days = [day.date for day in obj.days.all()]
        if not days:
            return f"No days available for {obj.name}"
        
        finish_date = max(days)
        days_to_finish = (finish_date - current_date).days + 1  # + 1 to get days until first day *after* fast
        if days_to_finish < 0:
            return f"{obj.name} has passed"
        return f"<span class='days_to_finish'>{days_to_finish}</span> day{'' if days_to_finish == 1 else 's'} until the end of {obj.name}"
    
    def get_days_to_feast(self, obj):
        current_date = timezone.localdate(timezone=self.context.get('tz'))
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - current_date).days
            if days_to_feast < 0:
                return None
            return days_to_feast
        return None

    def get_start_date(self, obj):
        if obj.days.count() == 0:
            return None
        return obj.days.order_by('date').first().date
    
    def get_end_date(self, obj):
        if obj.days.count() == 0:
            return None
        return obj.days.order_by('date').last().date
    
    def get_has_passed(self, obj):
        current_date = timezone.localdate(timezone=self.context.get('tz'))
        if obj.days.count() == 0:
            return False
        return self.get_end_date(obj) < current_date
    
    def get_next_fast_date(self, obj):
        current_date = timezone.localdate(timezone=self.context.get('tz'))
        if obj.days.count() == 0:
            return None
        next_day = obj.days.filter(date__gte=current_date).order_by('date').first()
        if next_day is not None:
            return next_day.date
        return None
    
    def get_total_number_of_days(self, obj):
        return obj.days.count()
    
    def get_current_day_number(self, obj):
        try:
            current_date = timezone.localdate(timezone=self.context.get('tz'))
            
            # Get the days for the fast that are less than or equal to the current date
            days = obj.days.filter(date__lte=current_date).order_by('date')
            
            if days.exists():
                # Count the number of days from the first day until the current date
                return days.count()
            else:
                return None
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def get_thumbnail(self, obj):
        """Get the thumbnail URL, preferring cached version to avoid S3 calls"""
        if obj.image:
            # Try to get/update cached URL
            cached_url = self.update_thumbnail_cache(obj, 'image', 'image_thumbnail')
            if cached_url:
                return cached_url
            
            # Fall back to direct thumbnail URL if caching fails
            try:
                return obj.image_thumbnail.url
            except Exception as e:
                logging.error(f"Error getting thumbnail URL for Fast {obj.id}: {e}")
                return None
        return None

    class Meta:
        model = models.Fast
        fields = ['id', 'name', 'church', 'description', 'culmination_feast', 
                 'culmination_feast_date', 'year', 'image', 'thumbnail', 'url',
                 'participant_count', 'countdown', 'days_to_feast', 'start_date',
                 'end_date', 'joined', 'has_passed', 'next_fast_date',
                 'total_number_of_days', 'current_day_number', 'modal_id']
        
    def to_representation(self, instance):
        """Optimize the serialization by using select_related and prefetch_related"""
        # Ensure church is pre-fetched
        if not hasattr(instance, '_prefetched_objects_cache'):
            instance = models.Fast.objects.select_related('church').prefetch_related(
                'days', 'profiles'
            ).get(id=instance.id)
        
        return super().to_representation(instance)

class JoinFastSerializer(serializers.ModelSerializer):
    fast_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = models.Profile
        fields = ['fast_id']

class FastStatsSerializer(serializers.Serializer):
    joined_fasts = serializers.SerializerMethodField()
    total_fasts = serializers.SerializerMethodField()
    total_fast_days = serializers.SerializerMethodField()

    def get_joined_fasts(self, obj):
        return obj.fasts.values_list('id', flat=True)

    def get_total_fasts(self, obj):
        # returns the total number of fasts the user has joined
        return obj.fasts.count()
    
    def get_total_fast_days(self, obj):
        # returns the total number of days the user has fasted
        return sum(fast.days.count() for fast in obj.fasts.all())
    
    class Meta:
        fields = ['joined_fasts', 'total_fasts', 'total_fast_days']


class DaySerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Day
        fields = ['id', 'date', 'fast', 'church']

class ParticipantSerializer(serializers.ModelSerializer):
    thumbnail = serializers.SerializerMethodField()
    abbreviation = serializers.SerializerMethodField()
    user = serializers.SerializerMethodField()

    def get_user(self, obj):
        # user is returning name while frontend is transitioning to api change.
        # this will be removed after frontend update is fully deployed.
        if obj.name:
            return obj.name
        else:
            return self.get_abbreviation(obj)

    def get_abbreviation(self, obj):
        if obj.name:
            return obj.name[0]
        else:
            return obj.user.email[0]

    def get_thumbnail(self, obj):
        if obj.profile_image_thumbnail:
            return obj.profile_image_thumbnail.url
        return None

    class Meta:
        model = models.Profile
        fields = ['id', 'name', 'profile_image', 'thumbnail', 'location', 'abbreviation', 'user']

class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        try:
            user = User.objects.get(email=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("User with this email address does not exist.")
        return value

class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    uidb64 = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError("Passwords do not match.")
        
        # Decode the uidb64 to get the user ID
        try:
            uid = force_str(urlsafe_base64_decode(data['uidb64']))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError("Invalid uidb64 or user does not exist.")
    
        # Check if the token is valid
        if not default_token_generator.check_token(user, data['token']):
            raise serializers.ValidationError("Invalid token.")
        
        return data
    

class DevotionalSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source='video.title')
    description = serializers.CharField(source='video.description')
    thumbnail = serializers.SerializerMethodField()
    thumbnail_small = serializers.SerializerMethodField()
    video = serializers.SerializerMethodField()
    date = serializers.DateField(source='day.date')
    fast_id = serializers.IntegerField(source='day.fast.id')
    created_at = serializers.DateTimeField(source='video.created_at')
    updated_at = serializers.DateTimeField(source='video.updated_at')

    class Meta:
        model = models.Devotional
        fields = [
            'id',
            'title',
            'description',
            'thumbnail',
            'thumbnail_small',
            'video',
            'date',
            'fast_id',
            'created_at',
            'updated_at',
        ]

    def get_thumbnail(self, obj):
        return obj.video.thumbnail.url if obj.video and obj.video.thumbnail else None

    def get_thumbnail_small(self, obj):
        if obj.video and obj.video.thumbnail:
            # Try to get cached URL first
            if obj.video.cached_thumbnail_url:
                return obj.video.cached_thumbnail_url
            # Fall back to generating URL
            try:
                return obj.video.thumbnail_small.url
            except:
                return None
        return None

    def get_video(self, obj):
        return obj.video.video.url if obj.video and obj.video.video else None
