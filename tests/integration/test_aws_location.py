"""
AWS Location Service Geocoding Integration Test

This test verifies that the AWS Location Service geocoding functionality
is working correctly with our Django application.
"""
import os
import sys
import time
import logging
import pytest
from django.conf import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Import Django-dependent modules
from hub.services.aws_geocoding import geocoder
from hub.models import GeocodingCache

# Skip entire module if API key and place index not set in application settings
pytestmark = [
    pytest.mark.skipif(
        not settings.AWS_LOCATION_API_KEY or settings.AWS_LOCATION_PLACE_INDEX == 'ExamplePlaceIndex',
        reason="AWS Location API key or Place Index not configured in application settings"
    ),
    # Mark all tests to ensure Django database is properly set up
    pytest.mark.django_db
]

def test_aws_credentials():
    """Test that AWS credentials are properly configured"""
    logger.info("Testing AWS credentials...")
    
    # Check standard AWS credentials
    required_keys = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_S3_REGION_NAME']
    missing_keys = [key for key in required_keys if not getattr(settings, key, None)]
    
    assert not missing_keys, f"Missing required AWS credentials: {', '.join(missing_keys)}"
    
    # Check AWS Location Service API key
    assert settings.AWS_LOCATION_API_KEY, "AWS_LOCATION_API_KEY is not set"
    logger.info("AWS Location Service API key is configured ✓")
    
    # Check Place Index name
    assert settings.AWS_LOCATION_PLACE_INDEX != 'ExamplePlaceIndex', "AWS_LOCATION_PLACE_INDEX not properly configured"
    logger.info(f"Using AWS Location Place Index: {settings.AWS_LOCATION_PLACE_INDEX} ✓")
    
    logger.info("AWS credentials are configured ✓")

def test_hardcoded_locations():
    """Test geocoding with hardcoded locations"""
    logger.info("Testing hardcoded locations...")
    
    test_locations = [
        "New York, NY",
        "Los Angeles, CA",
        "Chicago, IL"
    ]
    
    # Test all hardcoded locations
    for location in test_locations:
        start_time = time.time()
        coordinates = geocoder.geocode(location)
        duration = time.time() - start_time
        
        assert coordinates is not None, f"Failed to geocode {location}"
        logger.info(f"✓ Successfully geocoded {location} → {coordinates} (took {duration:.2f}s)")

def test_aws_geocoding():
    """Test geocoding with AWS Location Service"""
    logger.info("Testing AWS Location Service geocoding...")
    
    # Print current settings for debugging
    logger.info(f"Current settings - API Key set: {'Yes' if settings.AWS_LOCATION_API_KEY else 'No'}")
    logger.info(f"Current settings - Place Index: {settings.AWS_LOCATION_PLACE_INDEX}")
    logger.info(f"Geocoder object - API Key set: {'Yes' if geocoder.api_key else 'No'}")
    logger.info(f"Geocoder object - Place Index: {geocoder.place_index_name}")
    
    # Test with locations that are unlikely to be in the hardcoded list
    test_locations = [
        "Glendale, CA",
        "Juneau, AK",
        "Providence, RI"
    ]
    
    success_count = 0
    for location in test_locations:
        # Try to bypass hardcoded locations for testing the API
        normalized = location.lower().strip()
        hardcoded_result = geocoder._check_hardcoded_locations(normalized)
        if hardcoded_result is not None:
            logger.info(f"! {location} is in hardcoded locations, skipping AWS test")
            continue
            
        logger.info(f"Testing direct AWS Location Service API call for: {location}")
        start_time = time.time()
        
        # Set up parameters like in hub/services/aws_geocoding.py
        params = {
            'IndexName': settings.AWS_LOCATION_PLACE_INDEX,
            'Text': location,
            'MaxResults': 1
        }
        
        # Add API key if available
        if settings.AWS_LOCATION_API_KEY:
            params['Key'] = settings.AWS_LOCATION_API_KEY
            logger.info("Using API key authentication with Key parameter")
            logger.info(f"Key length: {len(params['Key'])}")
        
        # Dump API call params for debugging (excluding actual key)
        debug_params = params.copy()
        if 'Key' in debug_params:
            debug_params['Key'] = f"[API KEY - {len(debug_params['Key'])} chars]"
        logger.info(f"API call params: {debug_params}")
        
        # Make the direct request
        logger.info("Calling AWS Location Service API...")
        response = geocoder.client.search_place_index_for_text(**params)
        logger.info("API call successful!")
        duration = time.time() - start_time
        
        assert response.get('Results'), f"No results from AWS for {location} - Response: {response}"
        
        place = response['Results'][0]['Place']
        lon, lat = place['Geometry']['Point']
        logger.info(f"✓ AWS geocoded {location} → ({lat}, {lon}) (took {duration:.2f}s)")
        success_count += 1
    
    # Also test the geocoder.geocode method which should now be working
    if success_count > 0:
        test_loc = "Seattle, WA"
        logger.info(f"Testing geocoder.geocode() method with: {test_loc}")
        start_time = time.time()
        coordinates = geocoder.geocode(test_loc)
        duration = time.time() - start_time
        
        assert coordinates, f"geocoder.geocode() found no results for {test_loc}"
        logger.info(f"✓ geocoder.geocode() found {test_loc} → {coordinates} (took {duration:.2f}s)")
    
    assert success_count > 0, "No successful AWS geocoding calls"

@pytest.mark.django_db
def test_geocoding_cache():
    """Test the GeocodingCache model"""
    logger.info("Testing GeocodingCache functionality...")
    
    # Check if migrations are applied
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM hub_geocodingcache LIMIT 1")
            # If we get here, the table exists
    except Exception as e:
        pytest.fail(f"GeocodingCache table not available. Make sure migrations are applied: {str(e)}")
    
    test_location = "Test Location " + str(int(time.time()))
    test_lat = 12.345
    test_lon = 67.890
    
    # Create a cache entry
    cache_entry = GeocodingCache.objects.create(
        location_text=test_location.lower(),
        latitude=test_lat,
        longitude=test_lon
    )
    logger.info(f"✓ Created cache entry for {test_location}")
    
    # Verify it exists
    retrieved = GeocodingCache.objects.get(location_text=test_location.lower())
    assert retrieved.latitude == test_lat and retrieved.longitude == test_lon, \
        f"Retrieved coordinates don't match: ({retrieved.latitude}, {retrieved.longitude})"
    logger.info(f"✓ Retrieved cache entry correctly")
    
    # Cleanup
    cache_entry.delete()
    logger.info(f"✓ Deleted test cache entry") 