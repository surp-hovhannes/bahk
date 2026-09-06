"""Pure candidate generation and validation for devotional icon matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

MatchTier = Literal["direct_exact", "related_specific", "thematic", "no_match"]
MatchConfidence = Literal["high", "medium", "low", "none"]

TIER_CONFIDENCE = {
    "direct_exact": "high",
    "related_specific": "medium",
    "thematic": "low",
    "no_match": "none",
}
TIER_ORDER = {"direct_exact": 3, "related_specific": 2, "thematic": 1, "no_match": 0}
CANDIDATE_LIMIT_PER_TIER = 20


@dataclass(frozen=True)
class IconMatchRequest:
    """Caller context for a read-only icon recommendation."""

    kind: Literal["feast", "content"]
    primary_text: str
    context_terms: tuple[str, ...] = ()
    auto_assign_policy: Literal["feast_strict", "content_suggest", "none"] = "none"
    max_results: int = 1


@dataclass(frozen=True)
class RequestConcept:
    key: str
    aliases: tuple[str, ...]
    events: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateEvidence:
    icon_id: int
    title: str
    tags: tuple[str, ...]
    match_tier: MatchTier
    matched_concepts: tuple[str, ...]
    requested_concepts: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    evidence_quality: int
    specificity: int = 0

    @property
    def coverage(self) -> int:
        return len(self.matched_concepts)

    @property
    def complete_coverage(self) -> bool:
        return bool(self.requested_concepts) and set(self.matched_concepts) == set(self.requested_concepts)

    @property
    def sort_key(self):
        return (
            -TIER_ORDER[self.match_tier],
            -self.coverage,
            -self.specificity,
            -self.evidence_quality,
            normalize_text(self.title),
            self.icon_id,
        )


# Auditable aliases and non-direct relations that cannot be derived from metadata.
CONCEPTS = {
    "john_the_baptist": {
        "aliases": (
            "john the baptist",
            "john baptist",
            "john the forerunner",
            "john forerunner",
            "st john the baptist",
            "st john the forerunner",
            "john forerunner baptist",
        ),
        "negative_aliases": (
            "john apostle",
            "john the apostle",
            "john evangelist",
            "john the evangelist",
        ),
    },
    "holy_translators": {
        "aliases": ("holy translators", "holy translator", "tarkmanchatz"),
        "related": {
            # Mesrop created the Armenian alphabet and is a principal Holy Translator.
            "mesrop_mashtots": "principal_holy_translator",
        },
    },
    "mesrop_mashtots": {
        "aliases": (
            "mesrop mashtots",
            "mesrob mashtots",
            "st mesrop mashtots",
            "st mesrob mashtots",
            "saint mesrop mashtots",
            "saint mesrob mashtots",
        ),
    },
    "prodigal_son": {
        "aliases": ("prodigal son", "the prodigal son"),
        "themes": ("repentance",),
    },
}

THEME_ALIASES = {
    "repentance": (
        "penitential",
        "penitence",
        "repentance",
        "repentant",
        "repent",
        "contrition",
    ),
}

GENERIC_TERMS = frozenset(
    {
        "and",
        "companions",
        "day",
        "fast",
        "feast",
        "group",
        "holy",
        "icon",
        "of",
        "prayer",
        "saint",
        "saints",
        "single",
        "st",
        "the",
        "with",
    }
)

IDENTITY_QUALIFIERS = frozenset(
    {
        "apostle",
        "apostles",
        "baptist",
        "bishop",
        "catholicos",
        "deacon",
        "evangelist",
        "forerunner",
        "illuminator",
        "king",
        "martyr",
        "martyrs",
        "patriarch",
        "priest",
        "righteous",
        "virgin",
    }
)


def normalize_text(value: str) -> str:
    """Normalize metadata without creating substring matches."""
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = re.sub(r"\b(?:saints|saint|sts?)\.?\b", "st", value)
    value = "".join(character if character.isalnum() else " " for character in value)
    return " ".join(value.split())


def _icon_tags(icon) -> tuple[str, ...]:
    tags = getattr(icon, "tags", ())
    if hasattr(tags, "all"):
        tags = tags.all()
    return tuple(str(getattr(tag, "name", tag)) for tag in tags)


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = normalize_text(phrase)
    return bool(normalized_phrase) and f" {normalized_phrase} " in f" {text} "


def _meaningful_tokens(value: str) -> tuple[str, ...]:
    tokens = tuple(
        token for token in normalize_text(value).split() if token not in GENERIC_TERMS and not token.isdigit()
    )
    return tokens


def _literal_key(value: str) -> str:
    tokens = tuple(REVIEWED_NAME_ALIASES.get(token, token) for token in _meaningful_tokens(value))
    subjects = [token for token in tokens if token not in IDENTITY_QUALIFIERS]
    qualifiers = sorted(token for token in tokens if token in IDENTITY_QUALIFIERS)
    return f"literal:{' '.join((*subjects, *qualifiers))}"


# Reviewed spelling equivalence for the Armenian saint's name. Qualifiers are
# retained in the key: Vardan the Warrior never becomes Vardan of another place.
# No other transliterations or first-name equivalences are inferred.
REVIEWED_NAME_ALIASES = {"vardan": "vartan"}
EVENT_ALIASES = {
    "beheading": ("beheading", "decollation"),
    "nativity": ("nativity", "birth"),
}


def _registered_concept_for(value: str) -> str | None:
    key = _literal_key(value)
    for concept, definition in CONCEPTS.items():
        if any(key == _literal_key(alias) for alias in definition["aliases"]):
            return concept
    return None


def _split_concrete_phrases(value: str) -> tuple[str, ...]:
    """Preserve parenthetical identities and every Unicode subject clause."""
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    # Honorific periods are not clause boundaries. Other punctuation separates
    # explicit subjects/metadata; parentheses only group, never erase, text.
    value = re.sub(r"\b(sts?|saints?)\.", r"\1", value)
    value = re.sub(r"[()]", " ", value)
    value = re.sub(r"\b(?:and|with)\b|[,:;&/+]|\.(?=\s|$)", "|", value)
    return tuple(dict.fromkeys(" ".join(tokens) for part in value.split("|") if (tokens := _meaningful_tokens(part))))


def _phrase_concept(phrase: str) -> RequestConcept:
    tokens = list(_meaningful_tokens(phrase))
    events = tuple(sorted(event for event, aliases in EVENT_ALIASES.items() if set(tokens) & set(aliases)))
    subject = " ".join(token for token in tokens if not any(token in aliases for aliases in EVENT_ALIASES.values()))
    # Standalone events remain concrete concepts rather than empty subjects.
    if not subject:
        subject = " ".join(events)
        events = ()
    registered = _registered_concept_for(subject)
    key = registered or _literal_key(subject)
    return RequestConcept(key, (subject,), events)


def _request_concepts(request: IconMatchRequest) -> tuple[RequestConcept, ...]:
    concepts = {}
    for phrase in _split_concrete_phrases(request.primary_text):
        # Content themes are context, not additional saint identities.
        if request.kind == "content":
            theme_words = {word for aliases in THEME_ALIASES.values() for word in aliases}
            phrase = " ".join(word for word in phrase.split() if word not in theme_words)
            if not phrase:
                continue
        concept = _phrase_concept(phrase)
        previous = concepts.get(concept.key)
        if previous:
            concept = RequestConcept(concept.key, concept.aliases, tuple(sorted(set(previous.events + concept.events))))
        concepts[concept.key] = concept
    return tuple(concepts[key] for key in sorted(concepts))


def _recognized_themes(values: tuple[str, ...]) -> set[str]:
    normalized_values = tuple(normalize_text(value) for value in values)
    return {
        theme
        for theme, aliases in THEME_ALIASES.items()
        if any(_contains_phrase(value, alias) for value in normalized_values for alias in aliases)
    }


def _metadata_concepts(title: str, tags: tuple[str, ...], requested_keys: tuple[str, ...]):
    evidence = {}
    title_concepts = [_phrase_concept(phrase) for phrase in _split_concrete_phrases(title)]
    title_tokens = [
        set(_literal_key(concept.aliases[0]).removeprefix("literal:").split()) for concept in title_concepts
    ]
    events_by_key = {}
    for source, value in (("title", title), *(("tag", tag) for tag in tags)):
        for phrase in _split_concrete_phrases(value):
            concept = _phrase_concept(phrase)
            key = concept.key
            if source == "tag":
                tokens = set(_literal_key(concept.aliases[0]).removeprefix("literal:").split()) - IDENTITY_QUALIFIERS
                # Search tags cannot erase a title's distinguishing identity or
                # unknown event modifier, even when the tag is a registered alias.
                if any(
                    tokens & title_words and other.key != key
                    for other, title_words in zip(title_concepts, title_tokens)
                ):
                    continue
                if key.startswith("literal:") and len(tokens) == 1:
                    continue
            quality = (5 if source == "title" else 4) if key in CONCEPTS else (3 if source == "title" else 2)
            if quality > evidence.get(key, ("", 0))[1]:
                evidence[key] = (source, quality)
            events_by_key.setdefault(key, set()).update(concept.events)
    # Portrait fallback needs affirmative title identity evidence. A saint search
    # tag on an unknown scene is insufficient, and any event metadata prevents
    # us from reclassifying that depiction as a generic portrait.
    all_events = set()
    for value in (title, *tags):
        words = set(normalize_text(value).split())
        all_events.update(event for event, aliases in EVENT_ALIASES.items() if words & set(aliases))
    # Every title clause must describe a requested subject. Multiword tags also
    # constrain the depiction; one-word search hints do not establish a scene.
    # This check is limited to portrait fallback, preserving ordinary related
    # matches on titles that include artist metadata.
    portrait_supported = all(concept.key in requested_keys for concept in title_concepts) and all(
        _phrase_concept(phrase).key in requested_keys
        for tag in tags
        for phrase in _split_concrete_phrases(tag)
        if len(_meaningful_tokens(phrase)) > 1
    )
    portraits = {concept.key for concept in title_concepts} if portrait_supported and not all_events else set()
    return evidence, events_by_key, portraits, all_events


def generate_icon_candidates(
    icons,
    request: IconMatchRequest,
    interpreted_aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[CandidateEvidence]:
    """Generate deterministic tiered candidates with explicit request coverage."""
    # Kept as a compatibility argument; model-inferred identities are not trusted.
    request_concepts = _request_concepts(request)
    requested_keys = tuple(concept.key for concept in request_concepts)
    requested_events = {event for concept in request_concepts for event in concept.events}
    request_themes = (
        _recognized_themes((request.primary_text, *request.context_terms)) if request.kind == "content" else set()
    )
    candidates = []

    for icon in icons:
        title = str(icon.title)
        tags = _icon_tags(icon)
        metadata, depiction_events, portraits, all_events = _metadata_concepts(title, tags, requested_keys)
        # Check all metadata before matching: a saint-specific event tag cannot
        # override a contradictory event in the title or another tag.
        if requested_events and all_events - requested_events:
            continue
        event_exact = True
        matched = []
        refs = []
        qualities = []
        for concept in request_concepts:
            match = metadata.get(concept.key)
            if concept.events:
                actual_events = depiction_events.get(concept.key, set())
                if actual_events and actual_events != set(concept.events):
                    match = None
                if not actual_events:
                    event_exact = False
                    if concept.key not in portraits:
                        match = None
            if match:
                source, quality = match
                matched.append(concept.key)
                refs.append(f"{source}:{concept.key}")
                qualities.append(quality)

        if matched:
            complete = len(matched) == len(requested_keys)
            has_event_evidence = any(concept.events and concept.key in matched for concept in request_concepts)
            specificity = 2 if has_event_evidence and event_exact else 1 if has_event_evidence else 0
            if has_event_evidence:
                refs.append("depiction:event_exact" if event_exact else "depiction:subject_portrait")
            candidates.append(
                CandidateEvidence(
                    icon.id,
                    title,
                    tags,
                    "direct_exact" if complete else "related_specific",
                    tuple(sorted(matched)),
                    requested_keys,
                    tuple(sorted(refs)),
                    min(qualities),
                    specificity,
                )
            )
            continue

        icon_registered = set(metadata) & set(CONCEPTS)
        related = sorted(
            icon_concept
            for request_concept in requested_keys
            if request_concept in CONCEPTS
            for icon_concept in CONCEPTS[request_concept].get("related", {})
            if icon_concept in icon_registered
        )
        if related:
            candidates.append(
                CandidateEvidence(
                    icon.id,
                    title,
                    tags,
                    "related_specific",
                    tuple(related),
                    requested_keys,
                    tuple(f"relation:{item}" for item in related),
                    2,
                )
            )
            continue

        thematic = sorted(
            icon_concept
            for icon_concept in icon_registered
            if request_themes & set(CONCEPTS[icon_concept].get("themes", ()))
        )
        if thematic:
            candidates.append(
                CandidateEvidence(
                    icon.id,
                    title,
                    tags,
                    "thematic",
                    tuple(thematic),
                    requested_keys,
                    tuple(f"theme:{theme}" for theme in sorted(request_themes)),
                    1,
                )
            )

    candidates = _prefer_event_depictions(candidates)
    capped = []
    for tier in ("direct_exact", "related_specific", "thematic"):
        bucket = sorted(
            (item for item in candidates if item.match_tier == tier),
            key=lambda item: item.sort_key,
        )
        capped.extend(bucket[:CANDIDATE_LIMIT_PER_TIER])
    return capped


def _prefer_event_depictions(candidates):
    if any(item.match_tier == "direct_exact" and item.specificity == 2 for item in candidates):
        return [item for item in candidates if not (item.match_tier == "direct_exact" and item.specificity == 1)]
    return candidates


def _candidate_rationale(candidate):
    if candidate.match_tier == "direct_exact":
        if candidate.specificity == 2:
            return "explicit_event"
        return "full_composite" if candidate.coverage > 1 else "explicit_subject"
    return {"related_specific": "specific_related_subject", "thematic": "defensible_theme"}[candidate.match_tier]


def validate_and_rank_decision(payload, candidates, max_results=1):
    """Strictly validate one model decision, then rank equivalent choices in Python."""
    if not candidates or not isinstance(payload, dict) or set(payload) != {"decision"}:
        return []
    decision = payload["decision"]
    required = {
        "id",
        "match_tier",
        "confidence",
        "matched_concepts",
        "evidence_refs",
        "rationale_code",
    }
    if not isinstance(decision, dict) or set(decision) != required:
        return []

    tier = decision["match_tier"]
    confidence = decision["confidence"]
    if not isinstance(tier, str) or tier not in TIER_CONFIDENCE:
        return []
    if not isinstance(confidence, str) or confidence != TIER_CONFIDENCE[tier]:
        return []
    if tier == "no_match" or type(decision["id"]) is not int:
        return []

    by_id = {candidate.icon_id: candidate for candidate in candidates}
    selected = by_id.get(decision["id"])
    if selected is None or selected.match_tier != tier:
        return []
    matched_concepts = decision["matched_concepts"]
    evidence_refs = decision["evidence_refs"]
    if not isinstance(matched_concepts, list) or not isinstance(evidence_refs, list):
        return []
    if not all(isinstance(item, str) for item in (*matched_concepts, *evidence_refs)):
        return []
    if len(set(matched_concepts)) != len(matched_concepts) or len(set(evidence_refs)) != len(evidence_refs):
        return []
    if not set(matched_concepts).issubset(selected.matched_concepts):
        return []
    if not set(evidence_refs).issubset(selected.evidence_refs):
        return []
    if not matched_concepts or not evidence_refs:
        return []

    rationale_by_tier = {
        "direct_exact": {"explicit_subject", "explicit_event", "full_composite"},
        "related_specific": {"specific_related_subject", "specific_related_event"},
        "thematic": {"defensible_theme"},
    }
    if not isinstance(decision["rationale_code"], str) or decision["rationale_code"] not in rationale_by_tier[tier]:
        return []

    highest_tier = max(candidates, key=lambda item: TIER_ORDER[item.match_tier]).match_tier
    if tier != highest_tier:
        return []

    candidates = _prefer_event_depictions(candidates)
    ranked = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.match_tier == tier and set(candidate.matched_concepts) & set(matched_concepts)
        ),
        key=lambda item: item.sort_key,
    )
    return [
        {
            "id": candidate.icon_id,
            "match_tier": candidate.match_tier,
            "confidence": TIER_CONFIDENCE[candidate.match_tier],
            "matched_concepts": list(candidate.matched_concepts),
            "evidence_refs": list(candidate.evidence_refs),
            "rationale_code": _candidate_rationale(candidate),
        }
        for candidate in ranked[:max_results]
    ]
