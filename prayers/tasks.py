"""Celery tasks for the prayers app."""
import logging
from datetime import date, timedelta
from collections import defaultdict

from better_profanity import profanity
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, Q
from django.utils import timezone

from events.models import Event, EventType, UserActivityFeed, UserMilestone
from hub.services.llm_service import get_llm_service
from hub.models import LLMPrompt
from prayers.models import PrayerRequest, PrayerRequestPrayerLog

logger = logging.getLogger(__name__)

# Initialize profanity filter
profanity.load_censor_words()


def _get_moderation_prompt_and_service(prayer_request):
    """
    Get the moderation prompt and model info for prayer request moderation.

    First tries to fetch from LLMPrompt model (applies_to='prayer_requests', active=True).
    Falls back to hard-coded prompt if no active prompt exists in database.

    Returns:
        tuple: (model_name, prompt_text)
            model_name: str - The model identifier to use for the API call
            prompt_text: str - The formatted prompt with prayer request details
    """
    try:
        # Try to get active prompt from database
        llm_prompt = LLMPrompt.objects.get(applies_to='prayer_requests', active=True)

        # Format the prompt with prayer request data
        # The database prompt should use {title} and {description} placeholders
        prompt_text = llm_prompt.prompt.format(
            title=prayer_request.title,
            description=prayer_request.description
        )

        logger.info(f"Using LLMPrompt (id={llm_prompt.id}, model={llm_prompt.model}) for prayer request moderation")
        return llm_prompt.model, prompt_text

    except LLMPrompt.DoesNotExist:
        # Fallback to hard-coded prompt
        logger.warning("No active LLMPrompt found for prayer_requests, using hard-coded fallback")
        model_name = 'claude-sonnet-4-5-20250929'

        # Hard-coded fallback prompt (same as our enhanced prompt)
        prompt_text = f"""You are evaluating a prayer request submitted to a Christian community app. Assess the request for appropriateness and genuine prayer needs.

**Request Details:**
- Title: {prayer_request.title}
- Description: {prayer_request.description}

**Evaluation Criteria:**

1. **Genuine Prayer Need**: Does this represent a sincere request for prayer support? Acceptable topics include health concerns, relationship struggles, spiritual growth, grief, anxiety, financial hardship, guidance, protection, and similar life challenges.

2. **Appropriate Content**: Is the content respectful and suitable for a Christian community? Reject requests containing: explicit sexual content, graphic violence, hate speech, harassment of individuals, or content that mocks faith.

3. **Not Spam/Promotional**: Is this a real prayer need rather than advertising, fundraising solicitation, political campaigning, or repetitive spam?

4. **Coherence**: Is the request understandable and written in good faith? Reject incoherent gibberish, test submissions, or obvious jokes.

5. **Safety**: Does this request avoid dangerous content such as self-harm intentions, threats to others, requests for harmful activities, or sharing of private information about others?

**Special Considerations:**
- Anonymous requests should be evaluated with the same standards
- Requests may include personal struggles or vulnerable situations - be compassionate while maintaining community safety
- Prayer requests often include emotional language - distinguish between authentic expression and inappropriate content
- Requests may be brief (title only) or detailed - both can be valid if they meet the criteria

**Severity Levels:**
- **low**: Clear, appropriate prayer request with no concerns
- **medium**: Minor concerns but acceptable (e.g., slightly vague, emotional language that might be misinterpreted)
- **high**: Significant concerns requiring human review (e.g., borderline inappropriate, unclear intent, potential safety issues)
- **critical**: Immediate safety concerns (e.g., self-harm, threats, severe harassment)

**Response Format (JSON only):**
{{
  "approved": true/false,
  "reason": "Brief explanation of decision (1-2 sentences)",
  "concerns": ["list", "specific", "issues"],
  "severity": "low|medium|high|critical",
  "requires_human_review": false,
  "suggested_action": "approve|reject|flag_for_review|escalate"
}}

Note: The concerns array should be empty if fully approved with no issues.

**Examples of APPROVED requests (low severity):**
- Health issues, medical procedures, recovery
- Relationship challenges, family conflicts
- Anxiety, depression, mental health struggles
- Job loss, financial difficulties
- Spiritual growth, breaking bad habits
- Grief, loss of loved ones
- Guidance for major life decisions
- Protection during travel or difficult situations

**Examples of MEDIUM severity (may approve with flag):**
- Very emotional or distressing content that is still appropriate
- Vague requests that might need clarification
- Requests with minor grammatical issues but clear intent

**Examples of HIGH severity (flag for review):**
- Borderline inappropriate language
- Unclear whether request is genuine
- Mentions sensitive topics that need careful review

**Examples of CRITICAL severity (escalate immediately):**
- Expressions of intent to self-harm
- Threats to harm others
- Severe harassment or hate speech

**Examples of REJECTED requests:**
- "Buy my product! Visit mysite.com" (spam)
- "asdfasdf test test 123" (incoherent)
- Explicit sexual content or graphic descriptions (inappropriate)
- Political campaign messages (promotional)
"""

        return model_name, prompt_text


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def moderate_prayer_request_task(self, prayer_request_id):
    """
    Moderate a prayer request using profanity filter and LLM.

    This task:
    1. Runs profanity filter check
    2. Runs LLM moderation check
    3. Updates prayer request status based on results
    4. Sends email to admin if rejected

    Args:
        prayer_request_id: ID of the PrayerRequest to moderate
    """
    try:
        prayer_request = PrayerRequest.objects.get(id=prayer_request_id)

        # Check if already moderated
        if prayer_request.reviewed:
            logger.info(f"Prayer request {prayer_request_id} already moderated, skipping")
            return {'success': True, 'already_moderated': True}

        # Step 1: Profanity filter check
        title_has_profanity = profanity.contains_profanity(prayer_request.title)
        description_has_profanity = profanity.contains_profanity(prayer_request.description)

        if title_has_profanity or description_has_profanity:
            # Automatic rejection due to profanity
            prayer_request.reviewed = True
            prayer_request.status = 'rejected'
            prayer_request.moderation_result = {
                'profanity_check': {
                    'passed': False,
                    'title_contains_profanity': title_has_profanity,
                    'description_contains_profanity': description_has_profanity,
                },
                'llm_check': None,
                'reason': 'Content contains inappropriate language'
            }
            prayer_request.moderated_at = timezone.now()
            prayer_request.save()

            # Send email to admin
            _send_moderation_alert_email(prayer_request, 'profanity_detected')

            logger.info(f"Prayer request {prayer_request_id} rejected due to profanity")
            return {
                'success': True,
                'status': 'rejected',
                'reason': 'profanity'
            }

        # Step 2: LLM moderation check
        try:
            # Get prompt and model (from database or fallback to hard-coded)
            model_name, moderation_prompt = _get_moderation_prompt_and_service(prayer_request)

            # Call LLM with low temperature for consistent moderation
            from anthropic import Anthropic

            client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=model_name,
                max_tokens=500,
                temperature=0.1,
                messages=[{
                    'role': 'user',
                    'content': moderation_prompt
                }]
            )

            # Parse response
            import json
            response_text = response.content[0].text

            # Extract JSON from response (handle markdown code blocks)
            if '```json' in response_text:
                response_text = response_text.split('```json')[1].split('```')[0].strip()
            elif '```' in response_text:
                response_text = response_text.split('```')[1].split('```')[0].strip()

            llm_result = json.loads(response_text)

            # Update prayer request based on LLM result
            prayer_request.reviewed = True
            prayer_request.moderated_at = timezone.now()

            # Extract severity and review flags
            severity = llm_result.get('severity', 'low')
            requires_review = llm_result.get('requires_human_review', False)
            suggested_action = llm_result.get('suggested_action', 'approve' if llm_result.get('approved') else 'reject')

            # Store moderation metadata
            prayer_request.moderation_severity = severity
            prayer_request.moderation_result = {
                'profanity_check': {'passed': True},
                'llm_check': llm_result,
                'reason': llm_result.get('reason', 'Processed by automated moderation')
            }

            # Handle critical severity - always escalate
            if severity == 'critical':
                prayer_request.status = 'rejected'
                prayer_request.requires_human_review = True
                prayer_request.save()

                # Send urgent email to admin
                _send_moderation_alert_email(prayer_request, 'critical_safety_concern')

                logger.warning(
                    f"CRITICAL: Prayer request {prayer_request_id} flagged for safety concerns: "
                    f"{llm_result.get('reason')}"
                )

            # Handle high severity - flag for review regardless of approval
            elif severity == 'high' or requires_review:
                prayer_request.status = 'pending_moderation'
                prayer_request.requires_human_review = True
                prayer_request.save()

                # Send email to admin for manual review
                _send_moderation_alert_email(prayer_request, 'requires_review')

                logger.info(
                    f"Prayer request {prayer_request_id} flagged for human review (severity: {severity}): "
                    f"{llm_result.get('reason')}"
                )

            # Handle approved requests
            elif llm_result.get('approved', False):
                prayer_request.status = 'approved'
                prayer_request.requires_human_review = False

                # Save immediately so milestone check sees the updated status
                prayer_request.save()

                # Create event for approved prayer request
                Event.create_event(
                    event_type_code=EventType.PRAYER_REQUEST_CREATED,
                    user=prayer_request.requester,
                    target=prayer_request,
                    title=f'Prayer request created: {prayer_request.title}',
                    data={
                        'prayer_request_id': prayer_request.id,
                        'is_anonymous': prayer_request.is_anonymous,
                    }
                )

                # Check for first prayer request milestone
                if prayer_request.requester.prayer_requests.filter(
                    status='approved'
                ).count() == 1:
                    UserMilestone.create_milestone(
                        user=prayer_request.requester,
                        milestone_type='first_prayer_request_created',
                        related_object=prayer_request,
                        data={
                            'prayer_request_id': prayer_request.id,
                            'title': prayer_request.title,
                        }
                    )

                # Automatically accept own prayer request
                from prayers.models import PrayerRequestAcceptance
                PrayerRequestAcceptance.objects.get_or_create(
                    prayer_request=prayer_request,
                    user=prayer_request.requester,
                    defaults={'counts_for_milestones': False}
                )

                logger.info(
                    f"Prayer request {prayer_request_id} approved by LLM (severity: {severity})"
                )

            # Handle rejected requests
            else:
                prayer_request.status = 'rejected'
                prayer_request.requires_human_review = False
                prayer_request.save()

                # Send email to admin for awareness
                _send_moderation_alert_email(prayer_request, 'llm_rejected')

                logger.info(
                    f"Prayer request {prayer_request_id} rejected by LLM (severity: {severity}): "
                    f"{llm_result.get('reason')}"
                )

            prayer_request.save()

            return {
                'success': True,
                'status': prayer_request.status,
                'llm_result': llm_result
            }

        except Exception as llm_error:
            logger.error(f"LLM moderation error for prayer request {prayer_request_id}: {llm_error}")
            # If LLM fails, mark as pending and send email to admin
            prayer_request.moderation_result = {
                'profanity_check': {'passed': True},
                'llm_check': {'error': str(llm_error)},
                'reason': 'LLM moderation failed, requires manual review'
            }
            prayer_request.save()

            # Send email to admin for manual review
            _send_moderation_alert_email(prayer_request, 'llm_error')

            # Don't retry on LLM errors
            return {
                'success': False,
                'error': str(llm_error),
                'requires_manual_review': True
            }

    except PrayerRequest.DoesNotExist:
        logger.error(f"Prayer request {prayer_request_id} not found")
        return {'success': False, 'error': 'Prayer request not found'}

    except Exception as exc:
        logger.error(f"Error moderating prayer request {prayer_request_id}: {exc}")
        raise self.retry(exc=exc)


def _send_moderation_alert_email(prayer_request, alert_type):
    """Send email to admin about prayer request needing review."""
    subject_map = {
        'profanity_detected': 'Prayer Request Rejected - Profanity Detected',
        'llm_rejected': 'Prayer Request Rejected - Manual Review Needed',
        'llm_error': 'Prayer Request Moderation Error - Manual Review Required',
        'requires_review': 'Prayer Request Flagged for Human Review',
        'critical_safety_concern': 'Prayer Request Safety Concern',
    }

    subject = subject_map.get(alert_type, 'Prayer Request Needs Review')

    # Add severity indicator to subject for critical/high severity
    severity = prayer_request.moderation_severity
    if severity == 'critical':
        subject = f"🚨 CRITICAL: {subject}"
    elif severity == 'high':
        subject = f"⚠️  HIGH PRIORITY: {subject}"

    message = f"""
A prayer request has been flagged during moderation and requires your attention.

Prayer Request ID: {prayer_request.id}
Title: {prayer_request.title}
Description: {prayer_request.description}
Requester: {prayer_request.requester.email}
Anonymous: {'Yes' if prayer_request.is_anonymous else 'No'}
Created: {prayer_request.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}

SEVERITY: {severity.upper() if severity else 'Unknown'}
Requires Human Review: {'Yes' if prayer_request.requires_human_review else 'No'}
Status: {prayer_request.status}

Moderation Result:
{prayer_request.moderation_result}

Please review this prayer request in the Django admin panel.
Admin URL: {settings.SITE_URL}/admin/prayers/prayerrequest/{prayer_request.id}/change/
"""

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=['fastandprayhelp@gmail.com'],
            fail_silently=False,
        )
        logger.info(f"Moderation alert email sent for prayer request {prayer_request.id}")
    except Exception as e:
        logger.error(f"Failed to send moderation alert email for prayer request {prayer_request.id}: {e}")


@shared_task
def check_expired_prayer_requests_task():
    """
    Check for expired prayer requests and mark them as completed.
    Creates completion activity feed items for requesters.

    This task should run daily at 11:00 PM.
    """
    now = timezone.now()

    # Find prayer requests that have expired but are still approved
    expired_requests = PrayerRequest.objects.filter(
        status='approved',
        expiration_date__lte=now
    )

    count = 0
    for prayer_request in expired_requests:
        # Mark as completed
        prayer_request.mark_completed()

        # Create completion event
        Event.create_event(
            event_type_code=EventType.PRAYER_REQUEST_COMPLETED,
            user=prayer_request.requester,
            target=prayer_request,
            title=f'Prayer request completed: {prayer_request.title}',
            data={
                'prayer_request_id': prayer_request.id,
                'acceptance_count': prayer_request.get_acceptance_count(),
                'prayer_log_count': prayer_request.get_prayer_log_count(),
            }
        )

        # Create activity feed item
        UserActivityFeed.objects.create(
            user=prayer_request.requester,
            activity_type='prayer_request_completed',
            title='Your prayer request has completed',
            description=f'Your prayer request "{prayer_request.title}" has reached its duration. You can now send a thank you message to those who prayed.',
            target=prayer_request,
            data={
                'prayer_request_id': prayer_request.id,
                'acceptance_count': prayer_request.get_acceptance_count(),
            }
        )

        count += 1

    logger.info(f"Marked {count} prayer requests as completed")
    return {'success': True, 'completed_count': count}


@shared_task
def send_daily_prayer_count_notifications_task():
    """
    Send daily notifications to prayer request creators about how many people prayed.
    Creates activity feed items: "X users prayed for your request today".

    This task should run daily at 11:30 PM.
    """
    today = timezone.localdate()

    # Get all prayer logs from today, grouped by prayer request
    prayer_logs_today = PrayerRequestPrayerLog.objects.filter(
        prayed_on_date=today
    ).values('prayer_request_id').annotate(
        prayer_count=Count('user', distinct=True)
    )

    # Create a dict mapping prayer_request_id to count
    prayer_counts = {item['prayer_request_id']: item['prayer_count'] for item in prayer_logs_today}

    if not prayer_counts:
        logger.info("No prayers logged today, skipping daily notifications")
        return {'success': True, 'notifications_sent': 0}

    # Fetch prayer requests with their requesters
    # Include both approved requests and completed requests that expired today
    # (to capture prayers logged on the final day before expiration)
    prayer_requests = PrayerRequest.objects.filter(
        id__in=prayer_counts.keys()
    ).filter(
        Q(status='approved') |
        Q(
            status='completed',
            expiration_date__date=today
        )
    ).select_related('requester')

    notifications_sent = 0
    for prayer_request in prayer_requests:
        count = prayer_counts[prayer_request.id]

        if count > 0:
            # Create activity feed item
            UserActivityFeed.objects.create(
                user=prayer_request.requester,
                activity_type='prayer_request_daily_count',
                title=f'{count} {"person" if count == 1 else "people"} prayed for you today',
                description=f'{count} {"person" if count == 1 else "people"} prayed for your request "{prayer_request.title}" today.',
                target=prayer_request,
                data={
                    'prayer_request_id': prayer_request.id,
                    'prayer_count': count,
                    'date': today.isoformat(),
                }
            )
            notifications_sent += 1

    logger.info(f"Sent {notifications_sent} daily prayer count notifications")
    return {'success': True, 'notifications_sent': notifications_sent}
