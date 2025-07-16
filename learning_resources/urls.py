"""URLs for learning resources."""

from django.urls import path
from .views import RecipeListView, VideoListView, ArticleListView, DevotionalSetListView, DevotionalSetDetailView

urlpatterns = [
    path('videos/', VideoListView.as_view(), name='video-list'),
    path('articles/', ArticleListView.as_view(), name='article-list'),
    path('recipes/', RecipeListView.as_view(), name='recipe-list'),
    path('devotional-sets/', DevotionalSetListView.as_view(), name='devotional-set-list'),
    path('devotional-sets/<int:pk>/', DevotionalSetDetailView.as_view(), name='devotional-set-detail'),
]
