from django.urls import include, path
from rest_framework import routers

from hub import views


router = routers.DefaultRouter()
router.register(r"users", views.UserViewSet)
router.register(r"groups", views.GroupViewSet)

#Apply Simple JSON Web Token (SimpleJWT) Authentication Routes to the API
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)


# Wire up our API using automatic URL routing.
# Additionally, we include login URLs for the browsable API.
urlpatterns = [
    path("", include(router.urls)),
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path("fast/", views.FastOnDate.as_view(), name="fast_on_date"),
    path("web/", views.home, name="web_home"),
    path('profile_image/<int:pk>/<int:width>x<int:height>/', views.resized_profile_image_view, name='resized_profile_image'),
]

urlpatterns += router.urls