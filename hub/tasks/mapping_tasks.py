"""
Map generation tasks for the hub app.
"""
import time
import logging
from io import BytesIO
from uuid import uuid4
from django.core.files.base import ContentFile
from django.utils import timezone
from celery import shared_task
from celery.utils.log import get_task_logger
from hub.models import FastParticipantMap, Fast, Profile

# Configure matplotlib for non-interactive server environment
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

# Set up logging for Celery tasks
logger = get_task_logger(__name__)

def create_map(fast_id, file_format='svg', dpi=100):
    """
    Create a map with the locations of users participating in a fast.
    This simplified version only shows points without a world map background.
    
    Args:
        fast_id: ID of the Fast
        file_format: Output format ('svg' or 'png')
        dpi: DPI for rendering (higher = better quality but larger file)
        
    Returns:
        A tuple of (map_file, participant_count)
    """
    try:
        # Get Fast instance
        fast = Fast.objects.get(id=fast_id)
        
        # Get profiles participating in this fast
        profiles = Profile.objects.filter(fasts=fast)
        logger.info(f"Found {profiles.count()} profiles for fast '{fast}'")
        
        # Filter profiles with valid coordinates
        locations = []
        for profile in profiles:
            if profile.latitude is not None and profile.longitude is not None:
                locations.append({
                    'name': profile.name or 'Anonymous',
                    'latitude': profile.latitude,
                    'longitude': profile.longitude
                })
        
        logger.info(f"Found {len(locations)} valid locations for fast '{fast}'")
        
        if not locations:
            logger.warning(f"No valid locations found for fast '{fast}'")
            # Return empty file with 0 participants
            return ContentFile(b'', name=f"map_{fast_id}_{uuid4()}.{file_format}"), 0
        
        # Create a simple plot with points only
        fig, ax = plt.subplots(figsize=(12, 8), facecolor='#f0f0f0')
        
        # Plot points directly without GeoDataFrame
        lats = [loc['latitude'] for loc in locations]
        lons = [loc['longitude'] for loc in locations]
        
        # Create a scatter plot of the locations
        ax.scatter(lons, lats, color='red', s=100, alpha=0.7, edgecolor='white')
        
        # Set title and style the plot
        ax.set_title(f'Participants in {fast.name} Fast', fontsize=16)
        ax.set_xlabel('Longitude')
        ax.set_ylabel('Latitude')
        ax.grid(True, linestyle='--', alpha=0.7)
        
        # Adjust axis limits to provide some padding
        margin = 10  # degrees
        min_lon, max_lon = min(lons) - margin, max(lons) + margin
        min_lat, max_lat = min(lats) - margin, max(lats) + margin
        
        # Make sure we don't exceed valid lat/lon ranges
        min_lat = max(-90, min_lat)
        max_lat = min(90, max_lat)
        min_lon = max(-180, min_lon)
        max_lon = min(180, max_lon)
        
        ax.set_xlim(min_lon, max_lon)
        ax.set_ylim(min_lat, max_lat)
        
        # Save to BytesIO
        buffer = BytesIO()
        plt.savefig(buffer, format=file_format, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        
        # Create ContentFile
        buffer.seek(0)
        map_file = ContentFile(buffer.read(), name=f"map_{fast_id}_{uuid4()}.{file_format}")
        
        logger.info(f"Map file created for fast '{fast}'")
        return map_file, len(locations)
        
    except Exception as e:
        logger.error(f"Error creating map: {str(e)}")
        # Return empty file with 0 participants as a fallback
        return ContentFile(b'', name=f"map_{fast_id}_error_{uuid4()}.{file_format}"), 0

@shared_task(bind=True, max_retries=3)
def generate_participant_map(self, fast_id, delay=0):
    """
    Generate a map of participants for the given fast.
    
    Args:
        fast_id: ID of the Fast
        delay: Optional delay in seconds before processing (for debugging)
        
    Returns:
        Dict with status and details of the created map
    """
    if delay > 0:
        logger.info(f"Delaying map generation for {delay} seconds")
        time.sleep(delay)
    
    try:
        # Create the map
        logger.info(f"Generating map for Fast ID: {fast_id}")
        fast = Fast.objects.get(id=fast_id)
        
        # Generate the map
        map_file, participant_count = create_map(fast_id)
        
        # Save to database
        participant_map, created = FastParticipantMap.objects.get_or_create(fast=fast)
        participant_map.map_file = map_file
        participant_map.participant_count = participant_count
        participant_map.save()
        
        result = {
            "status": "success",
            "fast_id": fast_id,
            "participants": participant_count,
            "map_url": participant_map.map_url
        }
        logger.info(f"Map generated successfully for Fast ID: {fast_id}")
        return result
        
    except Exception as e:
        logger.error(f"Error generating map for Fast ID {fast_id}: {str(e)}")
        self.retry(exc=e, countdown=60)  # Retry after 60 seconds
        return {"status": "error", "message": str(e)}

@shared_task
def update_current_fast_maps():
    """
    Update maps for current and upcoming fasts only.
    This ensures we don't waste resources on past fasts.
    """
    try:
        today = timezone.now().date()
        
        # Find current and upcoming fasts by checking if they have any days today or in the future
        current_fasts = Fast.objects.filter(
            days__date__gte=today
        ).distinct()
        
        count = 0
        for fast in current_fasts:
            # Generate map for each current/upcoming fast
            generate_participant_map.delay(fast.id)
            count += 1
            
        logging.info(f"Scheduled map generation for {count} current/upcoming fasts")
        return count
    except Exception as e:
        logging.error(f"Error updating current fast maps: {str(e)}")
        return 0 