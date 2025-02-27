"""URLs for learning resources."""

from django.urls import path
from .views import RecipeListView, VideoListView, ArticleListView

urlpatterns = [
    path('videos/', VideoListView.as_view(), name='video-list'),
    path('articles/', ArticleListView.as_view(), name='article-list'),
    path('recipes/', RecipeListView.as_view(), name='recipe-list'),
]
