"""
Tasks for the hub app.

This module exposes all Celery tasks from the separate task modules.
"""
from .email_tasks import test_email_task, send_fast_reminder_task
from .mapping_tasks import generate_participant_map, update_current_fast_maps
from .geocoding_tasks import batch_geocode_profiles, geocode_profile_location
from .llm_tasks import generate_reading_context_task
from celery import shared_task

@shared_task
def add(x, y):
    """Simple task to add two numbers."""
    return x + y

__all__ = [
    'test_email_task',
    'send_fast_reminder_task',
    'generate_participant_map',
    'update_current_fast_maps',
    'batch_geocode_profiles',
    'geocode_profile_location',
    'generate_reading_context_task',
    'add'
] 