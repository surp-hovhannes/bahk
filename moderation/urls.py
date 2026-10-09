from django.contrib.auth.views import LogoutView
from django.urls import path
from moderation import views
from moderation.forms import ModerationLoginView

urlpatterns = [
    path(
        "login/",
        ModerationLoginView.as_view(),
        name="moderation-login",
    ),
    path("logout/", LogoutView.as_view(next_page="/moderation/login/"), name="moderation-logout"),
    path("", views.dashboard, name="moderation-dashboard"),
    path("<str:kind>/<int:pk>/", views.detail, name="moderation-detail"),
]
