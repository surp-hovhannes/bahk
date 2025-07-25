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
import hub.views as views
#Apply Simple JSON Web Token (SimpleJWT) Authentication Routes to the API
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('hub/', include('hub.urls')),
    path('', RedirectView.as_view(url='hub/', permanent=True)),

    # Authentication endpoints
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
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