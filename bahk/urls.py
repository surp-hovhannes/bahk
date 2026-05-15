""" URL configuration for bahk project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='web_home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='web_home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
#Apply Simple JSON Web Token (SimpleJWT) Authentication Routes to the API
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status


class HealthCheckView(APIView):
    """Health check endpoint for Render and other monitoring services.
    
    Verifies critical dependencies (database, cache) and returns 200 only
    when the app is actually functional, not just when the process is running.
    """
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        health = {
            "status": "healthy",
            "service": "fast-and-pray-api",
        }
        status_code = status.HTTP_200_OK

        # Verify database connectivity
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health["database"] = "connected"
        except Exception:
            health["database"] = "unreachable"
            health["status"] = "unhealthy"
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        # Verify cache connectivity (Redis)
        try:
            from django.core.cache import cache
            cache.set("health_check", "ok", 1)
            result = cache.get("health_check")
            if result == "ok":
                health["cache"] = "connected"
            else:
                health["cache"] = "unexpected response"
                health["status"] = "unhealthy"
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        except Exception:
            health["cache"] = "unreachable"
            health["status"] = "unhealthy"
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return Response(health, status=status_code)


class TrackingTokenObtainPairView(TokenObtainPairView):
    """Subclass to emit a USER_LOGGED_IN event for JWT credentials."""
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Emit login event using the validated user
        user = getattr(serializer, 'user', None)
        if user is not None:
            try:
                from events.models import Event, EventType
                Event.create_event(
                    event_type_code=EventType.USER_LOGGED_IN,
                    user=user,
                    title='User logged in (JWT)',
                    data={'method': 'jwt'},
                    request=request,
                )
            except Exception:
                pass

        return Response(serializer.validated_data, status=200)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', HealthCheckView.as_view(), name='health_check'),
    path('hub/', include('hub.urls')),
    path('', RedirectView.as_view(url='hub/', permanent=True)),

    # Authentication endpoints
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
    path('api/token/', TrackingTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    path('markdownx/', include('markdownx.urls')),  # Include markdownx URLs
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# add account authentication urls
urlpatterns += [
    path("accounts/", include("django.contrib.auth.urls")),
]

# pin hub endpoints to /api/ root url
urlpatterns += [
    path("api/", include("hub.urls")),
    path("api/learning-resources/", include("learning_resources.urls")),
    path("api/events/", include("events.urls")),
    path("api/", include("prayers.urls")),
    path("api/icons/", include("icons.urls")),
]

# Learning resources endpoints are handled by the include above
# Individual endpoints are defined in learning_resources/urls.py

# S3FileField URLs
urlpatterns += [
    path('api/s3-upload/', include('s3_file_field.urls')),
]

# for serving media files during development
if not settings.IS_PRODUCTION:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)