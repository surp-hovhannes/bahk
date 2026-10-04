"""Prayer request moderation with Cloudflare's Clef decision model.

Clef answers a fixed set of yes/no questions with probabilities; the decision
itself is made here with explicit thresholds. The result has the same shape as
the JSON the LLM moderation prompt returns, so ``moderate_prayer_request_task``
handles both engines identically.

Thresholds were set against a synthetic set of approvals, rejections and
crisis messages (English, Armenian, Spanish) on the full ``clef`` model.
``clef-flash`` missed an implicit suicide note in that set, so it is not the
default for this safety-relevant decision.
"""

from hub.services.clef import run_clef

QUESTIONS = {
    "genuine": {
        "type": "noul",
        "instructions": (
            "Is this a sincere request for prayer support or a thanksgiving for answered prayer "
            "(for example health, family, grief, anxiety or depression, finances, guidance, "
            "spiritual growth, protection, peace), written in good faith?"
        ),
    },
    "crisis": {
        "type": "noul",
        "instructions": (
            "Does the writer express intent, a plan, or an active risk of harming themselves or "
            "someone else, or describe being in immediate danger or ongoing abuse? Sadness, grief, "
            "depression or anxiety alone is NOT enough."
        ),
    },
    "spam": {
        "type": "noul",
        "instructions": (
            "Is this advertising, a link or product promotion, fundraising or donation solicitation, "
            "political campaigning, or repetitive spam rather than a prayer need?"
        ),
    },
    "inappropriate": {
        "type": "noul",
        "instructions": (
            "Does this contain explicit sexual content, graphic violence, hate speech, harassment or "
            "insults aimed at a person or group, or content mocking the Christian faith?"
        ),
    },
    "incoherent": {
        "type": "noul",
        "instructions": "Is this gibberish, a test submission, or an obvious joke rather than a real message?",
    },
    "private_info": {
        "type": "noul",
        "instructions": (
            "Does this expose private information about another identifiable person, such as their "
            "full name together with a phone number, address, email, or a secret they did not "
            "consent to share?"
        ),
    },
}

VIOLATIONS = ("spam", "inappropriate", "incoherent", "private_info")

VIOLATION_REASONS = {
    "spam": "promotional or spam content",
    "inappropriate": "inappropriate or hateful content",
    "incoherent": "incoherent or test content",
    "private_info": "private information about another person",
}

# Escalate on a low crisis probability: a missed crisis costs far more than a
# false alarm, and benign requests scored <= 0.07 in calibration.
CRISIS_THRESHOLD = 0.30
# Reject automatically only when a violation is near-certain and the request
# does not also read as a genuine prayer need.
REJECT_THRESHOLD = 0.85
REJECT_MAX_GENUINE = 0.50
# Approve automatically only when the request is clearly genuine and no
# violation is more likely than not.
APPROVE_MIN_GENUINE = 0.75
APPROVE_MAX_VIOLATION = 0.50


def moderation_state(prayer_request):
    """Render a prayer request as Clef input."""
    title = str(prayer_request.title).strip()
    description = str(prayer_request.description or "").strip()
    if description:
        return f"Title: {title}\nDescription: {description}"
    return f"Title: {title}"


def decide(probabilities):
    """Turn Clef probabilities into an LLM-moderation-shaped result dict."""
    genuine = probabilities["genuine"]
    crisis = probabilities["crisis"]
    top_violation = max(VIOLATIONS, key=lambda name: probabilities[name])
    top_probability = probabilities[top_violation]
    concerns = [VIOLATION_REASONS[name] for name in VIOLATIONS if probabilities[name] >= APPROVE_MAX_VIOLATION]

    if crisis >= CRISIS_THRESHOLD:
        return {
            "approved": False,
            "reason": "Possible risk of harm to the requester or others; needs immediate human attention.",
            "concerns": ["possible safety risk", *concerns],
            "severity": "critical",
            "requires_human_review": True,
            "suggested_action": "escalate",
        }
    if top_probability >= REJECT_THRESHOLD and genuine < REJECT_MAX_GENUINE:
        return {
            "approved": False,
            "reason": f"Rejected as {VIOLATION_REASONS[top_violation]}.",
            "concerns": concerns,
            "severity": "medium",
            "requires_human_review": False,
            "suggested_action": "reject",
        }
    if genuine >= APPROVE_MIN_GENUINE and top_probability < APPROVE_MAX_VIOLATION:
        return {
            "approved": True,
            "reason": "Genuine prayer request with no concerns.",
            "concerns": [],
            "severity": "low",
            "requires_human_review": False,
            "suggested_action": "approve",
        }
    return {
        "approved": False,
        "reason": "Automated moderation was not confident; needs human review.",
        "concerns": concerns or ["unclear whether this is a genuine prayer request"],
        "severity": "high",
        "requires_human_review": True,
        "suggested_action": "flag_for_review",
    }


def clef_moderation_result(prayer_request, model="clef"):
    """Moderate ``prayer_request`` with Clef and return the result dict.

    The dict carries the raw probabilities alongside the decision so admins
    can see why a request was routed where it was.
    """
    answers = run_clef(moderation_state(prayer_request), QUESTIONS, model=model)
    probabilities = {name: float(answers[name]["noul"]) for name in QUESTIONS}
    return {
        **decide(probabilities),
        "engine": "clef",
        "model": model,
        "probabilities": probabilities,
    }
