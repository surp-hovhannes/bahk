"""
Celery tasks for the hub app.

This module contains asynchronous tasks that are executed by Celery.
This file is kept for backward compatibility. New code should import from the hub.tasks package.
"""

# Re-export tasks from the tasks package for backward compatibility
from hub.tasks import (
    test_email_task,
    send_fast_reminder_task,
    generate_participant_map,
    update_current_fast_maps,
    batch_geocode_profiles
)

# For backward compatibility, ensure these functions/tasks are available by name
__all__ = [
    'test_email_task',
    'send_fast_reminder_task',
    'generate_participant_map',
    'update_current_fast_maps',
    'batch_geocode_profiles'
] 