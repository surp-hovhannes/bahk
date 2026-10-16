"""URL patterns for prayers app."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from prayers import views
from prayers.staff_library import StaffLibraryView

app_name = 'prayers'

# Router for viewsets
router = DefaultRouter()
router.register(r'prayer-requests', views.PrayerRequestViewSet, basename='prayer-request')

urlpatterns = [
    path("staff/prayer-library/<int:church_id>/", StaffLibraryView.as_view(), name="staff-library"),
    # Prayer endpoints
    path('prayers/', views.PrayerListView.as_view(), name='prayer-list'),
    path('prayers/<int:pk>/', views.PrayerDetailView.as_view(), name='prayer-detail'),

    # Prayer set endpoints
    path('prayer-sets/', views.PrayerSetListView.as_view(), name='prayer-set-list'),
    path('prayer-sets/<int:pk>/', views.PrayerSetDetailView.as_view(), name='prayer-set-detail'),

    # Prayer request endpoints (viewset router)
    path('', include(router.urls)),
]

