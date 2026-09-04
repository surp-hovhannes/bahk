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

    @property
    def coverage(self) -> int:
        return len(self.matched_concepts)

    @property
    def complete_coverage(self) -> bool:
        return bool(self.requested_concepts) and set(self.matched_concepts) == set(
            self.requested_concepts
        )

    @property
    def sort_key(self):
        return (
            -TIER_ORDER[self.match_tier],
            -self.coverage,
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
    value = re.sub(r"[^a-z0-9]+", " ", value)
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
        token
        for token in normalize_text(value).split()
        if token not in GENERIC_TERMS and not token.isdigit() and len(token) >= 3
    )
    if tokens and set(tokens).issubset(IDENTITY_QUALIFIERS):
        return ()
    return tokens


def _literal_key(value: str) -> str:
    tokens = _meaningful_tokens(value)
    subjects = [token for token in tokens if token not in IDENTITY_QUALIFIERS]
    qualifiers = sorted(token for token in tokens if token in IDENTITY_QUALIFIERS)
    return f"literal:{' '.join((*subjects, *qualifiers))}"


def _registered_concept_for(value: str) -> str | None:
    normalized = normalize_text(value)
    matches = []
    for concept, definition in CONCEPTS.items():
        aliases = tuple(normalize_text(alias) for alias in definition["aliases"])
        matching = [alias for alias in aliases if _contains_phrase(normalized, alias)]
        if matching:
            matches.append((max(len(alias.split()) for alias in matching), concept))
    return max(matches)[1] if matches else None


def _split_concrete_phrases(value: str) -> tuple[str, ...]:
    """Split explicit metadata into independently coverable concepts."""
    value = unicodedata.normalize("NFKD", str(value)).casefold()

    def preserve_identity_parenthetical(match):
        parenthetical = match.group(1)
        tokens = set(normalize_text(parenthetical).split())
        return f" {parenthetical} " if tokens & IDENTITY_QUALIFIERS else " "

    value = re.sub(r"\(([^)]*)\)", preserve_identity_parenthetical, value)
    value = re.sub(r"\b(?:and|with)\b|[,:;&/+]|\.(?=\s|$)", "|", value)
    phrases = []
    for part in value.split("|"):
        tokens = _meaningful_tokens(part)
        if not tokens:
            continue
        phrase = " ".join(tokens)
        if phrase not in phrases:
            phrases.append(phrase)
    return tuple(phrases)


def _request_concepts(request: IconMatchRequest) -> tuple[RequestConcept, ...]:
    """Retain every concrete identity/event in primary text, additively."""
    registered = []
    occupied_alias_tokens: set[str] = set()
    normalized = normalize_text(request.primary_text)
    for concept, definition in CONCEPTS.items():
        matching_aliases = tuple(
            normalize_text(alias)
            for alias in definition["aliases"]
            if _contains_phrase(normalized, alias)
        )
        if matching_aliases:
            registered.append(RequestConcept(concept, tuple(definition["aliases"])))
            for alias in matching_aliases:
                occupied_alias_tokens.update(_meaningful_tokens(alias))

    concepts = {concept.key: concept for concept in registered}
    for phrase in _split_concrete_phrases(request.primary_text):
        phrase_tokens = set(_meaningful_tokens(phrase))
        if phrase_tokens and phrase_tokens.issubset(occupied_alias_tokens):
            continue
        registered_key = _registered_concept_for(phrase)
        if registered_key:
            definition = CONCEPTS[registered_key]
            concepts.setdefault(
                registered_key,
                RequestConcept(registered_key, tuple(definition["aliases"])),
            )
            continue
        key = _literal_key(phrase)
        if key != "literal:":
            concepts.setdefault(key, RequestConcept(key, (phrase,)))
    return tuple(concepts[key] for key in sorted(concepts))


def build_metadata_vocabulary(icons) -> tuple[str, ...]:
    """Build the bounded concept vocabulary exposed to optional interpretation."""
    vocabulary = set(CONCEPTS)
    for icon in icons:
        for value in (str(icon.title), *_icon_tags(icon)):
            registered = _registered_concept_for(value)
            if registered:
                vocabulary.add(registered)
            for phrase in _split_concrete_phrases(value):
                key = _literal_key(phrase)
                if key != "literal:":
                    vocabulary.add(key)
    return tuple(sorted(vocabulary))


def _recognized_themes(values: tuple[str, ...]) -> set[str]:
    normalized_values = tuple(normalize_text(value) for value in values)
    return {
        theme
        for theme, aliases in THEME_ALIASES.items()
        if any(_contains_phrase(value, alias) for value in normalized_values for alias in aliases)
    }


def _metadata_concepts(title: str, tags: tuple[str, ...]) -> dict[str, tuple[str, int]]:
    evidence = {}
    for source, value in (("title", title), *(("tag", tag) for tag in tags)):
        registered = _registered_concept_for(value)
        if registered:
            quality = 5 if source == "title" else 4
            previous = evidence.get(registered, (source, 0))
            evidence[registered] = max(previous, (source, quality), key=lambda item: item[1])
        for phrase in _split_concrete_phrases(value):
            key = _literal_key(phrase)
            if key == "literal:":
                continue
            if source == "tag" and len(key.removeprefix("literal:").split()) == 1:
                # Bare-name tags are useful search hints but not sufficient
                # identity evidence (for example ``john`` on Transfiguration).
                continue
            quality = 3 if source == "title" else 2
            previous = evidence.get(key, (source, 0))
            evidence[key] = max(previous, (source, quality), key=lambda item: item[1])
    return evidence


def _matches_request_concept(
    request_concept: RequestConcept,
    metadata: dict[str, tuple[str, int]],
    interpreted_aliases: dict[str, tuple[str, ...]],
) -> tuple[str, int] | None:
    if request_concept.key in metadata:
        return metadata[request_concept.key]
    request_alias_keys = {_literal_key(alias) for alias in request_concept.aliases}
    request_alias_keys.update(interpreted_aliases.get(request_concept.key, ()))
    for metadata_key, source_quality in metadata.items():
        if metadata_key in request_alias_keys:
            return source_quality
    return None


def generate_icon_candidates(
    icons,
    request: IconMatchRequest,
    interpreted_aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[CandidateEvidence]:
    """Generate deterministic tiered candidates with explicit request coverage."""
    interpreted_aliases = interpreted_aliases or {}
    request_concepts = _request_concepts(request)
    requested_keys = tuple(concept.key for concept in request_concepts)
    request_themes = (
        _recognized_themes((request.primary_text, *request.context_terms))
        if request.kind == "content"
        else set()
    )
    candidates = []

    for icon in icons:
        title = str(icon.title)
        tags = _icon_tags(icon)
        metadata = _metadata_concepts(title, tags)
        matched = []
        refs = []
        qualities = []
        for concept in request_concepts:
            match = _matches_request_concept(concept, metadata, interpreted_aliases)
            if match:
                source, quality = match
                matched.append(concept.key)
                refs.append(f"{source}:{concept.key}")
                qualities.append(quality)

        if matched:
            complete = len(matched) == len(requested_keys)
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

    capped = []
    for tier in ("direct_exact", "related_specific", "thematic"):
        bucket = sorted(
            (item for item in candidates if item.match_tier == tier),
            key=lambda item: item.sort_key,
        )
        capped.extend(bucket[:CANDIDATE_LIMIT_PER_TIER])
    return capped


def validate_interpreted_concepts(
    payload,
    request: IconMatchRequest,
    vocabulary: tuple[str, ...],
):
    """Validate alias-only model output against request text and bounded metadata."""
    if (
        not isinstance(payload, dict)
        or set(payload) != {"concepts"}
        or not isinstance(payload["concepts"], list)
    ):
        return {}
    request_concepts = {concept.key: concept for concept in _request_concepts(request)}
    allowed_vocabulary = set(vocabulary)
    result: dict[str, list[str]] = {}
    for item in payload["concepts"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"request_concept", "metadata_concept", "aliases"}
        ):
            return {}
        request_key = item["request_concept"]
        metadata_key = item["metadata_concept"]
        aliases = item["aliases"]
        if request_key not in request_concepts or metadata_key not in allowed_vocabulary:
            return {}
        if metadata_key != request_key:
            # The interpreter may recognize an already-bounded alias, but it
            # may not assert a new semantic equivalence between unrelated
            # request and metadata concepts.
            return {}
        negative_aliases = CONCEPTS.get(request_key, {}).get("negative_aliases", ())
        metadata_label = metadata_key.removeprefix("literal:")
        if any(_contains_phrase(metadata_label, alias) for alias in negative_aliases):
            return {}
        if (
            not isinstance(aliases, list)
            or not aliases
            or not all(isinstance(alias, str) for alias in aliases)
        ):
            return {}
        if len(aliases) != len(set(aliases)):
            return {}
        request_concept = request_concepts[request_key]
        approved_aliases = {normalize_text(alias) for alias in request_concept.aliases}
        if request_key.startswith("literal:"):
            aliases_are_approved = all(_literal_key(alias) == request_key for alias in aliases)
        else:
            aliases_are_approved = all(normalize_text(alias) in approved_aliases for alias in aliases)
        if not aliases_are_approved or not all(
            _contains_phrase(normalize_text(request.primary_text), alias) for alias in aliases
        ):
            return {}
        result.setdefault(request_key, []).append(metadata_key)
    return {key: tuple(sorted(set(values))) for key, values in result.items()}


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
    if len(set(matched_concepts)) != len(matched_concepts) or len(set(evidence_refs)) != len(
        evidence_refs
    ):
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
    if decision["rationale_code"] not in rationale_by_tier[tier]:
        return []

    highest_tier = max(candidates, key=lambda item: TIER_ORDER[item.match_tier]).match_tier
    if tier != highest_tier:
        return []

    ranked = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.match_tier == tier
            and set(candidate.matched_concepts) & set(matched_concepts)
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
            "rationale_code": decision["rationale_code"],
        }
        for candidate in ranked[:max_results]
    ]
