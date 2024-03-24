from django.urls import include, path
from rest_framework import routers

from hub import views


router = routers.DefaultRouter()
router.register(r"users", views.UserViewSet)
router.register(r"groups", views.GroupViewSet)

# Wire up our API using automatic URL routing.
# Additionally, we include login URLs for the browsable API.
urlpatterns = [
    path("", include(router.urls)),
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
    path("fast/today/", views.TodaysFast.as_view()),
    path("fast/<yyyymmdd>/", views.FastOnDate.as_view()),
    path("fast/today/participant_count/", views.TodaysParticipantCount.as_view()),
    path("fast/<yyyymmdd>/participant_count/", views.ParticipantCountOnDate.as_view()),
]

urlpatterns += router.urls
