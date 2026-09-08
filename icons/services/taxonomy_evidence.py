"""Bounded deterministic interpretation of retained evidence, including legacy wire data."""

import re
from copy import deepcopy

from icons.services.taxonomy_inputs import normalize

ACTIVITY_CODES = (
    "unknown",
    "other",
    "praying",
    "sharing_food",
    "giving_alms",
    "teaching",
    "caring_for_sick",
    "comforting",
    "washing_feet",
    "welcoming_stranger",
    "mourning",
    "kneeling",
    "bowed_posture",
    "blessing",
    "shared_supper",
    "bread_and_cup",
    "returning_son",
    "father_embracing_son",
    "ascending_figure",
    "onlookers_below",
    "winged_messenger",
    "woman_receiving_message",
)
UNCERTAIN = re.compile(
    r"\b(?:uncertain|unreadable|illegible|perhaps|possibly|possible|maybe|appears|seems|partial(?:ly)?|fragment(?:ary)?|unclear|resembles?|not legible|not reliably)\b",
    re.I,
)


def inscription_spans(obs):
    if obs.get("kind") != "inscription" or not obs.get("readable") or obs.get("uncertain", False):
        return []
    if UNCERTAIN.search(obs["text"] + " " + obs.get("region", "")):
        return []
    if "literal_spans" in obs:
        return obs["literal_spans"][:16]
    # No transliteration or substring name guesses. Quoted literal spans are the
    # only narrative fallback; preserve unresolved text as uncertainty elsewhere.
    if UNCERTAIN.search(obs["text"]):
        return []
    spans = re.findall(r'“([^”]+)”|«([^»]+)»|"([^"]+)"|‘([^’]+)’', obs["text"])
    return [next(s for s in group if s) for group in spans][:16] or [obs["text"]]


def observation_codes(obs):
    if obs["kind"] not in {"activity", "object", "depiction"} or obs.get("uncertain", False):
        return set()
    text, region = obs["text"], obs.get("region", "")
    if UNCERTAIN.search(text + " " + region) or re.search(r"\b(?:not|without|no)\b", text + " " + region, re.I):
        return set()
    # Never count a repeated rule code as its own supporting description. Legacy
    # coded responses need literal support in the region, just like fresh ones.
    requested = obs.get("code", text if text in ACTIVITY_CODES else None)
    description = normalize(("" if text in ACTIVITY_CODES else text) + " " + region)
    foot = r"(?:(?:the|a|one|his|her|their|raised|bare|left|right|another|other|seated|figure|person|man|disciple|s) ){0,8}(?:feet|foot)"
    patterns = {
        "washing_feet": (
            r"\bwash(?:es|ing|ed)? " + foot + r"\b",
            r"\bpour(?:s|ing|ed)? water (?:from (?:a |the )?(?:vessel|pitcher|jug) )?(?:over|onto|on) " + foot + r"\b",
            r"\b(?:feet|foot) (?:is |are |being )*(?:washed|bathed)\b",
            r"\bwater (?:is |being )*poured (?:over|onto|on) " + foot + r"\b",
        ),
        "praying": (
            r"\bhands (?:are )?(?:joined|clasped|folded) (?:together )?in prayer\b",
            r"\b(?:raised open hands|open raised hands|hands raised) and (?:an? )?upward facing posture\b",
            r"\bpraying\b.*\b(?:hands|knees|kneeling)\b",
        ),
        "kneeling": (r"\bkneel(?:s|ing|ed)?\b",),
        "bowed_posture": (r"\b(?:head|figure|body) (?:is )?bowed\b",),
        "sharing_food": (r"\bshar(?:e|es|ing) (?:food|bread|a meal) (?:with|among)\b", r"\bgiv(?:e|es|ing) bread to\b"),
        "giving_alms": (r"\bgiv(?:e|es|ing) (?:coins|alms) to\b",),
        "comforting": (r"\b(?:embrac(?:e|es|ing)|holding)\b.*\b(?:another|shoulders|weeping|crying)\b",),
        "caring_for_sick": (
            r"\b(?:bandag(?:e|es|ing)|dress(?:es|ing) (?:a |the )?wound|tend(?:s|ing) (?:a |the )?sick)\b",
        ),
        "welcoming_stranger": (
            r"\b(?:welcom(?:e|es|ing)|receiv(?:e|es|ing)) (?:a |the )?(?:traveler|stranger|visitor)\b",
        ),
        "mourning": (
            r"\b(?:weep(?:s|ing)?|cry(?:ing|ies)) (?:beside|over|around) (?:a |the )?(?:body|coffin|grave)\b",
        ),
        "teaching": (
            r"\b(?:read(?:s|ing)|point(?:s|ing)) (?:from |to )?(?:an? |the )?(?:open )?(?:scroll|book) (?:to|before) (?:the |a )?(?:group|listeners|pupils)\b",
        ),
        "blessing": (r"\b(?:raised|raises)\b.*\bhand\b.*\bblessing\b",),
        "returning_son": (r"\b(?:son|younger figure) (?:returns|approaches) (?:his father|an older figure)\b",),
        "father_embracing_son": (r"\b(?:father|older figure) (?:embraces|holds) (?:his son|the younger)\b",),
        "ascending_figure": (r"\b(?:elevated|ascending|rising) figure\b.*\b(?:above|upward|clouds)\b",),
        "onlookers_below": (r"\bfigures? (?:look|looks|gaze|gazes) (?:upward|up|toward the elevated)\b",),
        "winged_messenger": (r"\bwinged figure\b.*\b(?:approaches|addresses|gestures toward)\b",),
        "woman_receiving_message": (r"\bwoman\b.*\b(?:faces|turns toward|listens to)\b.*\bwinged figure\b",),
    }
    codes = {
        code
        for code, alternatives in patterns.items()
        if any(re.search(pattern, description) for pattern in alternatives)
    }
    if (
        re.search(r"\b(?:figures|people|diners|group)\b", description)
        and re.search(r"\b(?:share|sharing|gathered|sit|seated|together)\b", description)
        and re.search(r"\btable\b", description)
        and re.search(r"\b(?:food|bread|loaves|meal|supper|eating)\b", description)
    ):
        codes.add("shared_supper")
    if re.search(r"\b(?:bread|loaves)\b", description) and re.search(r"\b(?:cups?|chalices?)\b", description):
        codes.add("bread_and_cup")
    if "onlookers_below" in codes and not re.search(r"\b(?:below|lower)\b", description):
        codes.remove("onlookers_below")
    if requested is not None:
        codes &= {requested}
    return codes


def bounded_comparison(value):
    from icons.services.vision_provider import bounded_size

    bounded_size(value)
    if not isinstance(value, dict) or set(value) != {"assertions"} or not isinstance(value["assertions"], (list, dict)):
        raise ValueError("malformed_comparison")


def normalize_comparison(claims, observation, comparison):
    """Preserve valid entries; conflicting duplicates poison only their claim."""
    from icons.services.vision_provider import COMPARISON_SCHEMA, bounded_validate

    bounded_comparison(comparison)
    allowed = {c["concept"] for c in claims}
    observed = {o["id"] for o in observation["observations"]}
    accepted, signatures, conflicted, diagnostics = {}, {}, set(), []
    entries = comparison["assertions"]
    if isinstance(entries, dict):
        entries = [
            (
                {**raw, "concept": int(key)}
                if isinstance(raw, dict) and "concept" not in raw and isinstance(key, str) and key.isdecimal()
                else {"malformed_keyed_entry": raw}
            )
            for key, raw in entries.items()
        ]
    for index, raw in enumerate(entries):
        try:
            bounded_validate(raw, COMPARISON_SCHEMA["properties"]["assertions"]["items"])
        except ValueError:
            diagnostics.append({"index": index, "code": "malformed_comparison_entry"})
            continue
        pk = raw["concept"]
        code = "accepted_comparison_entry"
        if pk not in allowed:
            code = "unknown_comparison_concept"
        elif not set(raw["observation_ids"]) <= observed:
            code = "unknown_comparison_observation"
        else:
            signature = (raw["agrees"], raw["conflict"], tuple(sorted(set(raw["observation_ids"]))))
            if pk in signatures:
                if signature != signatures[pk]:
                    conflicted.add(pk)
                    code = "conflicting_comparison_duplicate"
                else:
                    code = "coalesced_comparison_duplicate"
            else:
                signatures[pk] = signature
                accepted[pk] = deepcopy(raw)
                accepted[pk]["observation_ids"] = list(signature[2])
        diagnostics.append({"index": index, "code": code})
    for pk in conflicted:
        accepted.pop(pk, None)
    for diagnostic in diagnostics:
        raw = entries[diagnostic["index"]]
        pk = raw.get("concept") if isinstance(raw, dict) else None
        diagnostic["disposition"] = (
            "claim_unknown"
            if type(pk) is int and pk in conflicted
            else "accepted"
            if diagnostic["code"] == "accepted_comparison_entry"
            else "coalesced"
            if diagnostic["code"] == "coalesced_comparison_duplicate"
            else "discarded"
        )
    return accepted, conflicted, diagnostics


# Deliberately empty: adding a person signature requires separately sourced,
# reviewed feature definitions and collision tests. Activity codes are generic
# scene evidence and are never identity-distinctive features by themselves.
DISTINCTIVE_FEATURES = {}


def signature_support(vocabulary, observations):
    """Only sourced, unique conjunctions of distinctive attributes can qualify.

    No built-in person signature is asserted without a reviewed source. Generic
    halos/vestments never qualify. Colliding signatures suppress every candidate.
    """
    visible = {}
    for obs in observations.values():
        description = normalize(obs["text"])
        if (
            obs["kind"] not in {"object", "depiction"}
            or obs.get("uncertain", False)
            or UNCERTAIN.search(description)
            or re.search(r"\b(?:not|no|without)\b", description)
        ):
            continue
        for code, pattern in DISTINCTIVE_FEATURES.items():
            if re.search(pattern, description):
                visible.setdefault(code, []).append(obs)
    matched = {}
    for pk, c in vocabulary.items():
        for signature in c.definition.get("visual_signatures", []):
            codes = set(signature.get("all", []))
            if (
                c.kind == "subject"
                and signature.get("source")
                and len(codes) >= 2
                and codes <= DISTINCTIVE_FEATURES.keys()
                and codes <= visible.keys()
            ):
                matched[pk] = [o for code in sorted(codes) for o in visible[code]]
    return matched if len(matched) == 1 else {}
