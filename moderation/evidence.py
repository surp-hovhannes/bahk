"""Plain-language summaries of the automated moderation result stored on a prayer request."""

from prayers.clef_moderation import (
    APPROVE_MAX_VIOLATION,
    APPROVE_MIN_GENUINE,
    CRISIS_THRESHOLD,
    REJECT_THRESHOLD,
    VIOLATIONS,
)

SCORE_LABELS = {
    "genuine": "Genuine prayer need",
    "crisis": "Crisis risk",
    "spam": "Spam or promotion",
    "inappropriate": "Inappropriate",
    "incoherent": "Incoherent or test",
    "private_info": "Private information",
}

LOW_CONFIDENCE_CONCERN = "unclear whether this is a genuine prayer request"


def _checks(prayer):
    result = prayer.moderation_result if isinstance(prayer.moderation_result, dict) else {}
    check = result.get("llm_check")
    profanity = result.get("profanity_check")
    return result, check if isinstance(check, dict) else {}, profanity if isinstance(profanity, dict) else {}


def _cutoff(name, value):
    """Describe the cutoff that matters for this score and whether the score crosses it."""
    if name == "genuine":
        return f"approves at {APPROVE_MIN_GENUINE:.2f} or more", value >= APPROVE_MIN_GENUINE
    if name == "crisis":
        return f"escalates at {CRISIS_THRESHOLD:.2f} or more", value >= CRISIS_THRESHOLD
    return (
        f"blocks approval at {APPROVE_MAX_VIOLATION:.2f}, rejects at {REJECT_THRESHOLD:.2f}",
        value >= APPROVE_MAX_VIOLATION,
    )


def review_reason(prayer):
    """A short reason a request is waiting for a person, for queue status labels."""
    result, check, profanity = _checks(prayer)
    if not check or "error" in check:
        return "Automated check failed"
    if profanity.get("passed") is False:
        return "Profanity, otherwise genuine" if check.get("suggested_action") == "approve" else "Profanity"
    concerns = [c for c in check.get("concerns") or [] if isinstance(c, str) and c]
    specific = [c for c in concerns if c != LOW_CONFIDENCE_CONCERN]
    if specific:
        concern = specific[0]
        return "Possible " + (concern if len(concern) <= 40 else concern[:39].rstrip() + "…")
    return "Low confidence"


def summarize(prayer):
    """Reason, concerns and labelled scores for the detail page; None without a result."""
    result, check, profanity = _checks(prayer)
    if not result:
        return None
    probabilities = check.get("probabilities") if isinstance(check.get("probabilities"), dict) else {}
    scores = []
    for name in ("genuine", "crisis", *VIOLATIONS):
        value = probabilities.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        cutoff, crossed = _cutoff(name, value)
        scores.append(
            dict(
                label=SCORE_LABELS[name],
                value=value,
                percent=round(max(0.0, min(1.0, value)) * 100),
                cutoff=cutoff,
                crossed=crossed,
                good=name == "genuine",
            )
        )
    engine = check.get("engine") or ("" if not check or "error" in check else "llm")
    return dict(
        reason=result.get("reason") or check.get("reason") or "",
        concerns=[c for c in check.get("concerns") or [] if isinstance(c, str) and c],
        error=check.get("error") if "error" in check else None,
        engine={"clef": "Clef decision model", "llm": "Language model prompt"}.get(engine, engine),
        model=check.get("model", ""),
        profanity=profanity.get("passed") is False,
        scores=scores,
    )
