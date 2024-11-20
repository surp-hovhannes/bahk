from rest_framework import serializers
from .models import DeviceToken

class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ['token', 'user', 'device_type', 'is_active', 'created_at']
        read_only_fields = ['is_active', 'created_at']

    def validate_device_type(self, value):
        """
        Check that device_type is either 'ios' or 'android'
        """
        acceptable_device_types = [type_tuple[0] for type_tuple in DeviceToken.DEVICE_TYPES]
        if value not in acceptable_device_types:
            raise serializers.ValidationError(f"device_type was {value}, must be in: {acceptable_device_types}")
        return value