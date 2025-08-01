"""URLs for learning resources."""

from django.urls import path
from .views import (
    RecipeListView, VideoListView, ArticleListView, 
    DevotionalSetListView, DevotionalSetDetailView,
    BookmarkListView, BookmarkCreateView, 
    bookmark_delete_view, bookmark_check_view
)

urlpatterns = [
    # Content listing endpoints
    path('videos/', VideoListView.as_view(), name='video-list'),
    path('articles/', ArticleListView.as_view(), name='article-list'),
    path('recipes/', RecipeListView.as_view(), name='recipe-list'),
    path('devotional-sets/', DevotionalSetListView.as_view(), name='devotional-set-list'),
    path('devotional-sets/<int:pk>/', DevotionalSetDetailView.as_view(), name='devotional-set-detail'),
    
    # Bookmark endpoints
    path('bookmarks/', BookmarkListView.as_view(), name='bookmark-list'),
    path('bookmarks/create/', BookmarkCreateView.as_view(), name='bookmark-create'),
    path('bookmarks/check/<str:content_type>/<int:object_id>/', bookmark_check_view, name='bookmark-check'),
    path('bookmarks/<str:content_type>/<int:object_id>/', bookmark_delete_view, name='bookmark-delete'),
]
