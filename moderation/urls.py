from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from moderation import views
from moderation.forms import ModerationLoginForm

urlpatterns = [
    path(
        "login/",
        LoginView.as_view(
            template_name="moderation/login.html", authentication_form=ModerationLoginForm, next_page="/moderation/"
        ),
        name="moderation-login",
    ),
    path("logout/", LogoutView.as_view(next_page="/moderation/login/"), name="moderation-logout"),
    path("", views.dashboard, name="moderation-dashboard"),
    path("<str:kind>/<int:pk>/", views.detail, name="moderation-detail"),
]
