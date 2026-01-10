"""
URL patterns for the events app API endpoints.
"""

from django.urls import path
from . import views

app_name = 'events'

urlpatterns = [
    # Event listing and details
    path('events/', views.EventListView.as_view(), name='event-list'),
    path('events/<int:pk>/', views.EventDetailView.as_view(), name='event-detail'),
    path('events/my/', views.MyEventsView.as_view(), name='my-events'),
    
    # Event types
    path('event-types/', views.EventTypeListView.as_view(), name='event-type-list'),
    
    # Analytics and statistics
    path('stats/', views.EventStatsView.as_view(), name='event-stats'),
    path('stats/user/', views.UserEventStatsView.as_view(), name='user-event-stats'),
    path('stats/user/<int:user_id>/', views.UserEventStatsView.as_view(), name='user-event-stats-specific'),
    path('stats/fast/<int:fast_id>/', views.FastEventStatsView.as_view(), name='fast-event-stats'),
    
    # User Activity Feed
    path('activity-feed/', views.UserActivityFeedView.as_view(), name='activity-feed'),
    path('activity-feed/summary/', views.UserActivityFeedSummaryView.as_view(), name='activity-feed-summary'),
    path('activity-feed/milestones/', views.UserMilestonesView.as_view(), name='user-milestones'),
    path('activity-feed/mark-read/', views.MarkActivityReadView.as_view(), name='mark-activity-read'),
    path('activity-feed/generate/', views.GenerateActivityFeedView.as_view(), name='generate-activity-feed'),
    
    # Engagement tracking endpoints
    path('track/devotional-viewed/', views.TrackDevotionalViewedView.as_view(), name='track-devotional-viewed'),
    path('track/prayer-set-viewed/', views.TrackPrayerSetViewedView.as_view(), name='track-prayer-set-viewed'),
    path('track/checklist-used/', views.TrackChecklistUsedView.as_view(), name='track-checklist-used'),
    path('track/prayer-viewed/', views.TrackPrayerViewedView.as_view(), name='track-prayer-viewed'),
    path('track/prayer-request-viewed/', views.TrackPrayerRequestViewedView.as_view(), name='track-prayer-request-viewed'),
    
    # Admin utilities
    path('admin/trigger-milestone/<int:fast_id>/', views.trigger_milestone_check, name='trigger-milestone'),
]