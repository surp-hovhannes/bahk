"""
Analytics caching service to improve dashboard performance.
Implements smart caching with automatic invalidation.
"""

from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


class AnalyticsCacheService:
    """
    Smart caching service for analytics data with automatic invalidation.
    
    Cache Strategy:
    - Cache analytics data for common date ranges (7, 30, 90 days)
    - TTL varies by data freshness requirements:
      - Current day data: 5 minutes (frequently changing)
      - Past day data: 1 hour (stable)
      - Historical data: 4 hours (very stable)
    - Automatic invalidation on new events
    """
    
    # Cache TTL settings (in seconds)
    CURRENT_DAY_TTL = 300      # 5 minutes
    RECENT_DATA_TTL = 3600     # 1 hour  
    HISTORICAL_DATA_TTL = 14400 # 4 hours
    
    CACHE_PREFIX = "analytics"
    CACHE_VERSION = "v2"  # Increment to invalidate all analytics caches
    
    @classmethod
    def _get_cache_key(cls, cache_type, **kwargs):
        """Generate a cache key for analytics data."""
        # Create a deterministic hash from the parameters
        key_data = {
            'type': cache_type,
            'version': cls.CACHE_VERSION,
            **kwargs
        }
        key_string = json.dumps(key_data, sort_keys=True)
        key_hash = hashlib.md5(key_string.encode()).hexdigest()[:12]
        
        return f"{cls.CACHE_PREFIX}:{cache_type}:{key_hash}"
    
    @classmethod
    def _get_ttl_for_date_range(cls, days):
        """Get appropriate TTL based on date range recency."""
        if days <= 1:
            return cls.CURRENT_DAY_TTL
        elif days <= 7:
            return cls.RECENT_DATA_TTL
        else:
            return cls.HISTORICAL_DATA_TTL
    
    @classmethod
    def get_daily_aggregates(cls, start_of_window, num_days):
        """
        Get cached daily aggregates or return None if not cached.
        
        Args:
            start_of_window: datetime start of window
            num_days: number of days
            
        Returns:
            dict or None: Cached data or None if not found
        """
        cache_key = cls._get_cache_key(
            'daily_aggregates',
            start_date=start_of_window.isoformat(),
            num_days=num_days
        )
        
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.debug(f"Cache HIT for daily aggregates: {cache_key}")
            return cached_data
        
        logger.debug(f"Cache MISS for daily aggregates: {cache_key}")
        return None
    
    @classmethod
    def set_daily_aggregates(cls, start_of_window, num_days, data):
        """
        Cache daily aggregates data.
        
        Args:
            start_of_window: datetime start of window
            num_days: number of days
            data: dict of aggregated data to cache
        """
        cache_key = cls._get_cache_key(
            'daily_aggregates',
            start_date=start_of_window.isoformat(),
            num_days=num_days
        )
        
        ttl = cls._get_ttl_for_date_range(num_days)
        
        try:
            cache.set(cache_key, data, ttl)
            logger.debug(f"Cached daily aggregates: {cache_key} (TTL: {ttl}s)")
        except Exception as e:
            logger.warning(f"Failed to cache daily aggregates: {e}")
    
    @classmethod
    def get_fast_data(cls, fast_ids, start_of_window, num_days):
        """
        Get cached fast-specific data.
        
        Args:
            fast_ids: list of fast IDs
            start_of_window: datetime start of window  
            num_days: number of days
            
        Returns:
            dict or None: Cached data or None if not found
        """
        # Sort fast_ids for consistent cache keys
        fast_ids_sorted = sorted(fast_ids)
        
        cache_key = cls._get_cache_key(
            'fast_data',
            fast_ids=fast_ids_sorted,
            start_date=start_of_window.isoformat(),
            num_days=num_days
        )
        
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.debug(f"Cache HIT for fast data: {cache_key}")
            return cached_data
            
        logger.debug(f"Cache MISS for fast data: {cache_key}")
        return None
    
    @classmethod
    def set_fast_data(cls, fast_ids, start_of_window, num_days, data):
        """
        Cache fast-specific data.
        
        Args:
            fast_ids: list of fast IDs
            start_of_window: datetime start of window
            num_days: number of days
            data: dict of fast data to cache
        """
        # Sort fast_ids for consistent cache keys
        fast_ids_sorted = sorted(fast_ids)
        
        cache_key = cls._get_cache_key(
            'fast_data',
            fast_ids=fast_ids_sorted,
            start_date=start_of_window.isoformat(),
            num_days=num_days
        )
        
        ttl = cls._get_ttl_for_date_range(num_days)
        
        try:
            cache.set(cache_key, data, ttl)
            logger.debug(f"Cached fast data: {cache_key} (TTL: {ttl}s)")
        except Exception as e:
            logger.warning(f"Failed to cache fast data: {e}")
    
    @classmethod
    def invalidate_all_analytics(cls):
        """
        Invalidate all analytics caches.
        Call this when events are created/updated that might affect analytics.
        """
        try:
            # Pattern-based cache deletion if supported
            if hasattr(cache, 'delete_pattern'):
                pattern = f"{cls.CACHE_PREFIX}:*"
                deleted = cache.delete_pattern(pattern)
                logger.info(f"Invalidated {deleted} analytics cache entries")
            elif hasattr(cache, 'keys'):
                # Redis backend with keys() support
                pattern = f"{cls.CACHE_PREFIX}:*"
                keys = cache.keys(pattern)
                if keys:
                    cache.delete_many(keys)
                    logger.info(f"Invalidated {len(keys)} analytics cache entries")
            else:
                # Fallback: increment cache version to invalidate all
                cls.CACHE_VERSION = f"v{int(cls.CACHE_VERSION[1:]) + 1}"
                logger.info("Incremented cache version to invalidate all analytics caches")
                
        except Exception as e:
            logger.warning(f"Failed to invalidate analytics caches: {e}")
    
    @classmethod
    def invalidate_current_day(cls):
        """
        Invalidate only current day caches (lighter weight than full invalidation).
        Use this for events that only affect today's data.
        """
        try:
            today = timezone.now().date()
            
            # Invalidate caches that include today
            for days in [1, 7, 30, 90, 365]:
                start_of_window = timezone.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) - timedelta(days=days-1)
                
                cache_key = cls._get_cache_key(
                    'daily_aggregates',
                    start_date=start_of_window.isoformat(),
                    num_days=days
                )
                cache.delete(cache_key)
                
            logger.debug("Invalidated current day analytics caches")
            
        except Exception as e:
            logger.warning(f"Failed to invalidate current day caches: {e}")


# Cache warming utilities
class AnalyticsCacheWarmer:
    """
    Utilities to pre-warm analytics caches during low-traffic periods.
    """
    
    @classmethod
    def warm_common_date_ranges(cls):
        """
        Pre-warm caches for common date ranges (7, 30, 90 days).
        Run this as a periodic task during off-peak hours.
        """
        from .analytics_optimizer import AnalyticsQueryOptimizer
        
        common_ranges = [7, 30, 90]
        now = timezone.now()
        
        for days in common_ranges:
            try:
                end_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                start_of_window = end_of_today - timedelta(days=days)
                
                # Check if already cached
                cached = AnalyticsCacheService.get_daily_aggregates(start_of_window, days)
                if cached:
                    logger.debug(f"Skipping cache warming for {days} days - already cached")
                    continue
                
                # Generate and cache data
                logger.info(f"Warming cache for {days} days...")
                daily_aggregates = AnalyticsQueryOptimizer.get_daily_event_aggregates(
                    start_of_window, days
                )
                
                AnalyticsCacheService.set_daily_aggregates(
                    start_of_window, days, daily_aggregates
                )
                
                logger.info(f"Successfully warmed cache for {days} days")
                
            except Exception as e:
                logger.error(f"Failed to warm cache for {days} days: {e}")
                
        logger.info("Cache warming completed")
