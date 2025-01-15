from django.urls import include, path
from rest_framework import routers


from .views.profile import ProfileDetailView, ProfileImageUploadView
from .views.fast import FastListView, FastDetailView, JoinFastView, FastByDateView, FastOnDate, FastOnDateWithoutUser, FastParticipantsView, LeaveFastView, FastStatsView
from .views.day import FastDaysListView, UserDaysView
from .views.user import UserViewSet, GroupViewSet, RegisterView, PasswordResetView, PasswordResetConfirmView, DeleteAccountView
from .views.church import ChurchListView, ChurchDetailView
from .views.readings import GetDailyReadingsForDate
from .views.web import home, test_email_view, add_fast_to_profile, remove_fast_from_profile, register, join_fasts, edit_profile, changelog

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
    path('fasts/<int:fast_id>/participants/', FastParticipantsView.as_view(), name='fast-participants'),
    path('fasts/stats/', FastStatsView.as_view(), name='fast-stats'),


    # TODO: Remove these legacy endpoints after frontend is updated
    path('user/fasts/', FastOnDate.as_view(), name="fast_on_date"),
    path("fast/", FastOnDateWithoutUser.as_view(), name="fast_on_date"),
   
    # Day endpoints
    path('fasts/<int:fast_id>/days/', FastDaysListView.as_view(), name='fast-days-list'),
    path('user/days/', UserDaysView.as_view(), name='user-days'),

    # Church endpoints
    path('churches/', ChurchListView.as_view(), name='church-list'),
    path('churches/<int:pk>/', ChurchDetailView.as_view(), name='church-detail'),

    # Web endpoints
    path("web/", home, name="web_home"),
    path('add_fast_to_profile/<int:fast_id>/', add_fast_to_profile, name='add_fast_to_profile'),
    path('remove_fast_from_profile/<int:fast_id>/', remove_fast_from_profile, name='remove_fast_from_profile'),
    path("create_user/web/", register, name="register"),
    path("join_fasts/web/", join_fasts, name="join_fasts"),
    path("edit_profile/web/", edit_profile, name="edit_profile"),
    path('changelog/web/', changelog, name='changelog'),

    # Readings endpoints
    path("readings/", GetDailyReadingsForDate.as_view(), name="daily-readings"),

    # Misc endpoints
    path('test_email/', test_email_view, name='test_email'),

    # Push Notifications endpoints
    path('notifications/', include('notifications.urls')),
]

urlpatterns += router.urls