"""Model inventory for LLM generation: lifecycle, providers, and runtime resolution.

Every model ID the app sends to a provider is decided here, so the dropdown on
``LLMPrompt``, the hard-coded fallbacks, and the request builders cannot drift
apart. Lifecycle entries were verified against the provider deprecation pages
on 2026-09-30 (issue #561); see docs/LLM_MODELS.md for sources.
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

ANTHROPIC = "anthropic"
OPENAI = "openai"

ACTIVE = "active"
DEPRECATED = "deprecated"
RETIRED = "retired"

# Defaults for code paths that run without an active LLMPrompt.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
# Short, high-volume classification (feast designation).
DEFAULT_CLAUDE_CLASSIFIER_MODEL = "claude-haiku-5-5"
# Prayer-request moderation stays on Sonnet: a wrong approval is costlier than tokens.
DEFAULT_MODERATION_MODEL = "claude-sonnet-5-5"

# Claude models that think by default. Thinking tokens count toward
# ``max_tokens``, so requests to these models get an explicit effort level and
# headroom on top of the reply budget the caller asked for.
ADAPTIVE_THINKING_MODELS = frozenset({"claude-sonnet-5-5", "claude-haiku-5-5"})
DEFAULT_EFFORT = "low"
THINKING_HEADROOM_TOKENS = 2048


@dataclass(frozen=True)
class ModelLifecycle:
    status: str
    # First day the provider may stop serving the ID (conservative where notices conflict).
    shutdown: Optional[datetime.date] = None
    # Reviewed replacement; None means a human must choose one.
    replacement: Optional[str] = None


# IDs absent from this table are treated as active.
MODEL_LIFECYCLE = {
    "claude-3-5-sonnet-20241022": ModelLifecycle(RETIRED, datetime.date(2025, 10, 28), "claude-sonnet-5-5"),
    # Provider notices disagree on Nov 24 vs Nov 30; plan for reduced availability from Oct 30.
    "claude-sonnet-4-5-20250929": ModelLifecycle(DEPRECATED, datetime.date(2026, 10, 30), "claude-sonnet-5-5"),
    # OpenAI replacements are not mapped yet: pick one per workload after evaluation.
    "o4-mini": ModelLifecycle(DEPRECATED, datetime.date(2026, 10, 23)),
    "gpt-5": ModelLifecycle(DEPRECATED, datetime.date(2026, 12, 11)),
    "gpt-5-mini": ModelLifecycle(DEPRECATED, datetime.date(2026, 12, 11)),
    "gpt-5-nano": ModelLifecycle(DEPRECATED, datetime.date(2026, 12, 11)),
    "gpt-5-mini-2025-08-07": ModelLifecycle(DEPRECATED, datetime.date(2026, 12, 11)),
}


class UnsupportedModelError(ValueError):
    """Raised when a model ID cannot be served and has no reviewed replacement."""


def provider_for(model: str) -> str:
    """Return the provider that serves ``model``."""
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return OPENAI
    if model.startswith("claude"):
        return ANTHROPIC
    raise UnsupportedModelError(f"Unsupported model: {model}")


def lifecycle_for(model: str) -> ModelLifecycle:
    return MODEL_LIFECYCLE.get(model, ModelLifecycle(ACTIVE))


def is_past_shutdown(model: str, today: Optional[datetime.date] = None) -> bool:
    lifecycle = lifecycle_for(model)
    if lifecycle.status == RETIRED:
        return True
    today = today or datetime.date.today()
    return lifecycle.shutdown is not None and today >= lifecycle.shutdown


def can_activate(model: str) -> bool:
    """Whether a prompt may newly select or activate ``model``."""
    return lifecycle_for(model).status == ACTIVE


def resolve_model(model: str, today: Optional[datetime.date] = None) -> str:
    """Return the model ID to send to the provider for a configured ``model``.

    Saved prompts keep their configured ID (it is their provenance); only the
    request is redirected, and only once the provider has stopped serving it.
    """
    if not is_past_shutdown(model, today):
        return model
    replacement = lifecycle_for(model).replacement
    if replacement is None:
        raise UnsupportedModelError(
            f"Model {model!r} is past its provider shutdown date and has no reviewed "
            "replacement. Select a supported model on the LLM prompt."
        )
    logger.warning("Model %s is past shutdown; sending request to %s instead", model, replacement)
    return replacement
