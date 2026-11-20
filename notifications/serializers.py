from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from hub.models import Fast

from .models import DeviceToken, PostFastEncouragementEmail
import re

class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ['token', 'user', 'device_type', 'is_active', 'created_at']
        read_only_fields = ['user', 'is_active', 'created_at']

    def validate_device_type(self, value):
        """
        Check that device_type is either 'ios' or 'android'
        """
        acceptable_device_types = [type_tuple[0] for type_tuple in DeviceToken.DEVICE_TYPES]
        if value not in acceptable_device_types:
            raise serializers.ValidationError(f"device_type was {value}, must be in: {acceptable_device_types}")
        return value

    def validate_token(self, value):
        """
        Validate the token format
        """
        pattern = r'^ExponentPushToken\[[a-zA-Z0-9_-]+\]$'
        if not bool(re.match(pattern, value)):
            raise serializers.ValidationError(f'Invalid Expo push token format. Expected: {pattern}')
        return value


class PostFastEncouragementEmailSerializer(serializers.ModelSerializer):
    duplicate_fast_message = 'An encouragement email already exists for this fast.'

    fast = serializers.PrimaryKeyRelatedField(
        queryset=Fast.objects.all(),
        validators=[
            UniqueValidator(
                queryset=PostFastEncouragementEmail.objects.all(),
                message=duplicate_fast_message,
            ),
        ],
    )
    fast_id = serializers.IntegerField(read_only=True)
    fast_name = serializers.CharField(source='fast.name', read_only=True, allow_null=True)

    class Meta:
        model = PostFastEncouragementEmail
        fields = ('id', 'fast', 'fast_id', 'fast_name', 'subject', 'message')

    def validate_subject(self, value):
        if '\r' in value or '\n' in value:
            raise serializers.ValidationError('Subject must be a single line.')
        return value

    def validate(self, attrs):
        unknown = set(self.initial_data) - {'fast', 'subject', 'message'}
        if unknown:
            raise serializers.ValidationError(
                {name: 'Unknown field.' for name in sorted(unknown)}
            )
        if self.partial and not self.initial_data:
            raise serializers.ValidationError('PATCH body must contain at least one field.')
        return attrs

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save(update_fields=validated_data.keys())
        return instance
