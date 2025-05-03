"""
Celery tasks for the hub app.

This module contains asynchronous tasks that are executed by Celery.
This file is kept for backward compatibility. New code should import from the hub.tasks package.
"""

# Import task functions from the task modules
from hub.tasks.email_tasks import test_email_task, send_fast_reminder_task
from hub.tasks.mapping_tasks import generate_participant_map, update_current_fast_maps
from hub.tasks.geocoding_tasks import batch_geocode_profiles, geocode_profile_location
from hub.tasks.openai_tasks import generate_reading_context_task

# For backward compatibility, ensure these functions/tasks are available by name
__all__ = [
    'test_email_task',
    'send_fast_reminder_task',
    'generate_participant_map',
    'update_current_fast_maps',
    'batch_geocode_profiles',
    'geocode_profile_location',
    'generate_reading_context_task'
] 