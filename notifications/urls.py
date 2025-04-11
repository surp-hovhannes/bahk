from django.urls import path
from .views import DeviceTokenCreateView, TestPushNotificationView
from . import views

app_name = 'notifications'

urlpatterns = [
    path('register-device-token/', DeviceTokenCreateView.as_view(), name='register-device-token'),
    path('test-push/', TestPushNotificationView.as_view(), name='test-push-notification'),
    path('unsubscribe/', views.unsubscribe, name='unsubscribe'),
    path('unsubscribe/success/', views.unsubscribe_success, name='unsubscribe_success'),
    path('unsubscribe/error/', views.unsubscribe_error, name='unsubscribe_error'),
] 