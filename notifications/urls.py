from django.urls import path
from .views import DeviceTokenCreateView

urlpatterns = [
    path('register-device-token/', DeviceTokenCreateView.as_view(), name='register-device-token'),
] 