"""Tasks for icon matching; discovery is shared with the public API."""

import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction

from hub.models import Feast
from hub.services.icon_clef_judge import judge_icons, thumbnail_image_loader
from hub.services.icon_matching import IconMatchRequest
from hub.services.icon_match_router import match_icons
from hub.services.icon_taxonomy_matching import assignment_current
from icons.models import Icon

logger = logging.getLogger(__name__)

FEAST_ICON_ENGINES = ("llm", "clef", "clef_shadow")


def feast_icon_engine():
    """The configured FEAST_ICON_MATCH_ENGINE; an unknown value falls back to the LLM matcher."""
    engine = str(getattr(settings, "FEAST_ICON_MATCH_ENGINE", "llm")).strip().lower()
    if engine not in FEAST_ICON_ENGINES:
        logger.warning("Unknown FEAST_ICON_MATCH_ENGINE %r; using llm.", engine)
        return "llm"
    return engine


def _judge_with_clef(icons, feast_name):
    """Clef decides, seeing each icon's cached thumbnail as it did when calibrated."""
    image_for = thumbnail_image_loader({icon.id: icon.cached_thumbnail_url for icon in icons})
    return judge_icons(icons, feast_name, image_for=image_for, max_results=1)


def _written_icon(outcome):
    """The icon this task would write from ``outcome``, or None."""
    if outcome.status == "complete" and outcome.matches and outcome.matches[0].get("auto_assignable"):
        return outcome.matches[0]["id"]
    return None


def _log_clef_shadow(feast_id, icons, feast_name, llm_outcome):
    """Run Clef beside the LLM matcher and log what each would write. Clef writes nothing."""
    try:
        clef = _judge_with_clef(icons, feast_name)
    except Exception:
        logger.warning("Clef shadow failed for feast %s.", feast_id, exc_info=True)
        return
    llm_icon, clef_icon = _written_icon(llm_outcome), _written_icon(clef)
    logger.info(
        "Clef shadow for feast %s (%s): llm writes %s, clef would write %s (%s, top %s, %s)%s",
        feast_id,
        feast_name,
        llm_icon,
        clef_icon,
        clef.status,
        clef.matches[0]["id"] if clef.matches else None,
        ", ".join(clef.diagnostics),
        "" if llm_icon == clef_icon else " [differs]",
    )


def _match_icons_with_llm(icons, request, max_results=3):
    """Legacy list interface; production consumers use the structured outcome."""
    if not isinstance(request, IconMatchRequest):
        request = IconMatchRequest(
            kind="feast", primary_text=str(request), auto_assign_policy="feast_strict", max_results=max_results
        )
    from dataclasses import replace

    return match_icons(icons, replace(request, max_results=max_results)).matches


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def match_icon_to_feast_task(self, feast_id: int, commemoration=None):
    """
    Match an icon to a feast using AI-powered icon matching.

    FEAST_ICON_MATCH_ENGINE chooses who decides: the LLM matcher (``llm``), the Clef
    judge (``clef``), or the LLM matcher with Clef logged beside it (``clef_shadow``).
    Either way only a complete outcome's first, auto-assignable match is written.

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
        engine = feast_icon_engine()
        if engine == "clef":
            outcome = _judge_with_clef(icons, prompt)
        else:
            outcome = match_icons(
                icons,
                IconMatchRequest(
                    kind="feast",
                    primary_text=prompt,
                    auto_assign_policy="feast_strict",
                    max_results=1,
                ),
                church_id=church.id,
                commemoration=commemoration,
            )
            if engine == "clef_shadow":
                _log_clef_shadow(feast_id, icons, prompt, outcome)

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
                    if not assignment_current(icon, first_match):
                        return
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
