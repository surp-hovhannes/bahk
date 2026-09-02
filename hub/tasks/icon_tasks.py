"""Tasks for icon matching."""
import json
import logging
import re
import time

from celery import shared_task
from django.conf import settings
from django.db import transaction

from hub.constants import ICON_MATCH_CONFIDENCE_THRESHOLD
from hub.models import Feast
from hub.services.llm_requests import openai_chat_completion
from icons.models import Icon

logger = logging.getLogger(__name__)

ICON_MATCH_RESPONSE_FORMAT = {
    'type': 'json_schema',
    'json_schema': {
        'name': 'icon_matches',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'matches': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'confidence': {
                                'type': 'string',
                                'enum': ['high', 'medium', 'low'],
                            },
                        },
                        'required': ['id', 'confidence'],
                        'additionalProperties': False,
                    },
                    'maxItems': 3,
                },
            },
            'required': ['matches'],
            'additionalProperties': False,
        },
    },
}

ICON_DIRECTNESS_RESPONSE_FORMAT = {
    'type': 'json_schema',
    'json_schema': {
        'name': 'icon_directness',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'is_direct_match': {'type': 'boolean'},
            },
            'required': ['is_direct_match'],
            'additionalProperties': False,
        },
    },
}


def _get_openai_error_details(api_error):
    error_body = getattr(api_error, 'body', {}) or {}
    error_payload = error_body.get('error', error_body) if isinstance(error_body, dict) else {}
    error_code = ''
    error_message = str(api_error)
    if isinstance(error_payload, dict):
        error_code = error_payload.get('code') or ''
        error_message = error_payload.get('message') or error_message
    return str(error_code), str(error_message)


def _is_openai_rate_limit_error(api_error, error_code, error_message):
    status_code = getattr(api_error, 'status_code', None)
    return (
        status_code == 429
        or 'rate_limit_exceeded' in error_code
        or 'rate_limit' in error_code
        or 'rate limit' in error_message.lower()
    )


def _extract_openai_retry_delay(api_error, error_message):
    response = getattr(api_error, 'response', None)
    headers = getattr(response, 'headers', {}) or {}
    retry_after = headers.get('retry-after') or headers.get('Retry-After')
    if retry_after:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            pass

    match = re.search(r'please try again in ([0-9]+(?:\.[0-9]+)?)s', error_message, re.IGNORECASE)
    if match:
        return float(match.group(1))

    return None


def _simple_match_icons(icons, prompt, max_results):
    """Simple fallback matching based on title and tag keywords."""
    prompt_lower = prompt.lower()
    scored_icons = []
    
    for icon in icons:
        score = 0
        title_lower = icon.title.lower()
        tags_lower = [tag.name.lower() for tag in icon.tags.all()]
        
        # Check title matches
        if prompt_lower in title_lower or title_lower in prompt_lower:
            score += 10
        
        # Check tag matches
        for tag in tags_lower:
            if tag in prompt_lower or prompt_lower in tag:
                score += 5
        
        if score > 0:
            scored_icons.append((score, icon.id))
    
    # Sort by score descending and return IDs
    scored_icons.sort(reverse=True, key=lambda x: x[0])
    return [icon_id for _, icon_id in scored_icons[:max_results]]


def _match_icons_with_llm(icons, prompt, max_results=3):
    """
    Match icons using LLM-based matching.
    
    Returns a list of dicts with 'id' and 'confidence' keys.
    """
    # Format icon data for LLM
    icon_descriptions = []
    for icon in icons:
        tags = ', '.join([tag.name for tag in icon.tags.all()])
        description = f"Icon ID: {icon.id}, Title: {icon.title}, Tags: {tags}"
        icon_descriptions.append(description)
    
    system_prompt = """
You select the best devotional icon for a church-calendar commemoration.

Each candidate has an ID, title, and editorial tags. Return only candidates
that directly depict or name a person, group, or event explicitly commemorated
by the request. Titles and tags may use conventional saint-name variants,
abbreviations, transliterations, or synonymous event names.

RELEVANCE:
- A high-confidence match is an explicit, direct correspondence.
- A medium-confidence match is a direct but less-specific correspondence.
- A low-confidence match must still be directly related; otherwise omit it.
- A bare liturgical period, fast, day number, or ordinal is not a commemoration.
  Return no match unless the request also names a concrete person or event.
- Do not infer a match from thematic proximity. For example, Eastertide does
  not by itself mean Resurrection, Pentecost, Ascension, or Palm Sunday.
- Shared broad categories alone are insufficient: "saint", "martyr", "king",
  "apostle", "fast", or a group size must not produce a match.
- Return no matches rather than a merely related icon.

COMPOSITE COMMEMORATIONS:
- When the request names multiple people, compare every candidate before
  ranking. Prefer a composition naming two or more requested subjects over an
  icon naming only one subject.
- Do not assign high confidence to a single-subject icon when a matching group
  composition is available.
- A single named person is acceptable only when no group composition is
  available and that person is a principal subject of the commemoration.

RANKING:
1. Exact title or tag match, including direct conventional variants.
2. A composition covering more explicitly named subjects.
3. A canonical icon for the explicitly named event or person.
4. More-specific evidence beats broad thematic overlap.

Only return IDs from the supplied candidates. Never invent an ID. Do not
repeat an ID.
"""
    
    allowed_ids = {icon.id for icon in icons}
    
    user_message = f"""Commemoration: "{prompt}"
Allowed icon IDs: {sorted(allowed_ids)}

Candidate icons (ID, Title, Tags):
{chr(10).join(icon_descriptions)}

Return up to {max_results} matches in the required schema."""
    
    try:
        # Check if OpenAI API key is configured
        from openai import OpenAI
        from openai import APIError
        
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not configured, falling back to simple tag matching")
            # Fallback to simple tag/title matching
            matched_ids = _simple_match_icons(icons, prompt, max_results)
            # Convert to expected format with default confidence
            return [
                {'id': icon_id, 'confidence': 'medium'}
                for icon_id in matched_ids
            ]
        
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Prefer the more accurate model because incorrect high-confidence
        # assignments are persisted; retain cheaper models as fallbacks.
        models_to_try = ['gpt-4.1-mini', 'gpt-4.1-nano', 'gpt-4o-mini']
        response = None
        last_error = None
        
        for model in models_to_try:
            try:
                response = openai_chat_completion(client, model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=500,
                response_format=ICON_MATCH_RESPONSE_FORMAT)
                logger.info(f"Successfully used model: {model}")
                break
            except APIError as api_error:
                last_error = api_error
                error_code, error_message = _get_openai_error_details(api_error)

                if _is_openai_rate_limit_error(api_error, error_code, error_message):
                    max_rate_limit_retries = 3
                    for attempt in range(1, max_rate_limit_retries + 1):
                        retry_delay = _extract_openai_retry_delay(api_error, error_message)
                        backoff_delay = 2 ** (attempt - 1)
                        delay = max(backoff_delay, retry_delay or 0)
                        logger.warning(
                            "Rate limited by OpenAI, retrying in %.2fs (attempt %s/%s)",
                            delay,
                            attempt,
                            max_rate_limit_retries,
                        )
                        time.sleep(delay)
                        try:
                            response = openai_chat_completion(client, model=model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_message},
                            ],
                            max_tokens=500,
                            response_format=ICON_MATCH_RESPONSE_FORMAT)
                            logger.info(f"Successfully used model: {model}")
                            break
                        except APIError as retry_api_error:
                            last_error = retry_api_error
                            error_code, error_message = _get_openai_error_details(retry_api_error)
                            if _is_openai_rate_limit_error(retry_api_error, error_code, error_message):
                                api_error = retry_api_error
                                continue
                            raise
                        except Exception as retry_error:
                            last_error = retry_error
                            raise

                    if response:
                        break

                    logger.warning("OpenAI rate limit retries exhausted, falling back to simple tag matching")
                    matched_ids = _simple_match_icons(icons, prompt, max_results)
                    return [
                        {'id': icon_id, 'confidence': 'medium'}
                        for icon_id in matched_ids
                    ]
                
                # Check if it's a model access error (403 or model_not_found)
                if api_error.status_code == 403 or 'model_not_found' in error_code or 'does not have access' in error_message:
                    logger.warning(f"Model {model} not available (status: {api_error.status_code}), trying next model...")
                    continue
                # Check if it's a temperature unsupported error
                elif api_error.status_code == 400 and ('unsupported_value' in error_code or 'temperature' in error_message.lower()):
                    logger.warning(f"Model {model} doesn't support custom temperature, retrying without temperature parameter...")
                    try:
                        # Retry without temperature parameter (uses default)
                        response = openai_chat_completion(client, model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        max_tokens=500,
                        response_format=ICON_MATCH_RESPONSE_FORMAT)
                        logger.info(f"Successfully used model: {model} (without temperature)")
                        break
                    except Exception as retry_error:
                        logger.warning(f"Retry without temperature also failed for {model}, trying next model...")
                        last_error = retry_error
                        continue
                else:
                    # For other API errors, re-raise immediately
                    raise
            except Exception as model_error:
                last_error = model_error
                error_str = str(model_error)
                # Check if it's a model access error
                if 'model_not_found' in error_str or 'does not have access' in error_str:
                    logger.warning(f"Model {model} not available, trying next model...")
                    continue
                # Check if it's a temperature error
                elif 'temperature' in error_str.lower() and 'unsupported' in error_str.lower():
                    logger.warning(f"Model {model} doesn't support custom temperature, retrying without temperature parameter...")
                    try:
                        # Retry without temperature parameter
                        response = openai_chat_completion(client, model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        max_tokens=500,
                        response_format=ICON_MATCH_RESPONSE_FORMAT)
                        logger.info(f"Successfully used model: {model} (without temperature)")
                        break
                    except Exception as retry_error:
                        logger.warning(f"Retry without temperature also failed for {model}, trying next model...")
                        last_error = retry_error
                        continue
                else:
                    # For other errors, re-raise immediately
                    raise
        
        if not response:
            raise last_error if last_error else Exception("No models available")
        
        llm_response = response.choices[0].message.content.strip()
        parsed_response = json.loads(llm_response)
        if not isinstance(parsed_response, dict):
            logger.warning("Skipping non-object structured response from LLM")
            return []

        matches = parsed_response.get('matches')
        if not isinstance(matches, list):
            logger.warning("Skipping structured response without a matches list")
            return []

        matched_results = []
        seen_ids = set()
        for item in matches:
            if not isinstance(item, dict):
                logger.warning("Skipping invalid match from LLM response: %r", item)
                continue

            try:
                icon_id = int(item['id'])
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping invalid icon ID from LLM response: %r", item)
                continue

            if icon_id not in allowed_ids:
                logger.warning("Skipping out-of-scope icon ID from LLM response: %s", icon_id)
                continue
            if icon_id in seen_ids:
                logger.warning("Skipping duplicate icon ID from LLM response: %s", icon_id)
                continue

            seen_ids.add(icon_id)
            confidence = item.get('confidence')
            if confidence not in {'high', 'medium', 'low'}:
                logger.warning("Skipping invalid confidence from LLM response: %r", confidence)
                continue
            matched_results.append({'id': icon_id, 'confidence': confidence})

        return matched_results[:max_results]
    
    except Exception as e:
        logger.error(f"Error in LLM icon matching: {e}", exc_info=True)
        # Fallback to simple matching if LLM fails
        try:
            matched_ids = _simple_match_icons(icons, prompt, max_results)
            # Convert to expected format with default confidence
            return [
                {'id': icon_id, 'confidence': 'medium'}
                for icon_id in matched_ids
            ]
        except Exception as fallback_error:
            logger.error(f"Fallback matching also failed: {fallback_error}")
            return []

_GENERIC_ICON_MATCH_TOKENS = frozenset({
    'and', 'apostle', 'apostles', 'companions', 'day', 'fast', 'feast',
    'group', 'holy', 'icon', 'king', 'martyr', 'martyrs', 'of', 'saint',
    'saints', 'the',
})


def _has_direct_metadata_evidence(icon, commemoration):
    """Return whether title or tags explicitly name the commemoration."""
    def meaningful_tokens(value):
        return {
            token
            for token in re.findall(r'[a-z0-9]+', value.lower())
            if len(token) >= 4 and token not in _GENERIC_ICON_MATCH_TOKENS
        }

    commemoration_tokens = meaningful_tokens(commemoration)
    icon_text = ' '.join([icon.title, *(tag.name for tag in icon.tags.all())])
    icon_tokens = meaningful_tokens(icon_text)
    if commemoration_tokens & icon_tokens:
        return True

    return any(
        len(left) >= 6
        and len(right) >= 6
        and (left.startswith(right) or right.startswith(left))
        for left in commemoration_tokens
        for right in icon_tokens
    )


def _is_direct_icon_match(icon, commemoration):
    """Return whether an icon directly depicts the named commemoration."""
    tags = ', '.join(tag.name for tag in icon.tags.all())
    system_prompt = """
You are the final safety check before a devotional icon is assigned to a
church-calendar commemoration. Decide whether this one icon directly depicts
or names the commemoration.

Accept conventional saint-name variants, abbreviations, transliterations, and
canonical equivalents. Accept a group composition when it depicts multiple
named subjects, even if its title omits one subject. Reject thematic,
calendar-season, role-only, and broad-category associations. In particular,
"martyr", "saint", "king", "apostle", a fast, or a group size alone is not a
direct match.

If uncertain, reject the assignment.
"""
    user_message = f"""Commemoration: "{commemoration}"
Icon title: "{icon.title}"
Icon tags: "{tags}"

Does this icon directly match the commemoration?"""

    try:
        from openai import OpenAI

        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not configured; rejecting icon assignment")
            return False

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        for model in ('gpt-4.1-mini', 'gpt-4.1-nano', 'gpt-4o-mini'):
            try:
                response = openai_chat_completion(
                    client,
                    model=model,
                    messages=[
                        {'role': 'system', 'content': system_prompt},
                        {'role': 'user', 'content': user_message},
                    ],
                    max_tokens=50,
                    response_format=ICON_DIRECTNESS_RESPONSE_FORMAT,
                )
                payload = json.loads(response.choices[0].message.content)
                is_direct_match = payload.get('is_direct_match')
                if is_direct_match is True:
                    return True
                if is_direct_match is False and _has_direct_metadata_evidence(
                    icon,
                    commemoration,
                ):
                    logger.info(
                        "Accepted icon %s using direct title or tag evidence.",
                        icon.id,
                    )
                    return True
                if is_direct_match is False:
                    return False
                logger.warning("Invalid directness response from model %s", model)
            except Exception as error:
                logger.warning("Directness verification failed with %s: %s", model, error)

        return False
    except Exception as error:
        logger.error("Could not verify icon directness: %s", error, exc_info=True)
        return False



@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def match_icon_to_feast_task(self, feast_id: int):
    """
    Match an icon to a feast using AI-powered icon matching.
    
    Args:
        feast_id: ID of the Feast to match an icon for
    """
    try:
        feast = Feast.objects.select_related('church').get(pk=feast_id)
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
    icons = list(Icon.objects.filter(church=church).select_related('church').prefetch_related('tags'))
    
    if not icons:
        logger.info("No icons found for church %s, cannot match icon for feast %s.", church.id, feast_id)
        return
    
    # Use feast name as the prompt
    prompt = feast.name
    
    try:
        # Perform icon matching
        matched_results = _match_icons_with_llm(icons, prompt, max_results=1)
        
        if not matched_results:
            logger.info("No icon matches found for feast %s (%s).", feast_id, prompt)
            return
        
        # Check if we have a high confidence match
        first_match = matched_results[0]
        match_confidence = first_match.get('confidence', 'medium')
        
        # Compare confidence levels: 'high' > 'medium' > 'low'
        confidence_order = {'high': 3, 'medium': 2, 'low': 1}
        threshold_order = confidence_order.get(ICON_MATCH_CONFIDENCE_THRESHOLD, 2)
        match_order = confidence_order.get(match_confidence, 0)
        
        if match_order >= threshold_order:
            # Found a high confidence match, save it
            icon_id = first_match['id']
            try:
                icon = Icon.objects.get(pk=icon_id, church=church)
                if not _is_direct_icon_match(icon, prompt):
                    logger.info(
                        "Rejected icon %s for feast %s after directness verification.",
                        icon_id,
                        feast_id,
                    )
                    return
                with transaction.atomic():
                    locked_feast = Feast.objects.select_for_update().get(pk=feast_id)
                    if locked_feast.icon_id is not None:
                        logger.info(
                            "Feast %s received an icon while matching, skipping.",
                            feast_id,
                        )
                        return
                    locked_feast.icon = icon
                    locked_feast.save(update_fields=['icon'])
                logger.info(
                    "Matched icon %s (confidence: %s) to feast %s (%s)",
                    icon_id, match_confidence, feast_id, prompt
                )
            except Icon.DoesNotExist:
                logger.warning("Matched icon ID %s does not exist.", icon_id)
        else:
            logger.info(
                "Icon match found for feast %s but confidence %s is below threshold %s",
                feast_id, match_confidence, ICON_MATCH_CONFIDENCE_THRESHOLD
            )
    
    except Exception as e:
        logger.error(f"Error matching icon to feast {feast_id}: {e}", exc_info=True)
        # Don't retry on general exceptions, just log the error
        # Feasts can exist without icons, so this is not a critical failure
