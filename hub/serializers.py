"""Serializers for handling API requests."""
import datetime

from django.contrib.auth.models import Group, User
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_str
from django.core.mail import send_mail
from django.conf import settings

from hub import models

class ProfileSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    thumbnail = serializers.SerializerMethodField()

    def get_thumbnail(self, obj):
        if obj.profile_image_thumbnail:
            try:
                return obj.profile_image_thumbnail.url
            except:
                return None
        return None

    class Meta:
        model = models.Profile
        fields = ['user_id', 'username', 'email', 'profile_image', 'thumbnail', 
                 'location', 'church', 'receive_upcoming_fast_reminders']

class ProfileImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Profile
        fields = ['profile_image']

class UserSerializer(serializers.HyperlinkedModelSerializer):
    profile = ProfileSerializer(read_only=True)  # Include profile information

    class Meta:
        model = User
        fields = ["url", "username", "email", "groups", "profile"]

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
    church = serializers.PrimaryKeyRelatedField(source='profile.church', queryset=models.Church.objects.all(), required=True)

    class Meta:
        model = User
        fields = ('username', 'password', 'password2', 'email', 'church')

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        return attrs

    def create(self, validated_data):
        church = validated_data.pop('profile')['church']
        user = User.objects.create(
            username=validated_data['username'],
            email=validated_data['email'],
        )
        user.set_password(validated_data['password'])
        user.save()
        models.Profile.objects.create(user=user, church=church)

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
        fields = ["id","name"]


class FastSerializer(serializers.ModelSerializer):
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
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - datetime.date.today()).days
            if days_to_feast < 0:
                return f"{obj.culmination_feast} has passed"
            return f"<span class='days_to_finish'>{days_to_feast}</span> day{'' if days_to_feast == 1 else 's'} until {obj.culmination_feast}"
        
        days = [day.date for day in obj.days.all()]
        if not days:
            return f"No days available for {obj.name}"
        
        finish_date = max(days)
        days_to_finish = (finish_date - datetime.date.today()).days + 1  # + 1 to get days until first day *after* fast
        if days_to_finish < 0:
            return f"{obj.name} has passed"
        return f"<span class='days_to_finish'>{days_to_finish}</span> day{'' if days_to_finish == 1 else 's'} until the end of {obj.name}"
    
    def get_days_to_feast(self, obj):
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - datetime.date.today()).days
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
        if obj.days.count() == 0:
            return False
        return self.get_end_date(obj) < datetime.date.today()
    
    def get_next_fast_date(self, obj):
        if obj.days.count() == 0:
            return None
        next_day = obj.days.filter(date__gte=datetime.date.today()).order_by('date').first()
        if next_day is not None:
            return next_day.date
        return None
    
    def get_total_number_of_days(self, obj):
        return obj.days.count()
    
    def get_current_day_number(self, obj):
        try:
            if obj.days.count() == 0:
                return None
            
            start_date = self.get_start_date(obj)
            if start_date is None:
                return None
            
            current_date = datetime.date.today()
            end_date = self.get_end_date(obj)
            
            if current_date > end_date:
                return None 
            
            return (current_date - start_date).days + 1
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def get_thumbnail(self, obj):
        if obj.image:
            return obj.image.url
        return None

    class Meta:
        model = models.Fast
        fields = '__all__'

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

    def get_thumbnail(self, obj):
        if obj.profile_image_thumbnail:
            return obj.profile_image_thumbnail.url
        return None

    class Meta:
        model = models.Profile
        fields = ['id', 'user', 'profile_image', 'thumbnail', 'location'] 

    user = serializers.CharField(source='user.username')  # If you want to include the username instead of the user object

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