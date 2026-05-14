"""URL patterns for the icons app."""
from django.urls import path
from icons import views

app_name = 'icons'

urlpatterns = [
    # Fixed literal paths first (before parameterized paths)
    path('', views.IconListView.as_view(), name='icon-list'),
    path('match/', views.IconMatchView.as_view(), name='icon-match'),
    # Icon detail and nested endpoints
    path('<int:pk>/', views.IconDetailView.as_view(), name='icon-detail'),
    path('<int:pk>/feedback/', views.IconFeedbackCreateView.as_view(), name='icon-feedback'),
]
