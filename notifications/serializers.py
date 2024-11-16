from rest_framework import serializers
from .models import DeviceToken

class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ['token', 'device_type', 'is_active', 'created_at']
        read_only_fields = ['is_active', 'created_at']

    def validate_device_type(self, value):
        """
        Check that device_type is either 'ios' or 'android'
        """
        if value not in ['ios', 'android']:
            raise serializers.ValidationError("device_type must be either 'ios' or 'android'")
        return value 