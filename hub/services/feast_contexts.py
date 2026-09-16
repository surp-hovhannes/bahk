"""Append-only FeastContext versioning and staff regeneration task metadata."""

from copy import deepcopy
from dataclasses import dataclass
import logging
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max

from hub.cache import invalidate_feast_api_cache_for_feast
from hub.models import Feast, FeastContext, LLMPrompt

logger = logging.getLogger(__name__)

FEAST_CONTEXT_TASK_TIME_LIMIT = 12 * 60
FEAST_CONTEXT_TASK_SOFT_TIME_LIMIT = 10 * 60
FEAST_CONTEXT_TASK_MAX_RETRIES = 3
FEAST_CONTEXT_TASK_RETRY_DELAY = 60
FEAST_CONTEXT_TASK_TTL = (
    FEAST_CONTEXT_TASK_TIME_LIMIT + FEAST_CONTEXT_TASK_RETRY_DELAY
) * (FEAST_CONTEXT_TASK_MAX_RETRIES + 1)
FEAST_CONTEXT_TASK_STATUS_TTL = 24 * 60 * 60


class FeastContextRegenerationUnavailable(RuntimeError):
    """Raised when the cache cannot safely provide regeneration idempotency."""


@dataclass(frozen=True)
class FeastContextVersionInput:
    """Content and provenance for a newly appended context version."""

    operation: str
    actor_id: int | None = None
    prompt: LLMPrompt | None = None
    additional_instructions: str = ""
    restored_from: FeastContext | None = None


def available_feast_context_languages() -> tuple[str, ...]:
    """Return configured, de-duplicated language codes in stable order."""
    return tuple(dict.fromkeys(settings.MODELTRANS_AVAILABLE_LANGUAGES))


def _translated_field(field: str, language: str) -> str:
    default_language = getattr(settings, "MODELTRANS_DEFAULT_LANGUAGE", "en")
    return field if language == default_language else f"{field}_{language}"


def feast_context_languages(context: FeastContext, *, include_text: bool = True) -> dict:
    """Serialize every configured language without activating thread-local translations."""
    languages = {}
    for language in available_feast_context_languages():
        text = getattr(context, _translated_field("text", language), "") or ""
        short_text = getattr(context, _translated_field("short_text", language), "") or ""
        language_data = {"has_text": bool(text.strip() and short_text.strip())}
        if include_text:
            language_data.update({"text": text, "short_text": short_text})
        languages[language] = language_data
    return languages


def append_feast_context_version(
    feast_id: int,
    *,
    values: FeastContextVersionInput,
    language_updates: dict[str, dict[str, str]] | None = None,
    replace_languages: bool = True,
) -> FeastContext:
    """Append and activate one version while holding the feast's row lock.

    The active version is copied first, including all translation JSON. Supplied language
    updates are then applied. Historical rows are only deactivated, never edited or reactivated.
    """
    language_updates = language_updates or {}
    unknown_languages = set(language_updates) - set(available_feast_context_languages())
    if unknown_languages:
        raise ValueError(f"Unsupported languages: {', '.join(sorted(unknown_languages))}")

    with transaction.atomic():
        feast = Feast.objects.select_for_update().get(pk=feast_id)
        contexts = FeastContext.objects.filter(feast=feast)
        active = (
            contexts.select_for_update()
            .filter(active=True)
            .order_by("-version", "-pk")
            .first()
        )
        next_version = (contexts.aggregate(max_version=Max("version"))["max_version"] or 0) + 1

        context = FeastContext(
            feast=feast,
            version=next_version,
            operation=values.operation,
            created_by_id=values.actor_id,
            prompt=values.prompt or (active.prompt if active else None),
            additional_instructions=values.additional_instructions,
            restored_from=values.restored_from,
            text=active.text if active else "",
            short_text=active.short_text if active else "",
            i18n=deepcopy(active.i18n) if active and active.i18n else {},
            thumbs_up=0,
            thumbs_down=0,
            active=True,
        )

        for language, update in language_updates.items():
            text_field = _translated_field("text", language)
            short_text_field = _translated_field("short_text", language)
            if replace_languages or not (getattr(context, text_field, "") or "").strip():
                setattr(context, text_field, update["text"])
            if replace_languages or not (getattr(context, short_text_field, "") or "").strip():
                setattr(context, short_text_field, update["short_text"])

        context._feast_cache_invalidation_managed = True
        context.save()
        transaction.on_commit(lambda: invalidate_feast_api_cache_for_feast(feast))
        return context


def restore_feast_context_version(
    feast_id: int, *, version: int, actor_id: int | None
) -> FeastContext:
    """Copy a historical version into a newly appended active version."""
    with transaction.atomic():
        feast = Feast.objects.select_for_update().get(pk=feast_id)
        source = (
            FeastContext.objects.select_for_update()
            .filter(feast=feast, version=version)
            .order_by("-active", "-time_of_generation", "-pk")
            .first()
        )
        if source is None:
            raise FeastContext.DoesNotExist
        updates = {
            language: {
                "text": getattr(source, _translated_field("text", language), "") or "",
                "short_text": getattr(
                    source, _translated_field("short_text", language), ""
                )
                or "",
            }
            for language in available_feast_context_languages()
        }
        return append_feast_context_version(
            feast.id,
            values=FeastContextVersionInput(
                operation=FeastContext.Operation.RESTORED,
                actor_id=actor_id,
                prompt=source.prompt,
                restored_from=source,
            ),
            language_updates=updates,
        )


def feast_context_regeneration_lock_key(feast_id: int) -> str:
    return f"feast-context:regeneration:{feast_id}"


def feast_context_task_status_key(task_id: str) -> str:
    return f"feast-context:task:{task_id}"


def get_feast_context_task_status(task_id: str) -> dict | None:
    try:
        return cache.get(feast_context_task_status_key(task_id))
    except Exception:
        logger.warning("Failed to read FeastContext task status", exc_info=True)
        return None


def set_feast_context_task_status(
    task_id: str,
    *,
    feast_id: int,
    state: str,
    ready: bool,
    error: str | None = None,
) -> None:
    try:
        cache.set(
            feast_context_task_status_key(task_id),
            {
                "task_id": task_id,
                "feast_id": feast_id,
                "state": state,
                "ready": ready,
                "error": error,
            },
            timeout=FEAST_CONTEXT_TASK_STATUS_TTL,
        )
    except Exception:
        logger.warning("Failed to write FeastContext task status", exc_info=True)


def clear_feast_context_regeneration_lock(feast_id: int, task_id: str) -> None:
    """Clear only the lock still owned by this task."""
    key = feast_context_regeneration_lock_key(feast_id)
    try:
        if cache.get(key) == task_id:
            cache.delete(key)
    except Exception:
        logger.warning("Failed to clear FeastContext regeneration lock", exc_info=True)


def enqueue_feast_context_regeneration(
    feast_id: int, *, actor_id: int, additional_instructions: str = ""
) -> tuple[str, bool]:
    """Queue one staff regeneration or return the already-running task id."""
    from hub.tasks import generate_feast_context_task

    task_id = str(uuid4())
    lock_key = feast_context_regeneration_lock_key(feast_id)
    try:
        if not cache.add(lock_key, task_id, timeout=FEAST_CONTEXT_TASK_TTL):
            existing_task_id = cache.get(lock_key)
            if existing_task_id:
                return existing_task_id, False
            if not cache.add(lock_key, task_id, timeout=FEAST_CONTEXT_TASK_TTL):
                raise FeastContextRegenerationUnavailable
    except FeastContextRegenerationUnavailable:
        raise
    except Exception as exc:
        raise FeastContextRegenerationUnavailable from exc

    set_feast_context_task_status(
        task_id, feast_id=feast_id, state="PENDING", ready=False
    )
    try:
        generate_feast_context_task.apply_async(
            args=[feast_id],
            kwargs={
                "force_regeneration": True,
                "improvement_instructions": additional_instructions,
                "actor_id": actor_id,
            },
            task_id=task_id,
        )
    except Exception as exc:
        clear_feast_context_regeneration_lock(feast_id, task_id)
        try:
            cache.delete(feast_context_task_status_key(task_id))
        except Exception:
            logger.warning("Failed to delete FeastContext task status", exc_info=True)
        raise FeastContextRegenerationUnavailable from exc
    return task_id, True
