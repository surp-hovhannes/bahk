"""
Redis caching service for bookmarks to provide ultra-fast bookmark lookups.

This module implements a comprehensive caching strategy:
1. User bookmark sets cached in Redis for instant lookups
2. Cache invalidation on bookmark changes
3. Graceful fallback to database if Redis unavailable
4. Optimized cache key management
"""

import logging
from typing import Set, Optional, List, Dict, Any
from django.core.cache import cache
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.models import User
from django.conf import settings

logger = logging.getLogger(__name__)


class BookmarkCacheService:
    """
    High-performance Redis caching service for user bookmarks.
    
    Cache Strategy:
    - User bookmark sets stored as Redis sets for O(1) membership testing
    - Cache keys: "bookmarks:user:{user_id}:{content_type_id}"
    - TTL: 1 hour (refreshed on access)
    - Invalidation: On bookmark create/delete operations
    """
    
    # Cache configuration
    CACHE_TTL = 3600  # 1 hour
    CACHE_PREFIX = "bookmarks"
    
    @classmethod
    def _get_cache_key(cls, user_id: int, content_type_id: int) -> str:
        """Generate cache key for user bookmarks of a specific content type."""
        return f"{cls.CACHE_PREFIX}:user:{user_id}:ct:{content_type_id}"
    
    @classmethod
    def _get_user_cache_pattern(cls, user_id: int) -> str:
        """Generate cache key pattern for all user bookmarks."""
        return f"{cls.CACHE_PREFIX}:user:{user_id}:ct:*"
    
    @classmethod
    def get_user_bookmarks(cls, user: User, content_type: ContentType) -> Optional[Set[int]]:
        """
        Get user's bookmarked object IDs for a content type from cache.
        
        Returns:
            Set of object IDs if cached, None if not in cache
        """
        if not user.is_authenticated:
            return set()
        
        cache_key = cls._get_cache_key(user.id, content_type.id)
        
        try:
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                # Refresh TTL on access (sliding expiration)
                cache.touch(cache_key, cls.CACHE_TTL)
                return set(cached_data) if cached_data else set()
            
            return None  # Not in cache
            
        except Exception as e:
            logger.warning(f"Redis cache get failed for key {cache_key}: {e}")
            return None
    
    @classmethod
    def set_user_bookmarks(cls, user: User, content_type: ContentType, object_ids: Set[int]) -> bool:
        """
        Cache user's bookmarked object IDs for a content type.
        
        Args:
            user: The user
            content_type: Content type being bookmarked
            object_ids: Set of bookmarked object IDs
            
        Returns:
            True if successfully cached, False otherwise
        """
        if not user.is_authenticated:
            return False
        
        cache_key = cls._get_cache_key(user.id, content_type.id)
        
        try:
            # Store as list for JSON serialization
            cache.set(cache_key, list(object_ids), cls.CACHE_TTL)
            logger.debug(f"Cached {len(object_ids)} bookmarks for user {user.id}, content_type {content_type.id}")
            return True
            
        except Exception as e:
            logger.warning(f"Redis cache set failed for key {cache_key}: {e}")
            return False
    
    @classmethod
    def add_bookmark_to_cache(cls, user: User, content_type: ContentType, object_id: int) -> bool:
        """
        Add a single bookmark to the cache (if cache exists).
        
        This is an optimization for when a bookmark is created - we can update
        the cache instead of invalidating it.
        """
        if not user.is_authenticated:
            return False
        
        cache_key = cls._get_cache_key(user.id, content_type.id)
        
        try:
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                # Update existing cache
                bookmark_set = set(cached_data)
                bookmark_set.add(object_id)
                cache.set(cache_key, list(bookmark_set), cls.CACHE_TTL)
                logger.debug(f"Added bookmark {object_id} to cache for user {user.id}")
                return True
            
            # Cache doesn't exist, don't create it here
            return False
            
        except Exception as e:
            logger.warning(f"Redis cache update failed for key {cache_key}: {e}")
            return False
    
    @classmethod
    def remove_bookmark_from_cache(cls, user: User, content_type: ContentType, object_id: int) -> bool:
        """
        Remove a single bookmark from the cache (if cache exists).
        
        This is an optimization for when a bookmark is deleted - we can update
        the cache instead of invalidating it.
        """
        if not user.is_authenticated:
            return False
        
        cache_key = cls._get_cache_key(user.id, content_type.id)
        
        try:
            cached_data = cache.get(cache_key)
            if cached_data is not None:
                # Update existing cache
                bookmark_set = set(cached_data)
                bookmark_set.discard(object_id)
                cache.set(cache_key, list(bookmark_set), cls.CACHE_TTL)
                logger.debug(f"Removed bookmark {object_id} from cache for user {user.id}")
                return True
            
            # Cache doesn't exist, nothing to remove
            return False
            
        except Exception as e:
            logger.warning(f"Redis cache update failed for key {cache_key}: {e}")
            return False
    
    @classmethod
    def invalidate_user_bookmarks(cls, user: User, content_type: ContentType = None) -> bool:
        """
        Invalidate cached bookmarks for a user.
        
        Args:
            user: The user whose cache to invalidate
            content_type: If provided, only invalidate this content type.
                         If None, invalidate all content types for the user.
        """
        if not user.is_authenticated:
            return False
        
        try:
            if content_type:
                # Invalidate specific content type
                cache_key = cls._get_cache_key(user.id, content_type.id)
                cache.delete(cache_key)
                logger.debug(f"Invalidated bookmark cache for user {user.id}, content_type {content_type.id}")
            else:
                # Invalidate all content types for user (less efficient but thorough)
                pattern = cls._get_user_cache_pattern(user.id)
                # Note: Django cache doesn't support pattern deletion out of the box
                # For now, we'll invalidate common content types
                from learning_resources.models import Video, Article, Recipe
                from hub.models import DevotionalSet, Fast, Devotional, Reading
                
                content_types = [
                    ContentType.objects.get_for_model(Video),
                    ContentType.objects.get_for_model(Article),
                    ContentType.objects.get_for_model(Recipe),
                    ContentType.objects.get_for_model(DevotionalSet),
                    ContentType.objects.get_for_model(Fast),
                    ContentType.objects.get_for_model(Devotional),
                    ContentType.objects.get_for_model(Reading),
                ]
                
                for ct in content_types:
                    cache_key = cls._get_cache_key(user.id, ct.id)
                    cache.delete(cache_key)
                
                logger.debug(f"Invalidated all bookmark caches for user {user.id}")
            
            return True
            
        except Exception as e:
            logger.warning(f"Redis cache invalidation failed for user {user.id}: {e}")
            return False
    
    @classmethod
    def preload_user_bookmarks(cls, user: User, content_type: ContentType) -> Set[int]:
        """
        Preload user bookmarks from database and cache them.
        
        This method is called when cache is empty to populate it from the database.
        """
        if not user.is_authenticated:
            return set()
        
        try:
            # Import here to avoid circular imports
            from learning_resources.models import Bookmark
            
            # Fetch from database
            bookmarks = Bookmark.objects.filter(
                user=user,
                content_type=content_type
            ).values_list('object_id', flat=True)
            
            bookmark_set = set(bookmarks)
            
            # Cache the results
            cls.set_user_bookmarks(user, content_type, bookmark_set)
            
            logger.debug(f"Preloaded {len(bookmark_set)} bookmarks for user {user.id}, content_type {content_type.id}")
            return bookmark_set
            
        except Exception as e:
            logger.error(f"Failed to preload bookmarks for user {user.id}: {e}")
            return set()
    
    @classmethod
    def get_cache_stats(cls) -> Dict[str, Any]:
        """
        Get cache statistics for monitoring and debugging.
        
        Returns:
            Dictionary with cache statistics
        """
        try:
            # This requires django-redis and direct Redis access
            from django_redis import get_redis_connection
            
            redis_conn = get_redis_connection("default")
            
            # Get cache info
            info = redis_conn.info()
            
            # Count bookmark-related keys
            pattern = f"{cls.CACHE_PREFIX}:*"
            bookmark_keys = redis_conn.keys(pattern)
            
            return {
                'total_keys': len(bookmark_keys),
                'memory_used': info.get('used_memory_human', 'Unknown'),
                'hit_ratio': info.get('keyspace_hit_ratio', 'Unknown'),
                'cache_prefix': cls.CACHE_PREFIX,
                'cache_ttl': cls.CACHE_TTL,
            }
            
        except Exception as e:
            logger.warning(f"Failed to get cache stats: {e}")
            return {
                'error': str(e),
                'cache_available': False
            }


class BookmarkCacheManager:
    """
    High-level manager for bookmark caching operations.
    
    This class provides a clean interface for bookmark caching
    and integrates with the existing optimization system.
    """
    
    @staticmethod
    def get_bookmarks_for_objects(user: User, objects: List[Any]) -> Dict[int, bool]:
        """
        Get bookmark status for a list of objects using cache when possible.
        
        This method optimizes for the common case where we need bookmark status
        for multiple objects of the same type (e.g., a page of videos).
        
        Args:
            user: The user to check bookmarks for
            objects: List of model instances to check
            
        Returns:
            Dictionary mapping object_id -> is_bookmarked
        """
        if not user.is_authenticated or not objects:
            return {}
        
        # Group objects by content type
        objects_by_type = {}
        for obj in objects:
            content_type = ContentType.objects.get_for_model(obj)
            if content_type not in objects_by_type:
                objects_by_type[content_type] = []
            objects_by_type[content_type].append(obj)
        
        result = {}
        
        for content_type, type_objects in objects_by_type.items():
            # Try to get from cache first
            cached_bookmarks = BookmarkCacheService.get_user_bookmarks(user, content_type)
            
            if cached_bookmarks is None:
                # Cache miss - preload from database
                cached_bookmarks = BookmarkCacheService.preload_user_bookmarks(user, content_type)
            
            # Check bookmark status for each object
            for obj in type_objects:
                result[obj.id] = obj.id in cached_bookmarks
        
        return result
    
    @staticmethod
    def is_bookmarked(user: User, obj: Any) -> bool:
        """
        Check if a single object is bookmarked by the user.
        
        Args:
            user: The user to check
            obj: The object to check
            
        Returns:
            True if bookmarked, False otherwise
        """
        if not user.is_authenticated:
            return False
        
        content_type = ContentType.objects.get_for_model(obj)
        
        # Try cache first
        cached_bookmarks = BookmarkCacheService.get_user_bookmarks(user, content_type)
        
        if cached_bookmarks is None:
            # Cache miss - preload from database
            cached_bookmarks = BookmarkCacheService.preload_user_bookmarks(user, content_type)
        
        return obj.id in cached_bookmarks
    
    @staticmethod
    def bookmark_created(user: User, content_type: ContentType, object_id: int):
        """
        Handle bookmark creation event for cache management.
        
        This should be called whenever a bookmark is created.
        """
        BookmarkCacheService.add_bookmark_to_cache(user, content_type, object_id)
    
    @staticmethod
    def bookmark_deleted(user: User, content_type: ContentType, object_id: int):
        """
        Handle bookmark deletion event for cache management.
        
        This should be called whenever a bookmark is deleted.
        """
        BookmarkCacheService.remove_bookmark_from_cache(user, content_type, object_id)
    
    @staticmethod
    def user_cache_invalidated(user: User):
        """
        Handle user cache invalidation (e.g., when user data changes).
        
        This should be called when we want to force a cache refresh for a user.
        """
        BookmarkCacheService.invalidate_user_bookmarks(user)
    
    @staticmethod
    def bulk_bookmark_deleted(bookmarks_data: List[Dict[str, Any]]) -> None:
        """
        Handle bulk bookmark deletions efficiently.
        
        Groups bookmarks by user and content type for efficient cache updates.
        
        Args:
            bookmarks_data: List of dicts with keys: user_id, content_type_id, object_id
        """
        try:
            # Group by user for efficient cache updates
            users_affected = {}
            
            for bookmark_data in bookmarks_data:
                user_id = bookmark_data['user_id']
                content_type_id = bookmark_data['content_type_id']
                object_id = bookmark_data['object_id']
                
                if user_id not in users_affected:
                    users_affected[user_id] = {}
                
                if content_type_id not in users_affected[user_id]:
                    users_affected[user_id][content_type_id] = []
                
                users_affected[user_id][content_type_id].append(object_id)
            
            # Update cache for each affected user
            for user_id, content_types in users_affected.items():
                try:
                    # Get user object (with error handling)
                    user = User.objects.get(id=user_id)
                    
                    for content_type_id, object_ids in content_types.items():
                        try:
                            content_type = ContentType.objects.get(id=content_type_id)
                            
                            # Remove multiple bookmarks from cache efficiently
                            for object_id in object_ids:
                                BookmarkCacheService.remove_bookmark_from_cache(
                                    user, content_type, object_id
                                )
                                
                        except ContentType.DoesNotExist:
                            logger.warning(f"ContentType {content_type_id} not found during bulk cache update")
                            continue
                            
                except User.DoesNotExist:
                    logger.warning(f"User {user_id} not found during bulk cache update")
                    continue
            
            total_bookmarks = len(bookmarks_data)
            total_users = len(users_affected)
            logger.info(f"Bulk updated cache for {total_bookmarks} bookmarks across {total_users} users")
            
        except Exception as e:
            logger.error(f"Error in bulk bookmark cache update: {e}")
            # Don't raise - cache errors shouldn't break deletion operations