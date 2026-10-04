"""Client for Cloudflare's Clef decision models on Workers AI.

Clef answers typed questions about a piece of input ("state") with calibrated
probabilities instead of free text, so callers make the final decision with
plain thresholds in code.
"""

import requests
from django.conf import settings

from bahk.public_api.work import reject_public_work

CLEF_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/{model}"
CLEF_MODELS = ("clef", "clef-flash")


class ClefError(Exception):
    """Raised when Clef cannot return a usable answer."""


def run_clef(state, questions, *, model="clef", timeout=20):
    """Ask Clef ``questions`` about ``state`` and return its ``answers`` dict.

    ``questions`` maps question ids to definitions, e.g.
    ``{"spam": {"type": "noul", "instructions": "Is this spam?"}}``. Every
    question id is guaranteed to be present in the returned answers.
    """
    reject_public_work("llm")
    if model not in CLEF_MODELS:
        raise ClefError(f"Unknown Clef model: {model}")
    account_id = settings.CLOUDFLARE_WORKERSAI_ACCOUNT_ID
    api_key = settings.CLOUDFLARE_WORKERSAI_API_KEY
    if not account_id or not api_key:
        raise ClefError("Cloudflare Workers AI credentials are not configured")

    try:
        response = requests.post(
            CLEF_URL.format(account_id=account_id, model=model),
            json={"model": model, "state": state, "questions": questions},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise ClefError(f"Clef request failed: {exc}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200 or not payload.get("success"):
        errors = payload.get("errors") or response.text[:300]
        raise ClefError(f"Clef returned HTTP {response.status_code}: {errors}")

    answers = (payload.get("result") or {}).get("answers") or {}
    missing = set(questions) - set(answers)
    if missing:
        raise ClefError(f"Clef response missing answers for: {sorted(missing)}")
    return answers
