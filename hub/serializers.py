"""Serializers for handling API requests."""
import logging
from django.utils import timezone  # Add missing timezone import

from better_profanity import profanity
from django.contrib.auth.models import Group, User
from django.db.models.signals import post_save
from django.dispatch import receiver
from rest_framework import serializers
from django.utils.translation import activate, get_language
from django.contrib.auth.password_validation import validate_password
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_decode
from django.utils.encoding import force_str
from django.conf import settings
from django.utils.functional import cached_property

from hub import models
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
                 'location', 'timezone', 'church', 'receive_upcoming_fast_reminders', 
                 'receive_promotional_emails',
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
        
        # Split email into username and domain parts
        try:
            username_part, domain_part = email.split('@', 1)
        except ValueError:
            # Should not happen with EmailField validation, but added as safeguard
            raise serializers.ValidationError({'email': 'Invalid email format.'})

        # Split the domain part by '.' to get all its components
        domain_sub_parts = domain_part.split('.')

        # Combine username and all domain parts for profanity check
        all_email_parts = [username_part] + domain_sub_parts

        # Check each part for profanity, skipping empty strings
        if any(profanity.contains_profanity(part) for part in all_email_parts if part):
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cache the current date to avoid recalculating it in multiple methods
        self.current_date = timezone.localdate(timezone=self.context.get('tz'))

    def get_church(self, obj):
        return ChurchSerializer(obj.church, context=self.context).data if obj.church else None

    def get_joined(self, obj):
        """Use cached fast IDs to check if user has joined"""
        return obj.id in self._user_fast_ids

    def get_participant_count(self, obj):
        """Use annotated count if available"""
        return getattr(obj, 'participant_count', obj.profiles.count())
    
    def get_countdown(self, obj):
        """Use cached current_date and optimize database queries"""
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - self.current_date).days
            if days_to_feast < 0:
                return f"{obj.culmination_feast} has passed"
            return f"<span class='days_to_finish'>{days_to_feast}</span> day{'' if days_to_feast == 1 else 's'} until {obj.culmination_feast}"
        
        # Optimized version: Use database aggregation instead of loading all days into memory
        # This replaces the inefficient [day.date for day in obj.days.all()]
        from django.db.models import Max
        
        # Use database aggregation to get the latest date without loading all objects
        latest_day = obj.days.aggregate(max_date=Max('date'))['max_date']
        
        if not latest_day:
            return f"No days available for {obj.name}"
        
        days_to_finish = (latest_day - self.current_date).days + 1  # + 1 to get days until first day *after* fast
        if days_to_finish < 0:
            return f"{obj.name} has passed"
        return f"<span class='days_to_finish'>{days_to_finish}</span> day{'' if days_to_finish == 1 else 's'} until the end of {obj.name}"
    
    def get_days_to_feast(self, obj):
        """Use cached current_date"""
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - self.current_date).days
            if days_to_feast < 0:
                return None
            return days_to_feast
        return None

    def get_start_date(self, obj):
        """Use annotated value if available"""
        return getattr(obj, 'start_date', 
                     obj.days.order_by('date').first().date if obj.days.exists() else None)
    
    def get_end_date(self, obj):
        """Use annotated value if available"""
        return getattr(obj, 'end_date',
                     obj.days.order_by('date').last().date if obj.days.exists() else None)
    
    def get_has_passed(self, obj):
        """Use cached current_date and optimize end_date check"""
        end_date = self.get_end_date(obj)
        if end_date is None:
            return False
        return end_date < self.current_date
    
    def get_next_fast_date(self, obj):
        """Use cached current_date"""
        if obj.days.count() == 0:
            return None
        next_day = obj.days.filter(date__gte=self.current_date).order_by('date').first()
        if next_day is not None:
            return next_day.date
        return None
    
    def get_total_number_of_days(self, obj):
        """Use annotated value if available"""
        return getattr(obj, 'total_days', obj.days.count())
    
    def get_current_day_number(self, obj):
        """Use annotated value if available"""
        try:
            # Use the annotation if available
            if hasattr(obj, 'current_day_count'):
                return obj.current_day_count
            
            # Otherwise compute it
            days = obj.days.filter(date__lte=self.current_date).order_by('date')
            if days.exists():
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
                 'culmination_feast_date', 'culmination_feast_salutation',
                 'culmination_feast_message', 'culmination_feast_message_attribution',
                 'year', 'image', 'thumbnail', 'url',
                 'participant_count', 'countdown', 'days_to_feast', 'start_date',
                 'end_date', 'joined', 'has_passed', 'next_fast_date',
                 'total_number_of_days', 'current_day_number', 'modal_id']
        
    def to_representation(self, instance):
        # Activate language from context
        lang = self.context.get('lang') or (self.context.get('request').query_params.get('lang') if self.context.get('request') else None) or 'en'
        activate(lang)
        data = super().to_representation(instance)
        # Replace user-facing fields with translated values
        data['name'] = getattr(instance, 'name_i18n', instance.name)
        data['description'] = getattr(instance, 'description_i18n', instance.description)
        data['culmination_feast'] = getattr(instance, 'culmination_feast_i18n', instance.culmination_feast)
        data['culmination_feast_salutation'] = getattr(instance, 'culmination_feast_salutation_i18n', instance.culmination_feast_salutation)
        data['culmination_feast_message'] = getattr(instance, 'culmination_feast_message_i18n', instance.culmination_feast_message)
        data['culmination_feast_message_attribution'] = getattr(instance, 'culmination_feast_message_attribution_i18n', instance.culmination_feast_message_attribution)
        return data

    @cached_property
    def _user_fast_ids(self):
        """Cache the list of fast IDs the user has joined"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return set()
        return set(request.user.profile.fasts.values_list('id', flat=True))

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
        # Optimized version: Use database aggregation instead of N+1 queries
        # This replaces the inefficient sum(fast.days.count() for fast in obj.fasts.all())
        from django.db.models import Count
        
        # Single query with aggregation - much more efficient
        result = obj.fasts.aggregate(
            total_days=Count('days', distinct=True)
        )
        return result['total_days'] or 0
    
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
    title = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
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

    def get_title(self, obj):
        lang = self.context.get('lang') or (self.context.get('request').query_params.get('lang') if self.context.get('request') else None) or 'en'
        activate(lang)
        # Prefer video title translation; fallback
        return getattr(obj.video, 'title_i18n', obj.video.title) if obj.video else None

    def get_description(self, obj):
        lang = self.context.get('lang') or (self.context.get('request').query_params.get('lang') if self.context.get('request') else None) or 'en'
        activate(lang)
        # Prefer devotional description translation (model field), fallback to video description translation
        if getattr(obj, 'description', None):
            return getattr(obj, 'description_i18n', obj.description)
        if obj.video:
            return getattr(obj.video, 'description_i18n', obj.video.description)
        return None

class FastParticipantMapSerializer(serializers.ModelSerializer):
    """Serializer for the FastParticipantMap model."""
    
    class Meta:
        model = models.FastParticipantMap
        fields = ('id', 'map_url', 'last_updated', 'participant_count', 'format')
        
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Calculate the age of the map in hours
        if instance.last_updated:
            now = timezone.now()
            age_hours = (now - instance.last_updated).total_seconds() / 3600
            representation['age_hours'] = round(age_hours, 1)
        return representation
