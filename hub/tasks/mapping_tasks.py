"""
Map generation tasks for the hub app.

This module generates simple SVG world maps with dots representing fast participants,
with clustering support for areas with many participants.
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
import geopandas as gpd
# import contextily as ctx  # Removing dependency on contextily
from shapely.geometry import Point, Polygon
import pandas as pd
from pathlib import Path
from sklearn.cluster import DBSCAN
import urllib.request
import tempfile
import os
import math

# Set up logging for Celery tasks
logger = get_task_logger(__name__)


def download_world_map():
    """
    Download a simplified world map shapefile.
    
    Returns:
        GeoDataFrame: A GeoDataFrame containing world country boundaries
    """
    # URL for Natural Earth 1:110m Cultural Vectors (Admin 0 - Countries)
    url = "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip"
    
    try:
        # Create a temporary directory to store the downloaded file
        with tempfile.TemporaryDirectory() as temp_dir:
            # Download the zip file
            zip_path = os.path.join(temp_dir, "ne_110m_admin_0_countries.zip")
            logger.info(f"Downloading world map data from {url}")
            urllib.request.urlretrieve(url, zip_path)
            
            # Read the shapefile from the zip
            world = gpd.read_file(f"zip://{zip_path}")
            logger.info("World map data loaded successfully")
            
            return world
    except Exception as e:
        logger.error(f"Error downloading world map data: {e}")
        # Create a very simplified world map (just a rectangle) as a fallback
        logger.warning("Creating a simplified map without country boundaries")
        world = gpd.GeoDataFrame(
            {'geometry': [Polygon([(-180, -90), (180, -90), (180, 90), (-180, 90), (-180, -90)])]},
            geometry='geometry', 
            crs="EPSG:4326"
        )
        return world


def generate_participant_map_svg(participant_locations, output_path, 
                                clustering_distance=0.5, min_cluster_size=3,
                                map_width=10, map_height=6, dpi=150,
                                location_names=None):
    """
    Generate an SVG map showing participant locations with clustering.
    
    Args:
        participant_locations: List of (longitude, latitude) tuples
        output_path: Path to save the SVG file
        clustering_distance: Distance (in degrees) for clustering points
        min_cluster_size: Minimum number of points to form a cluster
        map_width: Width of the map in inches
        map_height: Height of the map in inches
        dpi: Resolution of the map
        location_names: Optional list of location names corresponding to participant_locations
    
    Returns:
        Path to the generated SVG file
    """
    try:
        # Initialize location_names if not provided (but we won't use them for labels)
        if location_names is None:
            location_names = [f"Point {i}" for i in range(len(participant_locations))]
        
        # Filter out invalid coordinates (0,0 or non-real numbers)
        valid_locations = []
        valid_names = []
        for i, (lon, lat) in enumerate(participant_locations):
            # Check if coordinates are valid (not 0,0 and are real numbers)
            if (lon != 0 or lat != 0) and isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
                if not (math.isnan(lon) or math.isnan(lat)):
                    # Ensure longitude is in the correct range (-180 to 180)
                    # This ensures Asian coordinates (positive longitudes) are correctly placed
                    if lon > 180:
                        lon = lon - 360
                    elif lon < -180:
                        lon = lon + 360
                    valid_locations.append((lon, lat))
                    valid_names.append(location_names[i] if i < len(location_names) else f"Point {i}")
        
        # Get the world map shapefile using our existing function
        world = download_world_map()
        
        # Create a GeoDataFrame from participant locations
        if valid_locations:
            # Create a list of Point objects
            points = [Point(lon, lat) for lon, lat in valid_locations]
            
            # Create a GeoDataFrame
            participant_points = gpd.GeoDataFrame({
                'geometry': points,
                'name': valid_names
            }, geometry='geometry', crs="EPSG:4326")
            
            # Check if all points are within the contiguous United States
            us_only = False
            if len(valid_locations) > 0:
                # Define bounding box for contiguous US (approximate)
                us_bounds = {
                    'min_lon': -125.0,  # West coast
                    'max_lon': -66.0,   # East coast
                    'min_lat': 24.0,    # Southern tip of Florida
                    'max_lat': 49.5     # Northern border
                }
                
                # Check if all points are within the US bounds
                all_in_us = all(
                    us_bounds['min_lon'] <= lon <= us_bounds['max_lon'] and
                    us_bounds['min_lat'] <= lat <= us_bounds['max_lat']
                    for lon, lat in valid_locations
                )
                
                us_only = all_in_us
            
            # Perform clustering if there are enough points
            cluster_centers = []
            cluster_sizes = []
            
            if len(valid_locations) >= min_cluster_size:
                # Convert to numpy array for DBSCAN
                coords = np.array(valid_locations)
                
                # For haversine metric, we need to:
                # 1. Convert to (latitude, longitude) order
                # 2. Convert to radians
                haversine_coords = np.radians(coords[:, [1, 0]])  # Swap lat/lon and convert to radians
                
                # Perform clustering
                db = DBSCAN(eps=np.radians(clustering_distance), min_samples=min_cluster_size, metric='haversine')
                labels = db.fit_predict(haversine_coords)
                
                # Add cluster labels to GeoDataFrame
                participant_points['cluster'] = labels
                
                # Get unique clusters (excluding noise points labeled as -1)
                unique_clusters = set(labels)
                if -1 in unique_clusters:
                    unique_clusters.remove(-1)
                
                # Calculate cluster centers and sizes
                for label in unique_clusters:
                    # Count points in this cluster
                    count = np.sum(labels == label)
                    # Get points in this cluster
                    cluster_points = coords[labels == label]
                    # Calculate center of cluster
                    center_lon = cluster_points[:, 0].mean()
                    center_lat = cluster_points[:, 1].mean()
                    
                    cluster_centers.append(Point(center_lon, center_lat))
                    cluster_sizes.append(count)
                
                if cluster_centers:
                    cluster_gdf = gpd.GeoDataFrame({
                        'geometry': cluster_centers,
                        'size': cluster_sizes
                    }, geometry='geometry', crs="EPSG:4326")
            
            # Create the plot
            fig, ax = plt.subplots(figsize=(map_width, map_height), dpi=dpi)
            
            if us_only:
                # Filter to just the US for US-only map
                # The Natural Earth dataset uses 'United States of America' or 'NAME_EN' field
                us = world[world['NAME_EN'] == 'United States of America']
                if us.empty:
                    # Try alternative field names
                    us = world[world['NAME'] == 'United States of America']
                    if us.empty:
                        # If still not found, try with partial match
                        us = world[world.apply(lambda row: any('United States' in str(val) for val in row if isinstance(val, str)), axis=1)]
                
                if not us.empty:
                    us.plot(ax=ax, facecolor='#771831', edgecolor='#771831', linewidth=0.8, alpha=0.8)
                    
                    # Set US-specific boundaries (contiguous US)
                    ax.set_xlim(us_bounds['min_lon'] - 1, us_bounds['max_lon'] + 1)
                    ax.set_ylim(us_bounds['min_lat'] - 1, us_bounds['max_lat'] + 1)
                else:
                    # Fallback to world map if US not found
                    logger.warning("Could not find US in world map data, falling back to world map")
                    world.plot(ax=ax, facecolor='#771831', edgecolor='#771831', linewidth=0.8, alpha=0.8)
                    ax.set_ylim(-60, 85)  # Exclude polar regions
            else:
                # Plot the world map with filled interior and boundary
                world.plot(ax=ax, facecolor='#771831', edgecolor='#771831', linewidth=0.8, alpha=0.8)
                
                # Set map boundaries to exclude Arctic and Antarctic regions
                ax.set_ylim(-60, 85)  # Latitude range from -60° (excludes Antarctica) to 85° (excludes North Pole)
            
            # Remove axes, background color, and simplify
            ax.set_axis_off()
            plt.tight_layout()
            fig.patch.set_facecolor('none')
            
            # Plot individual points (those not in clusters, with label -1)
            if len(valid_locations) > 0:
                non_clustered = participant_points[participant_points['cluster'] == -1] if 'cluster' in participant_points.columns else participant_points
                if len(non_clustered) > 0:
                    # Use red color for individual points to match cluster centers
                    # Increase markersize from 5 to 8 for better visibility
                    non_clustered.plot(ax=ax, color='#FFB700', markersize=30, alpha=0.8, linewidth=0.5)
            
            # Plot cluster centers with size reflecting the number of points
            if len(valid_locations) >= min_cluster_size and 'cluster_gdf' in locals():
                # Scale markers based on cluster size
                # Increase minimum size from 10 to 15
                min_size = 45
                # Increase maximum size from 100 to 120
                max_size = 150
                if len(cluster_centers) > 0:
                    # Scale marker sizes based on the number of participants
                    sizes = cluster_gdf['size'].values
                    normalized_sizes = min_size + (sizes - min(sizes)) * (max_size - min_size) / max(1, (max(sizes) - min(sizes)))
                    
                    # Plot cluster centers with size reflecting the number of points
                    # Increase alpha from 0.8 to 0.9 for better visibility
                    cluster_gdf.plot(
                        ax=ax,
                        color='#FFB700',
                        markersize=normalized_sizes,
                        alpha=0.8,
                        linewidth=0.5
                    )
            
            # Save the plot to SVG file
            plt.savefig(output_path, format='svg', bbox_inches='tight', pad_inches=0.1)
            plt.close(fig)
            
            logger.info(f"Generated participant map at {output_path}")
            return output_path
        else:
            # No valid locations, create an empty map
            fig, ax = plt.subplots(figsize=(map_width, map_height), dpi=dpi)
            
            # Plot the world map
            world.plot(ax=ax, facecolor='#771831', edgecolor='#771831', linewidth=0.8, alpha=0.8)
            
            # Set map boundaries to exclude Arctic and Antarctic regions
            ax.set_ylim(-60, 85)  # Exclude polar regions
            
            ax.set_axis_off()
            plt.tight_layout()
            fig.patch.set_facecolor('none')
            plt.savefig(output_path, format='svg', bbox_inches='tight', pad_inches=0.1)
            plt.close(fig)
            
            logger.info(f"Generated empty participant map at {output_path} (no valid locations)")
            return output_path
    except Exception as e:
        logger.error(f"Error generating participant map: {str(e)}")
        raise


def create_map(fast_id, file_format='svg', dpi=100):
    """
    Create a map with the locations of users participating in a fast.
    This is a simplified version that plots points against a simple world outline.
    
    Args:
        fast_id: ID of the Fast
        file_format: Output format (defaults to 'svg')
        dpi: DPI for rendering (higher = better quality but larger file)
        
    Returns:
        A tuple of (map_file, participant_count)
    """
    # Always use SVG regardless of what was passed
    file_format = 'svg'
    
    try:
        # Get Fast instance
        fast = Fast.objects.get(id=fast_id)
        
        # Get profiles participating in this fast
        profiles = Profile.objects.filter(fasts=fast)
        logger.info(f"Found {profiles.count()} profiles for fast '{fast}'")
        
        # Filter profiles with valid coordinates
        locations = []
        location_names = []
        for profile in profiles:
            if profile.latitude is not None and profile.longitude is not None:
                # Remove debugging log for Asian locations
                locations.append((profile.longitude, profile.latitude))
                location_names.append(profile.location or f"User {profile.id}")
        
        participant_count = len(locations)
        logger.info(f"Found {participant_count} valid locations for fast '{fast}'")
        
        if not locations:
            logger.warning(f"No valid locations found for fast '{fast}'")
            # Return empty file with 0 participants
            return ContentFile(b'', name=f"map_{fast_id}_{uuid4()}.{file_format}"), 0
        
        # Create a temporary file path for the SVG
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as temp_file:
            temp_path = temp_file.name
        
        try:
            # Generate the map using our standalone function
            generate_participant_map_svg(
                participant_locations=locations,
                output_path=temp_path,
                clustering_distance=0.5,
                min_cluster_size=3,
                map_width=10,
                map_height=6,
                dpi=dpi,
                location_names=location_names
            )
            
            # Read the generated SVG file
            with open(temp_path, 'rb') as f:
                svg_content = f.read()
            
            # Create a ContentFile for Django storage
            map_file = ContentFile(svg_content, name=f"map_{fast_id}_{uuid4()}.{file_format}")
            
            return map_file, participant_count
            
        finally:
            # Clean up the temporary file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        
    except Exception as e:
        logger.error(f"Error creating map: {str(e)}")
        # Return empty file with 0 participants as a fallback
        return ContentFile(b'', name=f"map_{fast_id}_error_{uuid4()}.{file_format}"), 0


def generate_sample_map(output_path=None):
    """
    Generate a sample map with random participant locations for testing.
    
    Args:
        output_path: Optional path to save the SVG file. If None, a default path is used.
        
    Returns:
        str: Path to the generated SVG file
    """
    # Create random participant locations
    np.random.seed(42)  # For reproducibility
    
    # New York area cluster
    ny_cluster = [(np.random.normal(-74.0, 0.3), np.random.normal(40.7, 0.3)) for _ in range(25)]
    ny_names = [f"New York User {i+1}" for i in range(25)]
    
    # London area cluster
    london_cluster = [(np.random.normal(0.1, 0.2), np.random.normal(51.5, 0.2)) for _ in range(20)]
    london_names = [f"London User {i+1}" for i in range(20)]
    
    # Tokyo area cluster
    tokyo_cluster = [(np.random.normal(139.7, 0.3), np.random.normal(35.7, 0.3)) for _ in range(15)]
    tokyo_names = [f"Tokyo User {i+1}" for i in range(15)]
    
    # Random individual points
    random_points = [(np.random.uniform(-180, 180), np.random.uniform(-60, 70)) for _ in range(30)]
    random_names = [f"Random User {i+1}" for i in range(30)]
    
    # Combine all points and names
    all_points = ny_cluster + london_cluster + tokyo_cluster + random_points
    all_names = ny_names + london_names + tokyo_names + random_names
    
    # Generate and return the map
    if output_path is None:
        output_dir = Path("static/maps")
        output_dir.mkdir(exist_ok=True, parents=True)
        output_path = output_dir / "sample_participant_map.svg"
    
    return generate_participant_map_svg(all_points, str(output_path), location_names=all_names)


@shared_task(bind=True, max_retries=3, name='hub.tasks.generate_participant_map')
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
        
        # Generate the map (always SVG)
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


@shared_task(name='hub.tasks.update_current_fast_maps')
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


if __name__ == "__main__":
    # This allows testing the map generation directly by running this file
    output_path = generate_sample_map()
    print(f"Sample map generated at: {output_path}") 