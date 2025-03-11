"""
Geocoding tasks for the hub app.
"""
import logging
from django.db import transaction
from django.utils import timezone
from celery import shared_task
from celery.utils.log import get_task_logger
from hub.models import Profile, GeocodingCache
from hub.services.aws_geocoding import geocoder

# Set up logging for Celery tasks
logger = get_task_logger(__name__)

@shared_task(bind=True, max_retries=3)
def geocode_profile_location(self, profile_id, location):
    """
    Geocode a single profile's location and update its coordinates.
    
    This task is triggered when a profile's location is updated.
    It handles the geocoding asynchronously to improve user experience.
    
    Args:
        profile_id: ID of the Profile to update
        location: Location string to geocode
        
    Returns:
        Dict with status and result details
    """
    logger.info(f"Geocoding location for Profile ID: {profile_id}, Location: {location}")
    
    try:
        # Skip empty locations
        if not location:
            logger.info(f"Empty location for Profile ID: {profile_id}, skipping")
            return {"status": "skipped", "reason": "empty_location"}
        
        # Get the profile
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error(f"Profile ID {profile_id} not found")
            return {"status": "error", "reason": "profile_not_found"}
        
        # Check if we need to update (in case profile was updated again while task was pending)
        if profile.location != location:
            logger.info(f"Profile location changed since task was scheduled, using current value: {profile.location}")
            location = profile.location
        
        # First check the cache
        normalized = location.lower().strip()
        cache_entry = GeocodingCache.objects.filter(
            location_text=normalized,
            error_count__lt=5
        ).first()
        
        if cache_entry and cache_entry.latitude and cache_entry.longitude:
            # Use cached coordinates
            profile.latitude = cache_entry.latitude
            profile.longitude = cache_entry.longitude
            profile.save(update_fields=['latitude', 'longitude'])
            logger.info(f"Updated Profile {profile_id} with cached coordinates: ({profile.latitude}, {profile.longitude})")
            return {
                "status": "success", 
                "source": "cache",
                "coordinates": (profile.latitude, profile.longitude)
            }
        
        # Use AWS Location Service
        coordinates = geocoder.geocode(location)
        
        if coordinates:
            # We got valid coordinates
            profile.latitude, profile.longitude = coordinates
            profile.save(update_fields=['latitude', 'longitude'])
            
            # Save to cache
            if not cache_entry:
                GeocodingCache.objects.create(
                    location_text=normalized,
                    latitude=coordinates[0],
                    longitude=coordinates[1]
                )
            else:
                cache_entry.latitude = coordinates[0]
                cache_entry.longitude = coordinates[1]
                cache_entry.error_count = 0  # Reset error count on success
                cache_entry.save()
                
            logger.info(f"Updated Profile {profile_id} with coordinates: {coordinates}")
            return {
                "status": "success", 
                "source": "aws",
                "coordinates": coordinates
            }
        elif cache_entry:
            # Increment error counter for failed geocoding attempts
            cache_entry.error_count += 1
            cache_entry.save()
            
        logger.warning(f"Failed to geocode location for Profile {profile_id}: {location}")
        return {"status": "error", "reason": "geocoding_failed"}
        
    except Exception as e:
        logger.error(f"Error geocoding location for Profile {profile_id}: {e}")
        # Retry the task with exponential backoff
        self.retry(exc=e, countdown=2 ** self.request.retries * 60)  # 1min, 2min, 4min, etc.
        return {"status": "error", "reason": str(e)}

@shared_task(bind=True, max_retries=3, name='hub.tasks.batch_geocode_profiles')
def batch_geocode_profiles(self, update_all=False):
    """
    Batch geocode all profiles with locations that don't have coordinates.
    
    Uses AWS Location Service to geocode locations and updates Profile coordinates.
    
    Args:
        update_all: If True, update all profiles regardless of existing coordinates
        
    Returns:
        Dict with status and statistics
    """
    logger.info(f"Starting batch geocoding task (update_all={update_all})")
    
    try:
        # Get profiles needing geocoding
        if update_all:
            profiles = Profile.objects.filter(location__isnull=False).exclude(location='')
        else:
            profiles = Profile.objects.filter(
                location__isnull=False, 
                latitude__isnull=True, 
                longitude__isnull=True
            ).exclude(location='')
            
        logger.info(f"Found {profiles.count()} profiles to geocode")
        
        # Track statistics
        stats = {
            "total": profiles.count(),
            "successful": 0,
            "failed": 0,
            "cached": 0,
            "locations_processed": []
        }
        
        # Process in smaller batches to avoid memory issues
        batch_size = 50
        for i in range(0, profiles.count(), batch_size):
            batch = profiles[i:i+batch_size]
            
            # Get unique locations to geocode
            unique_locations = {p.location.strip().lower(): p.location for p in batch if p.location}
            logger.info(f"Batch {i//batch_size + 1}: Processing {len(unique_locations)} unique locations")
            
            # Find existing cache entries
            normalized_locations = list(unique_locations.keys())
            cached_entries = {
                entry.location_text: (entry.latitude, entry.longitude) 
                for entry in GeocodingCache.objects.filter(
                    location_text__in=normalized_locations,
                    error_count__lt=5
                )
            }
            
            # Identify locations that need geocoding
            to_geocode = [
                location for normalized, location in unique_locations.items() 
                if normalized not in cached_entries
            ]
            
            # Geocode missing locations
            if to_geocode:
                geocoded_results = geocoder.batch_geocode(to_geocode)
                
                # Update cache with new results
                with transaction.atomic():
                    for location, coordinates in geocoded_results.items():
                        if not location:
                            continue
                            
                        normalized = location.strip().lower()
                        
                        if coordinates:
                            # Successfully geocoded
                            GeocodingCache.objects.update_or_create(
                                location_text=normalized,
                                defaults={
                                    'latitude': coordinates[0],
                                    'longitude': coordinates[1],
                                    'error_count': 0
                                }
                            )
                            cached_entries[normalized] = coordinates
                            stats["successful"] += 1
                        else:
                            # Failed to geocode
                            cache_entry, created = GeocodingCache.objects.get_or_create(
                                location_text=normalized,
                                defaults={
                                    'latitude': 0,
                                    'longitude': 0,
                                    'error_count': 1
                                }
                            )
                            if not created:
                                cache_entry.error_count += 1
                                cache_entry.save()
                            stats["failed"] += 1
                        
                        stats["locations_processed"].append(location)
            
            # Update profiles with coordinates
            with transaction.atomic():
                for profile in batch:
                    if not profile.location:
                        continue
                        
                    normalized = profile.location.strip().lower()
                    if normalized in cached_entries:
                        coordinates = cached_entries[normalized]
                        if coordinates:
                            profile.latitude, profile.longitude = coordinates
                            profile.save(update_fields=['latitude', 'longitude'])
                            stats["cached"] += 1
            
            logger.info(f"Completed batch {i//batch_size + 1}")
        
        return {
            "status": "success",
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"Error in batch geocoding: {str(e)}")
        self.retry(exc=e, countdown=300)  # Retry after 5 minutes
        return {
            "status": "error",
            "message": str(e)
        } 