from django.urls import include, path
from rest_framework import routers


from .views.profile import ProfileDetailView, ProfileImageUploadView
from .views.fast import (
    FastListView, 
    FastDetailView, 
    JoinFastView, 
    FastByDateView,
    FastByFeastDateView,
    FastOnDate, 
    FastOnDateWithoutUser, 
    FastParticipantsView,
    PaginatedFastParticipantsView,
    LeaveFastView, 
    FastStatsView,
    FastParticipantsMapView,
)
from .views.day import FastDaysListView, UserDaysView
from .views.devotionals import DevotionalByDateView, DevotionalsByFastView, DevotionalDetailView, DevotionalListView
from .views.user import (
    UserViewSet, 
    GroupViewSet, 
    RegisterView, 
    PasswordResetView,
    PasswordResetConfirmView,
    DeleteAccountView,
)
from .views.church import ChurchListView, ChurchDetailView
from .views.readings import GetDailyReadingsForDate, ReadingContextFeedbackView
from .views.feasts import GetFeastForDate, FeastContextFeedbackView
from .views.patristic_quotes import (
    PatristicQuoteListView,
    PatristicQuoteDetailView,
    PatristicQuotesByChurchView,
    PatristicQuotesByFastView,
    PatristicQuoteOfTheDayView,
)

from hub.views.admin import compare_reading_prompts


router = routers.DefaultRouter()
router.register(r"users", UserViewSet)
router.register(r"groups", GroupViewSet)

# Wire up our API using automatic URL routing.
# Additionally, we include login URLs for the browsable API.
urlpatterns = [
    path("", include(router.urls)),

    # User & Profile endpoints
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('profile/', ProfileDetailView.as_view(), name='profile-detail'),
    path('profile/image-upload/', ProfileImageUploadView.as_view(), name='profile-image-upload'),
    path('password/reset/', PasswordResetView.as_view(), name='password_reset'),
    path('password/reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('account/delete/', DeleteAccountView.as_view(), name='account-delete'),

    # Fast endpoints
    path('fasts/', FastListView.as_view(), name='fast-list'),
    path('fasts/<int:pk>/', FastDetailView.as_view(), name='fast-detail'),
    path('fasts/join/', JoinFastView.as_view(), name='fast-join'),
    path('fasts/leave/', LeaveFastView.as_view(), name='leave-fast'),
    path('fasts/by-date/', FastByDateView.as_view(), name='fast-by-date'),
    path('fasts/by-feast-date/', FastByFeastDateView.as_view(), name='fast-by-feast-date'),
    path('fasts/<int:fast_id>/participants/', FastParticipantsView.as_view(), name='fast-participants'),
    path('fasts/<int:fast_id>/participants/paginated/', PaginatedFastParticipantsView.as_view(), name='fast-participants-paginated'),
    path('fasts/stats/', FastStatsView.as_view(), name='fast-stats'),
    path('fasts/<int:fast_id>/participants/map/', FastParticipantsMapView.as_view(), name='fast-participants-map'),


    # TODO: Remove these legacy endpoints after frontend is updated
    path('user/fasts/', FastOnDate.as_view(), name="fast_on_date"),
    path("fast/", FastOnDateWithoutUser.as_view(), name="fast_on_date"),
   
    # Day endpoints
    path('fasts/<int:fast_id>/days/', FastDaysListView.as_view(), name='fast-days-list'),
    path('user/days/', UserDaysView.as_view(), name='user-days'),

    # Church endpoints
    path('churches/', ChurchListView.as_view(), name='church-list'),
    path('churches/<int:pk>/', ChurchDetailView.as_view(), name='church-detail'),

    # Devotional endpoints
    path('devotionals/', DevotionalListView.as_view(), name='devotional-list'),
    path('devotionals/<int:pk>/', DevotionalDetailView.as_view(), name='devotional-detail'),
    path('devotionals/by-fast/<int:fast_id>/', DevotionalsByFastView.as_view(), name='devotional-by-fast'),
    path('devotionals/by-date/', DevotionalByDateView.as_view(), name='devotional-on-date'),

    # Patristic Quotes endpoints
    path('patristic-quotes/', PatristicQuoteListView.as_view(), name='patristic-quote-list'),
    path('patristic-quotes/<int:pk>/', PatristicQuoteDetailView.as_view(), name='patristic-quote-detail'),
    path('patristic-quotes/by-church/<int:church_id>/', PatristicQuotesByChurchView.as_view(), name='patristic-quotes-by-church'),
    path('patristic-quotes/by-fast/<int:fast_id>/', PatristicQuotesByFastView.as_view(), name='patristic-quotes-by-fast'),
    path('patristic-quotes/quote-of-the-day/', PatristicQuoteOfTheDayView.as_view(), name='patristic-quote-of-the-day'),



    # Readings endpoints
    path("readings/", GetDailyReadingsForDate.as_view(), name="daily-readings"),
    path("readings/<int:pk>/feedback/", ReadingContextFeedbackView.as_view(), name="reading-context-feedback"),

    # Feasts endpoints
    path("feasts/", GetFeastForDate.as_view(), name="feast-for-date"),
    path("feasts/<int:pk>/feedback/", FeastContextFeedbackView.as_view(), name="feast-context-feedback"),



    # Push Notifications endpoints
    path('notifications/', include('notifications.urls')),

    # Admin endpoints
    path(
        "hub/reading/<int:reading_id>/compare-prompts/",
        compare_reading_prompts,
        name="hub_reading_compare_prompts",
    ),
]

urlpatterns += router.urls