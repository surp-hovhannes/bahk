"""URL patterns for the icons app."""
from django.urls import path
from icons import views

app_name = 'icons'

urlpatterns = [
    # Icon list and detail endpoints
    path('', views.IconListView.as_view(), name='icon-list'),
    path('<int:pk>/', views.IconDetailView.as_view(), name='icon-detail'),
    
    # Icon feedback endpoint (after <int:pk>/ to avoid routing conflicts)
    path('<int:pk>/feedback/', views.IconFeedbackCreateView.as_view(), name='icon-feedback'),
    
    # AI-powered icon matching endpoint
    path('match/', views.IconMatchView.as_view(), name='icon-match'),
]
