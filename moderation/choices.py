"""Human-readable decisions and selectable published test cases."""

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

ACTION_LABELS = {
    "approve": "Approve and publish",
    "reject": "Reject request",
    "acknowledge": "Complete review",
    "resolve": "Close report",
    "escalated": "Record escalation and close",
    "misclassification": "Report a review problem",
}


def action_label(action, *, crisis=False):
    if action == "acknowledge" and crisis:
        return "Close after follow-up"
    return ACTION_LABELS.get(action, action)


def correct_decisions(kind):
    if kind == "prayer":
        return (
            ("approve", "Approve and publish"),
            ("reject", "Keep unpublished"),
            ("flag_for_review", "Send for general review"),
            ("escalate", "Send to a crisis reviewer"),
        )
    if kind == "icon":
        return (("keep_open", "Keep report open"), ("resolve", "Close report"))
    return (("keep_open", "Keep in the review queue"), ("acknowledge", "Complete review"))


@lru_cache(maxsize=1)
def published_test_cases():
    """Only the committed, published synthetic subset is offered as a reference."""
    path = Path(__file__).resolve().parent.parent / "docs/evaluations/clef-synthetic-evaluation.json"
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        return tuple((case["recovery_id"], f"{case['recovery_id']} — {case['title']}") for case in cases)
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Published moderation test catalogue could not be loaded")
        return ()
