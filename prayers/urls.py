"""URL patterns for prayers app."""
from django.urls import path

from prayers import views

app_name = 'prayers'

urlpatterns = [
    # Prayer endpoints
    path('prayers/', views.PrayerListView.as_view(), name='prayer-list'),
    path('prayers/<int:pk>/', views.PrayerDetailView.as_view(), name='prayer-detail'),
    
    # Prayer set endpoints
    path('prayer-sets/', views.PrayerSetListView.as_view(), name='prayer-set-list'),
    path('prayer-sets/<int:pk>/', views.PrayerSetDetailView.as_view(), name='prayer-set-detail'),
]

