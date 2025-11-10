"""URL patterns for the icons app."""
from django.urls import path
from icons import views

app_name = 'icons'

urlpatterns = [
    # Icon list and detail endpoints
    path('', views.IconListView.as_view(), name='icon-list'),
    path('<int:pk>/', views.IconDetailView.as_view(), name='icon-detail'),
    
    # AI-powered icon matching endpoint
    path('match/', views.IconMatchView.as_view(), name='icon-match'),
]
