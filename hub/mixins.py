from django.utils import timezone
import logging

from hub.constants import DAYS_TO_CACHE_THUMBNAIL

class ThumbnailCacheMixin:
    """Mixin to handle thumbnail caching for models with ImageSpecField thumbnails."""
    
    def update_thumbnail_cache(self, obj, source_field, thumbnail_field):
        """
        Updates the thumbnail cache if needed.
        
        Args:
            obj: The model instance
            source_field: Name of the source image field (e.g., 'profile_image')
            thumbnail_field: Name of the thumbnail field (e.g., 'profile_image_thumbnail')
        """
        if not hasattr(obj, 'cached_thumbnail_url') or not hasattr(obj, 'cached_thumbnail_updated'):
            return
            
        source = getattr(obj, source_field, None)
        if not source:
            return
            
        should_update_cache = (
            not obj.cached_thumbnail_url or
            not obj.cached_thumbnail_updated or
            (timezone.now() - obj.cached_thumbnail_updated).days >= DAYS_TO_CACHE_THUMBNAIL
        )
        
        if should_update_cache:
            try:
                thumbnail = getattr(obj, thumbnail_field)
                # Force generation of the thumbnail
                thumbnail.generate()
                
                # Update the cache
                obj.cached_thumbnail_url = thumbnail.url
                obj.cached_thumbnail_updated = timezone.now()
                obj.save(update_fields=['cached_thumbnail_url', 'cached_thumbnail_updated'])
                
                return obj.cached_thumbnail_url
            except Exception as e:
                logging.error(f"Error updating thumbnail cache for {obj.__class__.__name__} {obj.id}: {e}")
                
        return obj.cached_thumbnail_url if obj.cached_thumbnail_url else None 