"""
AWS Location Service Geocoding Integration

This module provides geocoding functionality using AWS Location Service.
It uses the existing AWS credentials from settings.py and provides
both direct API access and a Django-friendly service class.
"""
import time
import logging
import boto3
from django.conf import settings
from botocore.exceptions import ClientError, BotoCoreError
from typing import Dict, Tuple, Optional, List, Any

logger = logging.getLogger(__name__)

# Create a place index name with a default fallback
PLACE_INDEX_NAME = getattr(settings, 'AWS_LOCATION_PLACE_INDEX', 'ExamplePlaceIndex')
API_KEY = getattr(settings, 'AWS_LOCATION_API_KEY', None)

# Common locations hardcoded for fallback and faster responses
COMMON_LOCATIONS = {
    "new york, ny": (40.7128, -74.0060),
    "los angeles, ca": (34.0522, -118.2437),
    "chicago, il": (41.8781, -87.6298),
    "houston, tx": (29.7604, -95.3698),
    "phoenix, az": (33.4484, -112.0740),
    "philadelphia, pa": (39.9526, -75.1652),
    "san antonio, tx": (29.4241, -98.4936),
    "san diego, ca": (32.7157, -117.1611),
    "dallas, tx": (32.7767, -96.7970),
    "san jose, ca": (37.3382, -121.8863),
    "austin, tx": (30.2672, -97.7431),
    "jacksonville, fl": (30.3322, -81.6557),
    "fort worth, tx": (32.7555, -97.3308),
    "columbus, oh": (39.9612, -82.9988),
    "charlotte, nc": (35.2271, -80.8431),
    "san francisco, ca": (37.7749, -122.4194),
    "indianapolis, in": (39.7684, -86.1581),
    "seattle, wa": (47.6062, -122.3321),
    "denver, co": (39.7392, -104.9903),
    "washington, dc": (38.9072, -77.0369),
    "boston, ma": (42.3601, -71.0589),
    "nashville, tn": (36.1627, -86.7816),
    "baltimore, md": (39.2904, -76.6122),
    "louisville, ky": (38.2527, -85.7585),
    "portland, or": (45.5051, -122.6750),
    "las vegas, nv": (36.1699, -115.1398),
    "milwaukee, wi": (43.0389, -87.9065),
    "albuquerque, nm": (35.0844, -106.6504),
    "tucson, az": (32.2226, -110.9747),
    "fresno, ca": (36.7378, -119.7871),
    "sacramento, ca": (38.5816, -121.4944),
    "atlanta, ga": (33.7490, -84.3880),
    "kansas city, mo": (39.0997, -94.5786),
    "miami, fl": (25.7617, -80.1918),
    "raleigh, nc": (35.7796, -78.6382),
    "omaha, ne": (41.2565, -95.9345),
    "minneapolis, mn": (44.9778, -93.2650),
    "orlando, fl": (28.5383, -81.3792),
}


class AWSLocationServiceGeocoderError(Exception):
    """Exception raised for errors in AWS Location Service geocoding."""
    pass


class AWSLocationServiceGeocoder:
    """
    AWS Location Service geocoder implementation.
    
    This class provides geocoding functionality using AWS Location Service,
    with fallback to hardcoded common locations for better performance and reliability.
    """
    
    def __init__(self, place_index_name: str = None, api_key: str = None):
        """
        Initialize the AWS Location Service geocoder.
        
        Args:
            place_index_name: The name of the AWS Location Service place index to use.
                              If not provided, uses the value from settings.
            api_key: Optional API key for AWS Location Service. If provided, uses API key
                     authentication instead of IAM role authentication.
        """
        self.place_index_name = place_index_name or PLACE_INDEX_NAME
        self.api_key = api_key or API_KEY
        self._client = None
        self._last_request_time = 0
        self._request_interval = 0.2  # 5 requests per second max (200ms between requests)
    
    @property
    def client(self):
        """
        Lazy initialization of AWS Location Service client.
        
        Returns:
            boto3.client: Initialized AWS Location Service client
        """
        if self._client is None:
            # Check if we're using API key authentication
            if self.api_key:
                # Create client with API key authentication
                self._client = boto3.client(
                    'location',
                    region_name=settings.AWS_S3_REGION_NAME,
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    # Additional API key header will be added on each request
                )
            else:
                # Create client with standard IAM authentication
                self._client = boto3.client(
                    'location',
                    region_name=settings.AWS_S3_REGION_NAME,
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
                )
        return self._client
    
    def _rate_limit(self):
        """
        Enforce rate limiting for AWS Location Service API calls.
        
        This method ensures we don't exceed the service's rate limits
        by adding a delay between requests if needed.
        """
        current_time = time.time()
        time_since_last_request = current_time - self._last_request_time
        
        if time_since_last_request < self._request_interval:
            # Sleep to maintain minimum interval between requests
            time.sleep(self._request_interval - time_since_last_request)
        
        self._last_request_time = time.time()
    
    def _normalize_location(self, location: str) -> str:
        """
        Normalize location text for consistent lookup.
        
        Args:
            location: The location text to normalize
            
        Returns:
            Normalized location string (lowercase, stripped)
        """
        if not location:
            return ""
        
        return location.lower().strip()
    
    def _check_hardcoded_locations(self, location: str) -> Optional[Tuple[float, float]]:
        """
        Check if location exists in hardcoded common locations.
        
        Args:
            location: Normalized location string
            
        Returns:
            Tuple of (latitude, longitude) if found, None otherwise
        """
        return COMMON_LOCATIONS.get(location)
    
    def geocode(self, location: str) -> Optional[Tuple[float, float]]:
        """
        Geocode a location string to coordinates.
        
        This method first checks hardcoded common locations for faster response,
        then falls back to AWS Location Service if needed.
        
        Args:
            location: The location string to geocode
            
        Returns:
            Tuple of (latitude, longitude) if successful, None otherwise
            
        Raises:
            AWSLocationServiceGeocoderError: If an error occurs during geocoding
        """
        if not location:
            logger.warning("Empty location provided for geocoding")
            return None
            
        normalized_location = self._normalize_location(location)
        
        # First check hardcoded locations for common places
        coordinates = self._check_hardcoded_locations(normalized_location)
        if coordinates:
            logger.debug(f"Found {location} in hardcoded locations: {coordinates}")
            return coordinates
            
        try:
            # Apply rate limiting before making API call
            self._rate_limit()
            
            # Prepare request parameters
            params = {
                'IndexName': self.place_index_name,
                'Text': location,
                'MaxResults': 1
            }
            
            # If using API key, add it as the Key parameter (not in headers)
            if self.api_key:
                params['Key'] = self.api_key
            
            # Make the geocoding request
            response = self.client.search_place_index_for_text(**params)
            
            # Check if we got results
            if not response.get('Results') or len(response['Results']) == 0:
                logger.warning(f"No geocoding results found for: {location}")
                return None
                
            # Extract coordinates from the first result
            place = response['Results'][0]['Place']
            # AWS returns as [longitude, latitude]
            lon, lat = place['Geometry']['Point']
            # But we return as (latitude, longitude) for consistency with other geocoding services
            return (lat, lon)
                
        except (ClientError, BotoCoreError) as e:
            error_message = f"AWS Location Service error for {location}: {str(e)}"
            logger.error(error_message)
            raise AWSLocationServiceGeocoderError(error_message) from e
        except Exception as e:
            error_message = f"Unexpected error geocoding {location}: {str(e)}"
            logger.error(error_message)
            raise AWSLocationServiceGeocoderError(error_message) from e

    def batch_geocode(self, locations: List[str]) -> Dict[str, Optional[Tuple[float, float]]]:
        """
        Geocode multiple locations in batch.
        
        Args:
            locations: List of location strings to geocode
            
        Returns:
            Dictionary mapping original location strings to their coordinates
            (None for locations that couldn't be geocoded)
        """
        results = {}
        
        for location in locations:
            if not location:
                results[location] = None
                continue
                
            try:
                results[location] = self.geocode(location)
            except AWSLocationServiceGeocoderError as e:
                logger.error(f"Failed to geocode {location}: {str(e)}")
                results[location] = None
                
        return results


# Singleton instance for easy import and use
geocoder = AWSLocationServiceGeocoder()


def get_coordinates(location: str) -> Optional[Tuple[float, float]]:
    """
    Convenience function to geocode a single location.
    
    Args:
        location: Location string to geocode
        
    Returns:
        Tuple of (latitude, longitude) if successful, None otherwise
    """
    try:
        return geocoder.geocode(location)
    except Exception as e:
        logger.error(f"Error geocoding {location}: {str(e)}")
        return None 