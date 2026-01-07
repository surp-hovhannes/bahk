"""
Central configuration for the `better_profanity` filter.

We use `better_profanity` as an initial pass for content moderation. Its default
dictionary contains some words (e.g. "god") that are not profane in the context
of a Christian prayer app and would cause false-positive rejections.
"""

from __future__ import annotations

from better_profanity import profanity


# Words that should NOT be treated as profanity in this app's context.
# NOTE: `better_profanity`'s wordset is lowercase.
PROFANITY_ALLOWLIST = {
    "god",
}


def configure_profanity_filter() -> None:
    """
    Load the default censor word list and apply our allowlist overrides.

    This function is safe to call multiple times.
    """
    profanity.load_censor_words()

    # Remove allowlisted words to avoid false positives.
    for word in PROFANITY_ALLOWLIST:
        censor_words = profanity.CENSOR_WORDSET
        # `better_profanity` has used both `set` and `list` for this container
        # across versions. Support both.
        if hasattr(censor_words, "discard"):
            censor_words.discard(word)
            continue

        # Fallback: treat it as a list-like container.
        try:
            while word in censor_words:
                censor_words.remove(word)
        except (TypeError, AttributeError):
            # If the underlying type changes again, fail open rather than
            # crashing app startup.
            pass


