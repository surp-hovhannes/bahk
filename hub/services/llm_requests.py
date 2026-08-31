"""Provider request construction for the supported LLM model families."""


OPENAI_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def openai_chat_completion(client, *, model, messages, max_tokens, temperature=None, **kwargs):
    """Create a Chat Completions request using model-compatible parameters."""
    request = {
        "model": model,
        "messages": messages,
        **kwargs,
    }
    if model.startswith(OPENAI_REASONING_MODEL_PREFIXES):
        request["max_completion_tokens"] = max_tokens
    else:
        request["max_tokens"] = max_tokens
        if temperature is not None:
            request["temperature"] = temperature
    return client.chat.completions.create(**request)


def anthropic_message(client, *, model, messages, max_tokens, system=None, **kwargs):
    """Create an Anthropic Messages request without removed sampling keywords."""
    request = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        **kwargs,
    }
    if system:
        request["system"] = system
    return client.messages.create(**request)
