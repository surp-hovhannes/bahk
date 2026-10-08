"""Provider request construction for the supported LLM model families."""

from bahk.public_api.work import reject_public_work
from hub.services.llm_models import (
    ADAPTIVE_THINKING_MODELS,
    DEFAULT_EFFORT,
    THINKING_HEADROOM_TOKENS,
    resolve_model,
)

OPENAI_REASONING_MODEL_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")


class LLMResponseError(ValueError):
    """Raised when a provider response carries no usable answer text."""


def openai_chat_completion(client, *, model, messages, max_tokens, temperature=None, **kwargs):
    """Create a Chat Completions request using model-compatible parameters."""
    reject_public_work("llm")
    model = resolve_model(model)
    request = {
        "model": model,
        "messages": messages,
        **kwargs,
    }
    if model.startswith(OPENAI_REASONING_MODEL_PREFIXES):
        request.pop("top_p", None)
        request["max_completion_tokens"] = max_tokens
    else:
        request["max_tokens"] = max_tokens
        if temperature is not None:
            request["temperature"] = temperature
    return client.chat.completions.create(**request)


def anthropic_message(client, *, model, messages, max_tokens, system=None, effort=None, **kwargs):
    """Create an Anthropic Messages request without removed sampling keywords.

    ``max_tokens`` is the budget for the reply. On models that think by default
    the request also carries an effort level and headroom for thinking, which
    counts toward the provider's ``max_tokens``.
    """
    reject_public_work("llm")
    model = resolve_model(model)
    request = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        **kwargs,
    }
    if model in ADAPTIVE_THINKING_MODELS:
        request["max_tokens"] = max_tokens + THINKING_HEADROOM_TOKENS
        request["output_config"] = {"effort": effort or DEFAULT_EFFORT, **request.get("output_config", {})}
    if system:
        request["system"] = system
    return client.messages.create(**request)


def anthropic_text(response):
    """Return the answer text of a Messages response, read by block type.

    Responses can open with ``thinking`` blocks, so the answer is not always
    ``content[0]``. Refusals and replies cut off before any text raise
    ``LLMResponseError`` rather than passing an empty string downstream.
    """
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise LLMResponseError(f"Model declined the request (category: {category})")
    text = "".join(
        block.text for block in (getattr(response, "content", None) or []) if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise LLMResponseError(f"Model returned no text (stop_reason: {stop_reason})")
    return text
