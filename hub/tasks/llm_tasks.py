import logging

from celery import shared_task
from django.conf import settings

from hub.models import LLMPrompt, Reading, ReadingContext, Feast, FeastContext
from hub.services.feast_contexts import (
    FEAST_CONTEXT_TASK_MAX_RETRIES,
    FEAST_CONTEXT_TASK_RETRY_DELAY,
    FEAST_CONTEXT_TASK_SOFT_TIME_LIMIT,
    FEAST_CONTEXT_TASK_TIME_LIMIT,
    FeastContextVersionInput,
    append_feast_context_version,
    clear_feast_context_regeneration_lock,
    get_feast_context_task_status,
    set_feast_context_task_status,
)
from hub.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

AVAILABLE_LANGUAGES = getattr(settings, 'MODELTRANS_AVAILABLE_LANGUAGES', ['en', 'hy'])


def _check_all_translations_present(context: ReadingContext, languages: list[str]) -> bool:
    """Check if context has translations for all languages."""
    for lang in languages:
        if lang == 'en':
            text = context.text
        else:
            text = getattr(context, f'text_{lang}', None)
        
        if not text or not text.strip():
            return False
    return True


def _update_context_translations(
    context: ReadingContext, 
    generated_contexts: dict[str, str], 
    force_regeneration: bool
) -> None:
    """Update existing context with missing or regenerated translations."""
    for lang, context_text in generated_contexts.items():
        if lang == 'en':
            if not context.text or not context.text.strip() or force_regeneration:
                context.text = context_text
        else:
            existing_text = getattr(context, f'text_{lang}', None)
            if not existing_text or not existing_text.strip() or force_regeneration:
                setattr(context, f'text_{lang}', context_text)
    context.save()


def _create_context_with_translations(
    reading: Reading,
    llm_prompt: LLMPrompt,
    generated_contexts: dict[str, str]
) -> ReadingContext:
    """Create new context with all translations."""
    english_text = generated_contexts.get('en', '')
    context = ReadingContext(
        reading=reading,
        text=english_text,
        prompt=llm_prompt,
    )
    
    for lang, context_text in generated_contexts.items():
        if lang != 'en':
            setattr(context, f'text_{lang}', context_text)
    
    context.save()
    return context


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_reading_context_task(
    self, reading_id: int, force_regeneration: bool = False, language_code: str = None
):
    """Generate and save AI context for a Reading instance in all available languages.

    Args:
        reading_id: ID of the Reading to generate context for
        force_regeneration: If True, regenerate even if context exists
        language_code: DEPRECATED - Ignored. All languages are always generated.
    """
    if language_code is not None:
        logger.warning(
            "language_code parameter is deprecated and will be removed in a future version. "
            "All languages are now generated automatically."
        )

    try:
        reading = Reading.objects.get(pk=reading_id)
    except Reading.DoesNotExist:
        logger.error("Reading with id %s not found.", reading_id)
        return

    active_context = reading.active_context
    if active_context and not force_regeneration:
        if _check_all_translations_present(active_context, AVAILABLE_LANGUAGES):
            logger.info(
                "Reading %s already has context for all languages, skipping.",
                reading_id
            )
            return

    llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='readings').first()
    if not llm_prompt:
        logger.error("No active LLM prompt found for readings.")
        return

    try:
        service = llm_prompt.get_llm_service()
        
        generated_contexts = {}
        for lang in AVAILABLE_LANGUAGES:
            context_text = service.generate_context(reading, llm_prompt, lang)
            if context_text:
                generated_contexts[lang] = context_text
            else:
                logger.warning(
                    "Failed to generate context for Reading %s in language %s",
                    reading_id, lang
                )
        
        if not generated_contexts:
            logger.error("Failed to generate context for Reading %s in any language", reading_id)
            raise self.retry(exc=Exception("Context generation failed for all languages"))
        
        if active_context:
            _update_context_translations(active_context, generated_contexts, force_regeneration)
            logger.info(
                "Context translations updated for Reading %s (languages: %s)",
                reading_id, ', '.join(generated_contexts.keys())
            )
        else:
            _create_context_with_translations(reading, llm_prompt, generated_contexts)
            logger.info(
                "Context generated for Reading %s in languages: %s",
                reading_id, ', '.join(generated_contexts.keys())
            )
    except ValueError as e:
        logger.error(f"Error selecting LLM service: {e}")
        raise self.retry(exc=e)


def _check_all_feast_translations_present(context: FeastContext, languages: list[str]) -> bool:
    """Check if feast context has translations for all languages (both text and short_text)."""
    for lang in languages:
        if lang == 'en':
            text = context.text
            short_text = context.short_text
        else:
            text = getattr(context, f'text_{lang}', None)
            short_text = getattr(context, f'short_text_{lang}', None)
        
        if not text or not text.strip() or not short_text or not short_text.strip():
            return False
    return True


def _append_generated_feast_context(
    feast: Feast,
    llm_prompt: LLMPrompt,
    generated_contexts: dict[str, dict[str, str]],
    *,
    force_regeneration: bool,
    improvement_instructions: str,
    actor_id: int | None,
) -> FeastContext:
    """Persist generated translations as a new context version."""
    return append_feast_context_version(
        feast.id,
        values=FeastContextVersionInput(
            operation=(
                FeastContext.Operation.REGENERATED
                if force_regeneration
                else FeastContext.Operation.GENERATED
            ),
            actor_id=actor_id,
            prompt=llm_prompt,
            additional_instructions=improvement_instructions or "",
        ),
        language_updates=generated_contexts,
        replace_languages=force_regeneration,
    )

def is_feast_context_generation_eligible(feast: Feast) -> bool:
    """Return whether FeastContext generation should run for this feast.

    A ``Feast`` row now exists only for an observance the engine marks ``is_comm``, so "is there
    anything here to write about" is already answered upstream, by a human-reviewed mark rather
    than by reading the display name.

    This used to guess from the name -- regexes looking for saint/martyr/prophet words, and for
    "fast"/"lent" plus "day" -- and both halves were wrong in the same way: they pattern-matched
    text the engine is free to rewrite, which is exactly the failure the observance id layer
    exists to prevent. They also disagreed with each other on Mijink, which the token list
    blocked while ``determine_feast_designation_task`` exempted as a named feast, not a generic
    fast day. The engine marks it a commemoration; it gets context.
    """
    return feast.designation != Feast.Designation.FAST


@shared_task(
    bind=True,
    max_retries=FEAST_CONTEXT_TASK_MAX_RETRIES,
    default_retry_delay=FEAST_CONTEXT_TASK_RETRY_DELAY,
    soft_time_limit=FEAST_CONTEXT_TASK_SOFT_TIME_LIMIT,
    time_limit=FEAST_CONTEXT_TASK_TIME_LIMIT,
)
def generate_feast_context_task(
    self,
    feast_id: int,
    force_regeneration: bool = False,
    language_code: str = None,
    improvement_instructions: str = None,
    actor_id: int | None = None,
):
    """Generate and append AI context for a Feast in all available languages."""
    task_id = self.request.id
    track_status = bool(
        task_id
        and (actor_id is not None or get_feast_context_task_status(task_id))
    )
    if track_status:
        set_feast_context_task_status(
            task_id, feast_id=feast_id, state="STARTED", ready=False
        )

    if language_code is not None:
        logger.warning(
            "language_code parameter is deprecated and will be removed in a future version. "
            "All languages are now generated automatically."
        )

    try:
        feast = Feast.objects.get(pk=feast_id)
    except Feast.DoesNotExist:
        logger.error("Feast with id %s not found.", feast_id)
        if track_status:
            set_feast_context_task_status(
                task_id, feast_id=feast_id, state="FAILURE", ready=True,
                error="Feast not found.",
            )
            clear_feast_context_regeneration_lock(feast_id, task_id)
        return

    if not is_feast_context_generation_eligible(feast):
        logger.info("Feast %s is a generic fast day, skipping context generation.", feast_id)
        if track_status:
            set_feast_context_task_status(
                task_id, feast_id=feast_id, state="FAILURE", ready=True,
                error="This feast is not eligible for context generation.",
            )
            clear_feast_context_regeneration_lock(feast_id, task_id)
        return

    active_context = feast.active_context
    if active_context and not force_regeneration:
        if _check_all_feast_translations_present(active_context, AVAILABLE_LANGUAGES):
            logger.info(
                "Feast %s already has context for all languages, skipping.", feast_id
            )
            if track_status:
                set_feast_context_task_status(
                    task_id, feast_id=feast_id, state="SUCCESS", ready=True
                )
                clear_feast_context_regeneration_lock(feast_id, task_id)
            return

    llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
    if not llm_prompt:
        logger.error("No active LLM prompt found for feasts.")
        if track_status:
            set_feast_context_task_status(
                task_id, feast_id=feast_id, state="FAILURE", ready=True,
                error="No active feast generation prompt is configured.",
            )
            clear_feast_context_regeneration_lock(feast_id, task_id)
        return

    try:
        service = llm_prompt.get_llm_service()
        generated_contexts = {}
        for lang in AVAILABLE_LANGUAGES:
            context_dict = service.generate_feast_context(
                feast, llm_prompt, lang, improvement_instructions
            )
            if (
                context_dict
                and isinstance(context_dict.get('text'), str)
                and context_dict['text'].strip()
                and isinstance(context_dict.get('short_text'), str)
                and context_dict['short_text'].strip()
            ):
                generated_contexts[lang] = {
                    "text": context_dict["text"].strip(),
                    "short_text": context_dict["short_text"].strip(),
                }
            else:
                logger.warning(
                    "Failed to generate complete context for Feast %s in language %s",
                    feast_id, lang
                )

        if not generated_contexts:
            raise RuntimeError("Context generation failed for all languages")

        _append_generated_feast_context(
            feast,
            llm_prompt,
            generated_contexts,
            force_regeneration=force_regeneration,
            improvement_instructions=improvement_instructions or "",
            actor_id=actor_id,
        )
        logger.info(
            "Context version appended for Feast %s (languages: %s)",
            feast_id, ', '.join(generated_contexts.keys())
        )
        if track_status:
            set_feast_context_task_status(
                task_id, feast_id=feast_id, state="SUCCESS", ready=True
            )
            clear_feast_context_regeneration_lock(feast_id, task_id)
    except Exception as exc:
        logger.exception("Error generating context for Feast %s", feast_id)
        if self.request.retries < self.max_retries:
            if track_status:
                set_feast_context_task_status(
                    task_id, feast_id=feast_id, state="RETRY", ready=False
                )
            raise self.retry(exc=exc)
        if track_status:
            set_feast_context_task_status(
                task_id, feast_id=feast_id, state="FAILURE", ready=True,
                error="Context generation failed.",
            )
            clear_feast_context_regeneration_lock(feast_id, task_id)
        raise


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def determine_feast_designation_task(self, feast_id: int):
    """Determine and set the designation for a Feast instance using AI.

    Every row reaching here is a commemoration -- the engine's ``is_comm`` mark is what mints it --
    so there is nothing left for a name regex to screen out.  This used to short-circuit
    "<Ordinal> day of Great Lent"-shaped names straight to ``FAST`` without asking the LLM, with
    hardcoded carve-outs for saint words and for Mijink.  The carve-out spelled out ``Saint`` and
    never ``St.``, which is the abbreviation the engine's display text overwhelmingly uses, so
    days plainly naming a saint were stamped as generic fasts anyway -- St. Theodore the Tyron,
    Lazarus Saturday and St. Gregory's Descent into the Pit among them on production.

    That is not a cost the classifier can undo later: ``designation`` is never overwritten once
    set, and it is what stands between a feast and its generated context.  Migration ``0068``
    repairs the rows this already wrote; removing it is what stops new ones.

    Args:
        feast_id: ID of the Feast to determine designation for
    """
    try:
        feast = Feast.objects.get(pk=feast_id)
    except Feast.DoesNotExist:
        logger.error("Feast with id %s not found.", feast_id)
        return

    # Skip if designation is already set (don't overwrite manual assignments)
    if feast.designation:
        logger.info("Feast %s already has designation '%s', skipping.", feast_id, feast.designation)
        return

    # Determine which LLM service to use based on active prompt or default
    model_name = None
    llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='feasts').first()
    if llm_prompt:
        model_name = llm_prompt.model
    
    try:
        service = get_llm_service(model_name if model_name else 'claude-sonnet-4-5-20250929')
        designation = service.determine_feast_designation(feast, model_name)
        
        if designation:
            # Verify it's a valid choice
            valid_choices = [choice[0] for choice in Feast.Designation.choices]
            if designation in valid_choices:
                feast.designation = designation
                feast.save(update_fields=['designation'])
                logger.info(
                    "Determined designation '%s' for Feast %s (%s)",
                    designation, feast_id, feast.name
                )
            else:
                logger.warning(
                    "Invalid designation returned: '%s' for Feast %s. Valid choices: %s",
                    designation, feast_id, valid_choices
                )
        else:
            logger.warning("Could not determine designation for Feast %s (%s)", feast_id, feast.name)
    except ValueError as e:
        logger.error(f"Error selecting LLM service for designation: {e}")
        # Don't retry on ValueError, just log the error
    except Exception as e:
        logger.exception(f"Error determining designation for Feast {feast_id}: {e}")
        # Don't retry on general exceptions, just log the error


@shared_task
def tag_intention_prayers(intention_id):
    """LLM-tag a FastIntention's text and store the result in matched_tags.

    No-op (leaves matched_tags None) if no active 'intentions' LLMPrompt exists
    or the LLM call fails — the curated keyword map remains the read-path fallback.
    """
    from hub.intention_recommendations import llm_tags_for_intention
    from hub.models import FastIntention

    intention = FastIntention.objects.filter(id=intention_id, is_active=True).first()
    if not intention or not intention.text.strip():
        return
    tags = llm_tags_for_intention(intention.text)
    if tags is None:
        return
    intention.matched_tags = tags
    intention.save(update_fields=['matched_tags', 'updated_at'])
    logger.debug("Intention %s tagged with %s", intention_id, tags)
