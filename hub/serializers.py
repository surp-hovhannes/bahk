"""Serializers for handling API requests."""
import datetime

from django.contrib.auth.models import Group, User
from rest_framework import serializers

from hub import models


class UserSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = User
        fields = ["url", "username", "email", "groups"]


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

    def get_participant_count(self, obj):
        return obj.profiles.count()
    
    def get_countdown(self, obj):
        if obj.culmination_feast and obj.culmination_feast_date:
            days_to_feast = (obj.culmination_feast_date - datetime.date.today()).days
            if days_to_feast < 0:
                return "{obj.culmination_feast} has passed"
            return f"{days_to_feast} days until {obj.culmination_feast}"
        
        finish_date = max([day.date for day in obj.days.all()])
        days_to_finish = (finish_date - datetime.date.today()).days + 1  # + 1 to get days until first day *after* fast
        if days_to_finish < 0:
            return f"{obj.name} has passed"
        return f"{days_to_finish} days until the end of {obj.name}"

    class Meta:
        model = models.Fast
        fields = ["name", "church", "participant_count", "description", "countdown"]
