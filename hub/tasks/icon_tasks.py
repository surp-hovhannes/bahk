"""Tasks for icon matching; discovery is shared with the public API."""

import logging

from celery import shared_task
from django.db import transaction

from hub.models import Feast
from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_match_service import match_icons
from icons.models import Icon

logger = logging.getLogger(__name__)


def _match_icons_with_llm(icons, request, max_results=3):
    """Legacy list interface; production consumers use the structured outcome."""
    if not isinstance(request, IconMatchRequest):
        request = IconMatchRequest(
            kind="feast", primary_text=str(request), auto_assign_policy="feast_strict", max_results=max_results
        )
    from dataclasses import replace

    return match_icons(icons, replace(request, max_results=max_results)).matches


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def match_icon_to_feast_task(self, feast_id: int):
    """
    Match an icon to a feast using AI-powered icon matching.

    Args:
        feast_id: ID of the Feast to match an icon for
    """
    try:
        feast = Feast.objects.select_related("church").get(pk=feast_id)
    except Feast.DoesNotExist:
        logger.error("Feast with id %s not found.", feast_id)
        return

    # Skip if icon is already set
    if feast.icon:
        logger.info("Feast %s already has an icon assigned, skipping.", feast_id)
        return

    # Icons are scoped per church, which is now the feast's own key rather than its day's
    church = feast.church
    if not church:
        logger.warning("Feast %s has no associated church, cannot match icons.", feast_id)
        return

    # Get icons for this church
    icons = list(Icon.objects.filter(church=church).select_related("church").prefetch_related("tags"))

    if not icons:
        logger.info("No icons found for church %s, cannot match icon for feast %s.", church.id, feast_id)
        return

    # Use feast name as the prompt
    prompt = feast.name

    try:
        # Perform icon matching
        outcome = match_icons(
            icons,
            IconMatchRequest(
                kind="feast",
                primary_text=prompt,
                auto_assign_policy="feast_strict",
                max_results=1,
            ),
        )

        if outcome.status != "complete":
            logger.info("Icon matching for feast %s is %s: %s", feast_id, outcome.status, outcome.diagnostics)
            return
        matched_results = outcome.matches
        if not matched_results:
            logger.info("No icon matches found for feast %s (%s).", feast_id, prompt)
            return

        first_match = matched_results[0]
        match_confidence = first_match.get("confidence")
        match_tier = first_match.get("match_tier")

        if first_match.get("auto_assignable"):
            # Found a high confidence match, save it
            icon_id = first_match["id"]
            try:
                with transaction.atomic():
                    locked_feast = Feast.objects.select_for_update().get(pk=feast_id)
                    if (
                        locked_feast.icon_id is not None
                        or locked_feast.church_id != church.id
                        or locked_feast.name != prompt
                    ):
                        logger.info(
                            "Feast %s received an icon while matching, skipping.",
                            feast_id,
                        )
                        return
                    icon = Icon.objects.select_for_update().get(pk=icon_id, church=church)
                    locked_feast.icon = icon
                    locked_feast.save(update_fields=["icon"])
                logger.info(
                    "Matched icon %s (confidence: %s) to feast %s (%s)", icon_id, match_confidence, feast_id, prompt
                )
            except Icon.DoesNotExist:
                logger.warning("Matched icon ID %s does not exist.", icon_id)
        else:
            logger.info(
                "Icon match found for feast %s but tier/confidence %s/%s is not assignable",
                feast_id,
                match_tier,
                match_confidence,
            )

    except Exception as e:
        logger.error(f"Error matching icon to feast {feast_id}: {e}", exc_info=True)
        # Don't retry on general exceptions, just log the error
        # Feasts can exist without icons, so this is not a critical failure
