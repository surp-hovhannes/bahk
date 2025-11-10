"""
Feast-related tasks for the hub app.
"""
import logging
from datetime import datetime

from celery import shared_task
import sentry_sdk

from hub.models import Church
from hub.utils import get_or_create_feast_for_date

logger = logging.getLogger(__name__)


@shared_task(name='hub.tasks.create_feast_date_task')
@sentry_sdk.monitor(monitor_slug='daily-feast-date-creation')
def create_feast_date_task():
    """
    Create feast date for today if it doesn't already exist.
    
    This task:
    1. Gets today's date
    2. Gets the default church
    3. Checks if a Day exists for today's date and church
    4. Checks if a Fast is associated with the Day (skips if so)
    5. Checks if a Feast already exists for that Day
    6. If not, scrapes and creates the feast
    
    Runs daily at 5 minutes past midnight (00:05).
    """
    # Add additional context for Sentry
    sentry_sdk.set_context("feast_task", {
        "type": "feast_date_creation",
        "scheduled": "daily"
    })
    
    # Add breadcrumb for monitoring the flow
    sentry_sdk.add_breadcrumb(
        category="task",
        message="Starting create_feast_date process",
        level="info"
    )
    
    # Get today's date
    today = datetime.today().date()
    logger.info(f"Creating feast date for {today}")
    
    try:
        # Get the default church
        church = Church.objects.get(pk=Church.get_default_pk())
        
        # Use the shared utility function to get or create feast
        feast_obj, feast_created, status_dict = get_or_create_feast_for_date(
            today, 
            church, 
            check_fast=True
        )
        
        # Log the result
        if status_dict["status"] == "skipped":
            reason = status_dict.get("reason", "unknown")
            if reason == "fast_associated":
                logger.info(f"Day {today} is associated with Fast '{status_dict.get('fast_name')}'. Skipping feast lookup.")
            elif reason == "feast_already_exists":
                logger.info(f"Feast already exists for {today}. Skipping creation.")
            elif reason == "no_feast_data":
                logger.warning(f"No feast data found for {today}")
            elif reason == "no_feast_name":
                logger.warning(f"No feast name found for {today}. Skipping.")
            
            sentry_sdk.add_breadcrumb(
                category="task",
                message=f"Feast creation skipped: {reason}",
                level="info" if reason != "no_feast_data" and reason != "no_feast_name" else "warning"
            )
        else:
            action = "Created" if feast_created else "Updated"
            logger.info(f"{action} feast: {feast_obj} for {today}")
            sentry_sdk.add_breadcrumb(
                category="task",
                message=f"{action} feast: {feast_obj.name} for {today}",
                level="info"
            )
        
        return status_dict
        
    except Exception as e:
        logger.error(f"Error creating feast date for {today}: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        raise

