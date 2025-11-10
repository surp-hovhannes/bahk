"""Tasks for icon matching."""
import json
import logging
import re

from celery import shared_task
from django.conf import settings

from hub.constants import ICON_MATCH_CONFIDENCE_THRESHOLD
from hub.models import Feast
from icons.models import Icon

logger = logging.getLogger(__name__)


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
    
    # Create LLM prompt
    system_prompt = """
You match a user's natural-language request to the most relevant icons.

INPUT:
- A list of icons. Each icon has: ID, Title, and Tags.
- A user request.
- A maximum number of results (N).

OUTPUT FORMAT (STRICT):
Return a JSON array of match objects. Each object must follow this exact format:

[
  {
    "id": 3,
    "confidence": "high"
  },
  {
    "id": 12,
    "confidence": "medium"
  }
]

Rules for Output:
- Do NOT include any text outside the JSON.
- Do NOT include extra keys or commentary.
- If no icons are meaningfully relevant, return: []
- Return at most N matches.

CONFIDENCE SCORING:
Assign confidence based on clarity of match:
- "high": The icon's title or tags clearly and directly match the request, with minimal ambiguity.
- "medium": The match is plausible and relevant, but not exact.
- "low": Only return "low" confidence if it is still clearly related; otherwise do not return it at all.

RELEVANCE RULES:
- Prefer icons whose Title strongly matches the user request.
- Next, consider strong Tag matches.
- Ignore weak or tangential keyword overlap.
- Only return IDs that appear in the provided list.
- NEVER guess or invent icons.

TIEBREAKERS:
If multiple icons seem similar in relevance:
1) Exact title match or near-synonym wins.
2) More specific tags beat general tags.
3) Well-known canonical association beats broad thematic similarity.

If unsure whether an icon is relevant:
DO NOT RETURN IT.
"""
    
    allowed_ids = [icon.id for icon in icons]
    
    user_message = f"""User request: "{prompt}"
Allowed icon IDs: {allowed_ids}

Available icons (ID, Title, Tags):
{chr(10).join(icon_descriptions)}

Return up to {max_results} most relevant icons as a JSON array of objects with "id" and "confidence" fields."""
    
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
        
        # Try models in order of preference, falling back if one fails
        models_to_try = ['gpt-5-mini', 'gpt-4.1-nano', 'gpt-4o-mini', 'gpt-4.1-mini']
        response = None
        last_error = None
        
        for model in models_to_try:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_completion_tokens=500
                )
                logger.info(f"Successfully used model: {model}")
                break
            except APIError as api_error:
                last_error = api_error
                error_body = getattr(api_error, 'body', {}) or {}
                error_code = error_body.get('error', {}).get('code', '')
                error_message = str(api_error)
                
                # Check if it's a model access error (403 or model_not_found)
                if api_error.status_code == 403 or 'model_not_found' in error_code or 'does not have access' in error_message:
                    logger.warning(f"Model {model} not available (status: {api_error.status_code}), trying next model...")
                    continue
                # Check if it's a temperature unsupported error
                elif api_error.status_code == 400 and ('unsupported_value' in error_code or 'temperature' in error_message.lower()):
                    logger.warning(f"Model {model} doesn't support custom temperature, retrying without temperature parameter...")
                    try:
                        # Retry without temperature parameter (uses default)
                        response = client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_message},
                            ],
                            max_completion_tokens=500,
                        )
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
                        response = client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_message},
                            ],
                            max_completion_tokens=500,
                        )
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
        
        # Parse the response
        llm_response = response.choices[0].message.content.strip()
        
        # Try to parse as JSON array
        try:
            parsed_response = json.loads(llm_response)
            if not isinstance(parsed_response, list):
                parsed_response = [parsed_response]
            
            # Handle new format: array of objects with 'id' and 'confidence'
            matched_results = []
            valid_confidence_levels = {'high', 'medium', 'low'}
            for item in parsed_response:
                if isinstance(item, dict):
                    # New format: {"id": 3, "confidence": "high"}
                    if 'id' in item:
                        confidence = item.get('confidence', 'medium')
                        # Validate confidence level
                        if confidence not in valid_confidence_levels:
                            logger.warning(f"Invalid confidence '{confidence}', defaulting to 'medium'")
                            confidence = 'medium'
                        matched_results.append({
                            'id': item['id'],
                            'confidence': confidence
                        })
                elif isinstance(item, (int, str)):
                    # Backward compatibility: just an ID
                    matched_results.append({
                        'id': int(item),
                        'confidence': 'medium'  # Default if not provided
                    })
            
            # Limit to max_results
            matched_results = matched_results[:max_results]
            return matched_results
            
        except json.JSONDecodeError:
            # Fallback: extract numbers from response
            matched_ids = [int(x) for x in re.findall(r'\d+', llm_response)]
            return [
                {'id': icon_id, 'confidence': 'medium'}
                for icon_id in matched_ids[:max_results]
            ]
    
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


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def match_icon_to_feast_task(self, feast_id: int):
    """
    Match an icon to a feast using AI-powered icon matching.
    
    Args:
        feast_id: ID of the Feast to match an icon for
    """
    try:
        feast = Feast.objects.select_related('day', 'day__church').get(pk=feast_id)
    except Feast.DoesNotExist:
        logger.error("Feast with id %s not found.", feast_id)
        return
    
    # Skip if icon is already set
    if feast.icon:
        logger.info("Feast %s already has an icon assigned, skipping.", feast_id)
        return
    
    # Get the church from the feast's day
    church = feast.day.church
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
                icon = Icon.objects.get(pk=icon_id)
                feast.icon = icon
                feast.save(update_fields=['icon'])
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

