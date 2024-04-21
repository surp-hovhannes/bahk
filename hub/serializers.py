"""Serializers for handling API requests."""
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

    def get_participant_count(self, obj):
        return obj.profiles.count()

    class Meta:
        model = models.Fast
        fields = ["name", "church", "participant_count"]
