"""Tasks for icon matching."""
import json
import logging
import re
import time

from celery import shared_task
from django.conf import settings
from django.db import transaction

from hub.models import Feast
from hub.services.icon_matching import (
    IconMatchRequest,
    generate_icon_candidates,
    validate_and_rank_decision,
)
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
                'decision': {
                    'type': 'object',
                    'properties': {
                        'id': {'type': ['integer', 'null']},
                        'match_tier': {
                            'type': 'string',
                            'enum': ['direct_exact', 'related_specific', 'thematic', 'no_match'],
                        },
                        'confidence': {
                            'type': 'string',
                            'enum': ['high', 'medium', 'low', 'none'],
                        },
                        'matched_concepts': {'type': 'array', 'items': {'type': 'string'}},
                        'evidence_refs': {'type': 'array', 'items': {'type': 'string'}},
                        'rationale_code': {
                            'type': 'string',
                            'enum': [
                                'explicit_subject',
                                'explicit_event',
                                'full_composite',
                                'specific_related_subject',
                                'specific_related_event',
                                'defensible_theme',
                                'no_eligible_candidate',
                            ],
                        },
                    },
                    'required': [
                        'id',
                        'match_tier',
                        'confidence',
                        'matched_concepts',
                        'evidence_refs',
                        'rationale_code',
                    ],
                    'additionalProperties': False,
                },
            },
            'required': ['decision'],
            'additionalProperties': False,
        },
    },
}

ICON_MATCH_MODELS = ('gpt-4.1-mini', 'gpt-4.1-nano', 'gpt-4o-mini')
RATE_LIMIT_RETRIES = 3
MAX_RETRY_DELAY_SECONDS = 8.0


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
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    match = re.search(
        r'(?:retry|try again)(?:[^0-9]+)([0-9]+(?:\.[0-9]+)?)\s*s',
        error_message,
        re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def _call_openai_with_fallback(client, *, messages, max_tokens, response_format):
    """Call configured models with bounded Retry-After-aware 429 retries."""
    from openai import APIError

    for model in ICON_MATCH_MODELS:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                return openai_chat_completion(
                    client,
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )
            except APIError as api_error:
                error_code, error_message = _get_openai_error_details(api_error)
                if _is_openai_rate_limit_error(api_error, error_code, error_message):
                    if attempt == RATE_LIMIT_RETRIES:
                        logger.warning('Rate-limit retries exhausted for model %s', model)
                        break
                    retry_after = _extract_openai_retry_delay(api_error, error_message)
                    delay = min(
                        MAX_RETRY_DELAY_SECONDS,
                        max(2**attempt, retry_after or 0),
                    )
                    logger.warning(
                        'Rate limited by OpenAI; retrying %s in %.2fs (%s/%s)',
                        model,
                        delay,
                        attempt + 1,
                        RATE_LIMIT_RETRIES,
                    )
                    time.sleep(delay)
                    continue
                if (
                    getattr(api_error, 'status_code', None) == 403
                    or 'model_not_found' in error_code
                    or 'does not have access' in error_message
                ):
                    break
                raise
    return None


def _coerce_match_request(request, max_results):
    if isinstance(request, IconMatchRequest):
        return IconMatchRequest(
            kind=request.kind,
            primary_text=request.primary_text,
            context_terms=tuple(request.context_terms),
            auto_assign_policy=request.auto_assign_policy,
            max_results=max_results,
        )
    return IconMatchRequest(
        kind='feast',
        primary_text=str(request),
        auto_assign_policy='feast_strict',
        max_results=max_results,
    )


def _match_icons_with_llm(icons, request, max_results=3):
    """Return strictly validated, Python-ranked icon match decisions."""
    request = _coerce_match_request(request, max_results)
    candidates = generate_icon_candidates(icons, request)
    if not candidates:
        return []
    system_prompt = """
You adjudicate precomputed devotional-icon candidates. Return one decision.

The relationship hierarchy is strict: direct_exact/high outranks
related_specific/medium, which outranks thematic/low, then no_match/none.
Only approve evidence supplied with a candidate. Never invent an ID, concept,
evidence reference, tier, or relationship. Equivalent direct duplicates are
not ambiguity; approve the direct concept and Python will tie-break them.
Event-exact evidence takes precedence over same-subject portrait fallback.
Reject incidental shared names, broad categories, and thematic proximity.
Return no_match when no supplied candidate is defensible.
"""
    try:
        from openai import OpenAI

        if not settings.OPENAI_API_KEY:
            logger.warning('OPENAI_API_KEY not configured; returning no icon match')
            return []

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        candidate_payload = [
            {
                'id': candidate.icon_id,
                'title': candidate.title,
                'tags': list(candidate.tags),
                'evidence_tier': candidate.match_tier,
                'matched_concepts': list(candidate.matched_concepts),
                'requested_concepts': list(candidate.requested_concepts),
                'coverage': candidate.coverage,
                'specificity': candidate.specificity,
                'complete_coverage': candidate.complete_coverage,
                'evidence_refs': list(candidate.evidence_refs),
            }
            for candidate in candidates
        ]
        user_message = json.dumps(
            {
                'request': {
                    'kind': request.kind,
                    'primary_text': request.primary_text,
                    'context_terms': list(request.context_terms),
                },
                'candidates': candidate_payload,
            },
            sort_keys=True,
        )
        response = _call_openai_with_fallback(
            client,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            max_tokens=500,
            response_format=ICON_MATCH_RESPONSE_FORMAT,
        )

        if response is None:
            logger.warning('No configured OpenAI model was available; returning no icon match')
            return []

        content = response.choices[0].message.content
        payload = json.loads(content)
        results = validate_and_rank_decision(payload, candidates, max_results=max_results)
        if not results and payload.get('decision', {}).get('match_tier') != 'no_match':
            logger.warning('Rejected invalid icon match response')
        return results
    except Exception as error:
        logger.error('Icon matching provider failed closed: %s', error, exc_info=True)
        return []



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
        matched_results = _match_icons_with_llm(
            icons,
            IconMatchRequest(
                kind='feast',
                primary_text=prompt,
                auto_assign_policy='feast_strict',
                max_results=1,
            ),
            max_results=1,
        )
        
        if not matched_results:
            logger.info("No icon matches found for feast %s (%s).", feast_id, prompt)
            return
        
        first_match = matched_results[0]
        match_confidence = first_match.get('confidence')
        match_tier = first_match.get('match_tier')

        if match_tier == 'direct_exact' and match_confidence == 'high':
            # Found a high confidence match, save it
            icon_id = first_match['id']
            try:
                icon = Icon.objects.get(pk=icon_id, church=church)
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
                "Icon match found for feast %s but tier/confidence %s/%s is not assignable",
                feast_id,
                match_tier,
                match_confidence,
            )
    
    except Exception as e:
        logger.error(f"Error matching icon to feast {feast_id}: {e}", exc_info=True)
        # Don't retry on general exceptions, just log the error
        # Feasts can exist without icons, so this is not a critical failure
