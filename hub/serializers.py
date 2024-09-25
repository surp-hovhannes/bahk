"""Serializers for handling API requests."""
import datetime

from django.contrib.auth.models import Group, User
from rest_framework import serializers

from hub import models

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Profile
        fields = ['profile_image', 'location', 'church', 'receive_upcoming_fast_reminders']
    
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


class ChurchSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Church
        fields = ["name"]


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
    curent_day_number = serializers.SerializerMethodField()
    modal_id = serializers.ReadOnlyField()


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
    
    def get_curent_day_number(self, obj):
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

    class Meta:
        model = models.Fast
        fields = '__all__'

class JoinFastSerializer(serializers.ModelSerializer):
    fast_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = models.Profile
        fields = ['fast_id']

class DaySerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Day
        fields = ['id', 'date', 'fast', 'church']

class ParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Profile
        fields = ['id', 'user', 'profile_image', 'location'] 

    user = serializers.CharField(source='user.username')  # If you want to include the username instead of the user object