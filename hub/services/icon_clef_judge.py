"""Judge an icon shortlist for a feast with Cloudflare's Clef decision model.

The LLM matcher asks a model to write a ranked JSON shortlist and then validates
it row by row; most of its failure handling exists because those rows can be
malformed. Here each shortlisted icon gets the same few yes/no questions, Clef
returns a probability for each, and suggestion and assignment are thresholds in
code. This is the pattern ``prayers/clef_moderation.py`` uses for moderation.

The thresholds are starting points, not calibrated values. Tune them against
labelled cases with ``evaluate_icon_matching_cases --engine clef`` before any
production use.
"""

import contextvars
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from bahk.public_api.work import reject_public_work
from hub.services.clef import ClefError, noul_probabilities, run_clef
from hub.services.icon_match_service import IconMatchOutcome, serialize_catalogue

# Asked once per feast.
FEAST_QUESTIONS = {
    "names_event": {
        "type": "noul",
        "instructions": (
            "Does this commemoration remember a specific event or scene, such as a miracle, a martyrdom, "
            "a finding or exaltation, or a feast of the Lord, rather than only honouring one or more persons?"
        ),
    },
}

# Asked once per shortlisted icon, against the feast and that icon's metadata.
ICON_QUESTIONS = {
    "depicts_event": {
        "type": "noul",
        "instructions": (
            "Does this icon depict the event or scene the commemoration remembers? Answer no if the "
            "commemoration remembers no event, or if the icon only shows the people involved without that event."
        ),
    },
    "all_figures": {
        "type": "noul",
        "instructions": (
            "Does this icon identifiably depict every person or group the commemoration names? Answer no if "
            "any named person or group is missing, or if the icon shows a different person who merely shares "
            "a name, a role or a category such as martyr, warrior or priest."
        ),
    },
    "related": {
        "type": "noul",
        "instructions": (
            "Does this icon depict at least one person, scene or object this commemoration is specifically "
            "about? Answer no if it shares only a period, artist, manuscript, medium or a broad category."
        ),
    },
}

# A probability at or above this places an icon on that rung of the ladder.
RUNG_THRESHOLD = 0.50
# Assignment needs a near-certain answer on the rung that justifies it.
ASSIGN_THRESHOLD = 0.85
SHORTLIST_LIMIT = 24
RANK = ("exact_event", "exact_subject", "subject_portrait", "related_specific")
MAX_WORKERS = 8

STOPWORDS = frozenset(
    "a and day feast for great her his holy in of remembrance saint saints st sts sunday the their to".split()
)


def _tokens(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return {t for t in re.findall(r"\w+", text) if len(t) > 1 and t not in STOPWORDS}


def lexical_shortlist(feast, records, limit=SHORTLIST_LIMIT):
    """Icons sharing a word with the feast name, most shared words first.

    Deliberately cheap and recall-oriented: Clef rejects what does not fit,
    but it never sees what this leaves out. Words of four or more letters also
    match inside run-together titles such as "StGregoryofTatevandStJohnofVorodn".
    """
    wanted = _tokens(feast)
    scored = []
    for record in records:
        texts = [record["title"], *record["tags"]]
        words = set().union(*(_tokens(text) for text in texts))
        compact = "".join(re.findall(r"\w+", unicodedata.normalize("NFKC", " ".join(texts)).casefold()))
        overlap = sum(1 for t in wanted if t in words or (len(t) >= 4 and t in compact))
        if overlap:
            scored.append((-overlap, record["id"], record))
    return [record for _, _, record in sorted(scored, key=lambda item: item[:2])[:limit]]


def icon_state(feast, record, *, with_image=False):
    tags = ", ".join(record["tags"]) or "(none)"
    state = f"Commemoration: {feast}\nIcon title: {record['title']}\nIcon tags: {tags}"
    return state + "\nThe icon's image is attached." if with_image else state


def place(probabilities, names_event):
    """Map one icon's probabilities onto the matcher's relation ladder, or None to omit it."""
    if probabilities["depicts_event"] >= RUNG_THRESHOLD:
        return "exact_event", probabilities["depicts_event"]
    if probabilities["all_figures"] >= RUNG_THRESHOLD:
        return ("subject_portrait" if names_event else "exact_subject"), probabilities["all_figures"]
    if probabilities["related"] >= RUNG_THRESHOLD:
        return "related_specific", probabilities["related"]
    return None, 0.0


def assignable(relation, strength, *, event_shortlisted=False):
    """Only a near-certain depiction is written without a human.

    A portrait of the commemorated saint for an event feast (subject_portrait)
    is written only when no shortlisted icon reached the event rung: if the
    catalogue may hold the event itself, that is the more specific depiction.
    """
    if strength < ASSIGN_THRESHOLD:
        return False
    if relation == "subject_portrait":
        return not event_shortlisted
    return relation in ("exact_event", "exact_subject")


def _confidence(strength):
    return "high" if strength >= ASSIGN_THRESHOLD else ("medium" if strength >= 0.65 else "low")


def judge_icons(icons, feast, *, candidate_ids=None, model="clef", run=None, max_results=5, image_for=None):
    """Judge a shortlist for ``feast`` and return an ``IconMatchOutcome``.

    ``candidate_ids`` supplies the shortlist (for example an LLM matcher's
    positives); without it the shortlist is ``lexical_shortlist``. ``complete``
    means every shortlisted icon was judged; icons outside the shortlist were
    screened out before Clef. Any failed Clef call makes the outcome
    ``partial``, which ``icon_tasks`` never assigns from, because the failed
    icon might have been the better match.

    ``image_for(icon_id)`` may return the icon's image as a base64 data URL;
    Clef then judges the picture as well as its title and tags. An icon whose
    image is unavailable is judged on its metadata alone.
    """
    # Checked here because worker threads would not see the request context otherwise.
    reject_public_work("llm")
    run = run or run_clef
    started = time.monotonic()
    records = serialize_catalogue(icons)
    outcome = IconMatchOutcome(catalogue_count=len(records), model=model)
    if candidate_ids is None:
        shortlist, source = lexical_shortlist(feast, records), "lexical"
    else:
        by_id = {record["id"]: record for record in records}
        shortlist, source = [by_id[i] for i in dict.fromkeys(candidate_ids) if i in by_id], "provided"
    outcome.diagnostics.append(f"clef_shortlist:{source}:{len(shortlist)}")
    if not shortlist:
        # Nothing shares a word with the feast: silence, at no cost.
        outcome.status, outcome.assessed_count, outcome.catalogue_complete = "complete", len(records), True
        outcome.elapsed_seconds = round(time.monotonic() - started, 3)
        return outcome

    def ask(state, questions, images=None):
        answers = run(state, questions, model=model, images=images) if images else run(state, questions, model=model)
        return noul_probabilities(answers, questions)

    try:
        names_event = ask(f"Commemoration: {feast}", FEAST_QUESTIONS)["names_event"] >= RUNG_THRESHOLD
    except (ClefError, RuntimeError):
        outcome.diagnostics.append("clef_feast_question_failed")
        outcome.elapsed_seconds = round(time.monotonic() - started, 3)
        return outcome
    outcome.call_count = 1

    def judge(record):
        try:
            image = image_for(record["id"]) if image_for else None
        except Exception:
            image = None
        try:
            state = icon_state(feast, record, with_image=bool(image))
            return record, ask(state, ICON_QUESTIONS, [image] if image else None), bool(image)
        except (ClefError, RuntimeError):
            return record, None, bool(image)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # One context copy per call, taken here so each worker sees this request's context.
        futures = [pool.submit(contextvars.copy_context().run, judge, record) for record in shortlist]
        judged = [future.result() for future in futures]
    outcome.call_count += len(shortlist)

    failures = sum(1 for _, probabilities, _ in judged if probabilities is None)
    if image_for:
        outcome.diagnostics.append(f"clef_images:{sum(1 for *_, imaged in judged if imaged)}/{len(judged)}")
    matches, strengths = [], {}
    for record, probabilities, _ in judged:
        if probabilities is None:
            continue
        relation, strength = place(probabilities, names_event)
        if relation is None:
            continue
        matches.append(
            {
                "id": record["id"],
                "relation": relation,
                "match_tier": relation,
                "confidence": _confidence(strength),
                "relevance": round(strength * 100),
                "reason": ", ".join(f"{name}={value:.2f}" for name, value in probabilities.items()),
                "probabilities": probabilities,
                "provenance": "clef_judge",
            }
        )
        strengths[record["id"]] = strength
    event_shortlisted = any(m["relation"] == "exact_event" for m in matches)
    for m in matches:
        m["auto_assignable"] = assignable(m["relation"], strengths[m["id"]], event_shortlisted=event_shortlisted)
    matches.sort(key=lambda m: (RANK.index(m["relation"]), -m["relevance"], m["id"]))
    outcome.matches = matches[:max_results]
    if failures:
        outcome.diagnostics.append(f"clef_icon_question_failed:{failures}")
        outcome.status = "partial"
        outcome.assessed_count = len(records) - failures
    else:
        outcome.status = "complete"
        outcome.assessed_count = len(records)
        outcome.catalogue_complete = True
    outcome.diagnostics.append(f"clef_names_event:{names_event}")
    outcome.elapsed_seconds = round(time.monotonic() - started, 3)
    return outcome
