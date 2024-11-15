from django.urls import path
from .views import DeviceTokenCreateView, TestPushNotificationView

urlpatterns = [
    path('register-device-token/', DeviceTokenCreateView.as_view(), name='register-device-token'),
    path('test-push/', TestPushNotificationView.as_view(), name='test-push-notification'),
] 