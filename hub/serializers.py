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
    modal_id = serializers.ReadOnlyField()

    def get_church(self, obj):
        return ChurchSerializer(obj.church, context=self.context).data if obj.church else None

        

    def get_joined(self, obj):    
        # test if request is present otherwise return False
        if 'request' not in self.context:
            return False
        else:
            request = self.context.get('request')
            if request and hasattr(request, 'user'):
                return request.user.profile.fasts.filter(id=obj.id).exists()
            return False

    def get_participant_count(self, obj):
        return obj.profiles.count()
    
    def get_countdown(self, obj):
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - datetime.date.today()).days
            if days_to_feast < 0:
                return f"{obj.culmination_feast} has passed"
            return f"<span class='days_to_finish'>{days_to_feast}</span> day{'' if days_to_feast == 1 else 's'} until {obj.culmination_feast}"
        
        finish_date = max([day.date for day in obj.days.all()])
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
        return obj.days.order_by('date').first().date
    
    def get_end_date(self, obj):
        return obj.days.order_by('date').last().date
    
    def get_has_passed(self, obj):
        return self.get_end_date(obj) < datetime.date.today()
    
    def get_next_fast_date(self, obj):
        next_day = obj.days.filter(date__gte=datetime.date.today()).order_by('date').first()
        if next_day is not None:
            return next_day.date
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