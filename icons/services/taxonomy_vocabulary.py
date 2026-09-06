"""Sourced meanings plus explicit metadata grammar; no calendar or fuzzy identity.

A grammatical source claim can introduce an ordinary qualified person without
registration. Arbitrary multiword labels cannot. Definitions below describe
iconographic meanings, not feasts or dates.
"""

import re
import json

from django.conf import settings

from icons.models import TaxonomyAlias, TaxonomyConcept, TaxonomyRelease, TaxonomyRelation
from icons.services.taxonomy_inputs import digest, normalize

THEMES = {
    "prayer": (["prayer", "praying"], ["praying"]),
    "charity": (["charity", "generosity", "sharing bread"], ["sharing_food", "giving_alms"]),
    "repentance": (["repentance", "penitence", "contrition"], []),
    "teaching": (["teaching", "instruction"], ["teaching"]),
    "healing": (["healing", "care for the sick"], ["caring_for_sick"]),
    "mercy": (["mercy", "compassion", "forgiveness"], ["forgiving", "comforting", "caring_for_sick"]),
    "service": (["service", "serving others"], ["washing_feet", "caring_for_sick", "giving_alms"]),
    "gratitude": (["gratitude", "thanksgiving", "thankfulness"], []),
    "humility": (["humility", "humble service"], ["washing_feet"]),
    "trust": (["trust", "faith", "reliance on god"], []),
    "hope": (["hope", "restoration"], ["comforting"]),
    "hospitality": (["hospitality", "welcoming strangers"], ["welcoming_stranger", "sharing_food"]),
    "love": (["love", "loving care"], ["comforting", "caring_for_sick"]),
    "peace": (["peace", "reconciliation"], ["reconciling"]),
    "mourning": (["mourning", "grief", "sorrow"], ["mourning"]),
}
EVENT_ACTIONS = (
    "birth",
    "nativity",
    "baptism",
    "presentation",
    "martyrdom",
    "dormition",
    "resurrection",
    "arrival",
    "departure",
    "transfiguration",
    "beheading",
    "assumption",
    "ascension",
    "annunciation",
    "visitation",
    "crucifixion",
    "burial",
    "entombment",
    "calling",
    "conversion",
)
EVENT = re.compile(r"^(?:the )?(" + "|".join(EVENT_ACTIONS) + r") of (.+)$")
NEGATION = re.compile(r"\b(not|without|except|excluding|no)\b")
SAINT = re.compile(r"^(?:saints?|sts?\.?|սուրբ|սրբոց|սբ\.?)\s+", re.I)
ROLE = r"(?:bishop|archbishop|catholicos|patriarch|apostle|evangelist|martyr|deacon|priest|archangel|abbot|elder|king|queen|prophet|վարդապետ|կաթողիկոս)"
QUALIFIER = re.compile(r"\b(?:the\s+[\w]+|of\s+(?:the\s+)?[\w]+)\b", re.I)
UNSUITABLE = {
    "companions",
    "unknown",
    "unidentified",
    "icon",
    "scene",
    "portrait",
    "holy",
    "supper",
    "feast",
    "commemoration",
    "celebration",
    "memory",
}


def release_version():
    return getattr(settings, "ICON_TAXONOMY_RELEASE", "catalogue-v2")


def release():
    if release_version() == "catalogue-v1":
        raise ValueError("legacy_release_requires_upgrade")
    return TaxonomyRelease.objects.get_or_create(
        version=release_version(),
        defaults={
            "source": {"rules": "qualified-label-grammar-v3", "calendar": False},
        },
    )[0]


def concept(kind, label, definition=None, *, language="und", source=None):
    normalized = canonical_identity(label)
    key = kind + ":" + digest(normalized)[:40]
    obj, _ = TaxonomyConcept.objects.get_or_create(
        release=release(),
        key=key,
        defaults={
            "kind": kind,
            "label": label[:500],
            "definition": definition or {},
            "source": source or {"rule": "explicit_qualified_metadata_v2"},
        },
    )
    alias(obj, label, language, source)
    return obj


def alias(obj, label, language="und", source=None):
    TaxonomyAlias.objects.get_or_create(
        concept=obj,
        language=language,
        text=label[:500],
        defaults={
            "normalized": canonical_identity(label)[:500],
            "source": source or {"rule": "exact_label"},
        },
    )


def seed_vocabulary():
    """Idempotent source definitions. Never update meanings in place on startup."""
    themes = {}
    for label, (labels, activities) in THEMES.items():
        obj = concept("theme", label, {"activities": activities}, source={"rules": "observable-themes-v2"})
        for text in labels:
            alias(obj, text, "en", {"rules": "observable-themes-v2"})
        themes[label] = obj
    source = {
        "definition_set": "biblical-iconographic-scenes-v1",
        "references": ["John 13:1-17", "Luke 22:14-20", "Luke 15:11-32", "Acts 1:6-11"],
    }
    christ = concept("subject", "Jesus Christ", {"qualified": True}, source=source)
    mary = concept(
        "subject",
        "Virgin Mary",
        {"qualified": True},
        source={"definition_set": "biblical-iconographic-scenes-v1", "references": ["Luke 1:26-38"]},
    )
    for text in ("Mother of God", "Theotokos", "Mary Mother of God", "Mary the Mother of God"):
        alias(mary, text, source={"definition_set": "christian-iconographic-titles-v1"})
    for text in ("Jesus", "Christ", "Christ Pantocrator", "Jesus Pantocrator", "Pantocrator"):
        alias(christ, text, source={"definition_set": "christian-iconographic-titles-v2"})
    apostles = concept("group", "Twelve Apostles", {"complete": False, "members": [], "qualified": True}, source=source)
    # Conjunctions of independent activity/object observations may support scene
    # suggestions. They cannot establish named participant identity or assignment.
    scenes = [
        ("Last Supper", [christ.pk, apostles.pk], ["shared_supper", "bread_and_cup"], ["gratitude", "charity"]),
        ("Washing of Feet", [christ.pk], ["washing_feet"], ["service", "humility"]),
        ("Return of the Prodigal Son", [], ["returning_son", "father_embracing_son"], ["repentance", "mercy", "love"]),
        ("Ascension of Jesus Christ", [christ.pk], ["ascending_figure", "onlookers_below"], ["hope", "trust"]),
        ("Annunciation to Virgin Mary", [mary.pk], ["winged_messenger", "woman_receiving_message"], ["trust"]),
    ]
    for label, members, observations, theme_names in scenes:
        obj = concept("event", label, {"members": members, "observations_all": observations}, source=source)
        if label == "Ascension of Jesus Christ":
            alias(obj, "Holy Ascension", source=source)
        for name in theme_names:
            TaxonomyRelation.objects.get_or_create(
                source_concept=obj, target=themes[name], kind="theme", defaults={"source": source}
            )
    return themes


# Compatibility for callers that need only a seed entry point.
seed_themes = seed_vocabulary


def descriptive_text(text):
    """Separators precede marker/role grammar; callers retain the original source."""
    return re.sub(r"[_‐‑–—-]+", " ", text)


def qualified(text, *, person_context=False):
    """Full identity grammar: qualified epithet/place/role or sourced person label.

    Two name components or a saint marker alone do not disambiguate identity.
    Explicit role/place/epithet grammar qualifies; other sourced names remain
    cautious candidates whose source strength is assessed separately.
    """
    raw = descriptive_text(text).strip()
    saint = bool(SAINT.match(raw))
    stripped = SAINT.sub("", raw)
    value = normalize(stripped)
    words = value.split()
    if re.search(
        r"\b(?:maybe|possibly|unknown|unidentified|style|denomination|orthodox|catholic|at|in|to|from|with|before|after|during)\b",
        value,
    ):
        return False
    if re.match(
        r"^(?:a|an|the|prayer|reflection|meditation|thought|request|artist|church|parish|cathedral|manuscript|collection)\b",
        value,
    ):
        return False
    if not 2 <= len(words) <= 12 or NEGATION.search(value) or set(words) & UNSUITABLE:
        return False
    if any(value.startswith(action + " ") for action in EVENT_ACTIONS):
        return False
    # Preserve the entire identity string; do not reduce to a bare name.
    proper_source = bool(stripped and stripped[0].isupper()) or saint or person_context
    if proper_source and QUALIFIER.search(stripped) and not re.match(r"^(?:the|of)\b", value):
        return True
    if re.fullmatch(ROLE + r"\s+\w+(?:\s+\w+)*", value):
        return True
    return False


def source_marked_name(text):
    text = descriptive_text(text)
    words = normalize(text).split()
    return bool(
        SAINT.match(text)
        and 1 <= len(words) <= 4
        and all(
            word.isalpha() and word not in UNSUITABLE | {"of", "the", "and", "with", "maybe", "possibly"}
            for word in words
        )
    )


def canonical_identity(text):
    value = normalize(text)
    value = re.sub(r"^(?:portrait of|icon of) ", "", value)
    value = re.sub(r" portrait$", "", value)
    # Normalize role order, retaining role and every place/epithet/ordinal.
    match = re.fullmatch(r"(" + ROLE + r") (\w+)(.*)", value)
    if match:
        value = match[2] + " the " + match[1] + match[3]
    value = re.sub(r"\bof (" + ROLE + r") of\b", r"the \1 of", value)
    value = re.sub(r"^(\w+) (" + ROLE + r") of\b", r"\1 the \2 of", value)
    return value


def source_class(text):
    text = descriptive_text(text)
    value = normalize(text)
    # Explicit relational qualifiers are identity-bearing, including places whose
    # names also occur in provenance. Role equivalence requires a sourced alias.
    if (SAINT.match(text) or re.match(r"^" + ROLE + r"\s+", value)) and qualified(text) and QUALIFIER.search(value):
        return "identity"
    # A trailing short uppercase locale/catalogue token is not a second name.
    # This is intentionally conservative without maintaining a city/state list.
    if SAINT.match(text) and re.search(r"(?:\s|[-_])[A-Z]{2,3}$", text):
        return "provenance"
    if re.search(
        r"\b(?:orthodox|catholic|church|parish|cathedral|manuscript|iconographic style|artist|photopic\w*|collection|scribe|donor)\b",
        value,
    ):
        return "provenance"
    if re.search(
        r"\b(?:" + "|".join(EVENT_ACTIONS) + r"|agony|washing|pentecost|theophany|council|vision|supper|holy family)\b",
        value,
    ):
        return "event"
    if value in {normalize(code.replace("_", " ")) for _, codes in THEMES.values() for code in codes}:
        return "activity"
    if SAINT.match(text) or qualified(text):
        return "identity"
    return "unknown"


def identity_constrained(text):
    text = descriptive_text(text).strip()
    # Explicit person markers inside prose still constrain identity.
    if re.search(r"(?:^|\s)(?:saints?|sts?\.?|սուրբ|սրբոց|սբ\.?)\s+", text, re.I):
        return True
    scoped = re.search(r"\b(?:for|about|on|of)\s+(.+)$", text, re.I)
    if scoped and qualified(scoped[1]):
        return True
    # A grammatical person claim, not any prose containing 'of' or 'the'.
    if re.match(r"^(?:a |an |the )?(?:prayer|reflection|meditation|thought|request)\b", text, re.I):
        return False
    return bool(SAINT.match(text) or qualified(text) or re.match(r"^" + ROLE + r"\s+", normalize(text)))


def resolve(text, *, kinds=None):
    qs = TaxonomyAlias.objects.filter(concept__release__version=release_version(), normalized=canonical_identity(text))
    if kinds:
        qs = qs.filter(concept__kind__in=kinds)
    found = list(TaxonomyConcept.objects.filter(pk__in=qs.values("concept_id")).order_by("pk"))
    return found[0] if len(found) == 1 else None


def parse(text, *, create=False, person_context=False):
    original = text
    value = canonical_identity(text)
    result = {
        "subjects": [],
        "event": None,
        "event_intent": None,
        "themes": [],
        "group": None,
        "unresolved": [],
        "identity_constraint": identity_constrained(original),
        "lookup_terms": [],
    }
    if NEGATION.search(value) or not value:
        result["unresolved"] = [original]
        return result
    result["lookup_terms"].append(value)
    if source_class(original) == "provenance":
        result["unresolved"] = [original]
        return result
    known = resolve(value)
    if known:
        if known.kind == "subject":
            result["subjects"], result["identity_constraint"] = [known.pk], True
        elif known.kind == "theme":
            result["themes"] = [known.pk]
        else:
            result[known.kind] = known.pk
            result["subjects"] = known.definition.get("members", [])
            result["identity_constraint"] = bool(result["subjects"]) or known.kind == "group"
            if known.kind == "event":
                result["event_intent"] = {"label": known.label, "action": known.definition.get("action", known.key)}
        return result
    # Image file separators are normalized before parsing; suffix portrait is a
    # depiction claim, not part of a saint's qualified name.
    value = re.sub(r"^(?:portrait of|icon of) ", "", value)
    value = re.sub(r" portrait$", "", value)
    event = EVENT.fullmatch(value)
    if event:
        result["event_intent"] = {"action": event[1], "label": value}
    if not event and (source_class(original) in {"event", "provenance"} or scene_hint(original, result)):
        result["unresolved"] = [original]
        if source_class(original) != "provenance":
            result["event_intent"] = {"action": "unresolved_scene", "label": value}
        return result
    subject_text = event[2] if event else value
    parts = re.split(r"\s+(?:and|և|եւ)\s+", subject_text)
    # Recover saint source markers from the original text for qualification.
    original_parts = re.split(r"\s+(?:and|և|եւ)\s+", descriptive_text(original), flags=re.I)
    for index, part in enumerate(parts):
        result["lookup_terms"].append(normalize(part))
        part = canonical_identity(part)
        found = resolve(part, kinds=["subject", "group"])
        source_text = original_parts[index] if index < len(original_parts) else part
        source_text = re.sub(r"^(?:portrait of|icon of) ", "", source_text, flags=re.I)
        source_text = re.sub(r" portrait$", "", source_text, flags=re.I)
        if event:
            source_text = re.sub(r"^.*?\bof\s+", "", source_text, count=1, flags=re.I)
        if not found and create and (qualified(source_text) or source_marked_name(source_text)):
            found = concept(
                "subject", part, {"qualified": qualified(source_text), "source_marked": bool(SAINT.match(source_text))}
            )
        if found:
            result["subjects"].append(found.pk)
            result["identity_constraint"] = True
        else:
            result["unresolved"].append(part)
    result["subjects"] = sorted(set(result["subjects"]))
    if not result["unresolved"] and result["subjects"]:
        if event and create:
            result["event"] = concept("event", value, {"members": result["subjects"], "action": event[1]}).pk
        elif len(parts) > 1 and create:
            group = concept("group", value, {"members": result["subjects"], "complete": True})
            result["group"] = group.pk
            for member in result["subjects"]:
                TaxonomyRelation.objects.get_or_create(
                    source_concept=group,
                    target_id=member,
                    kind="member",
                    defaults={"source": {"rule": "explicit_conjunction_v1"}},
                )
    return result


def scene_hint(text, parsed):
    """Retain event/scene claims without inventing a canonical event identity.

    Open-ended scene labels and action descriptions are distinct from ordinary
    search tags. Unrecognized wording stays available to contextual comparison.
    """
    value = normalize(text)
    return bool(
        parsed["event_intent"]
        or re.search(r"\b(?:" + "|".join(EVENT_ACTIONS) + r")\b", value)
        or re.search(r"\b(?:scene|event|episode|narrative|miracle|procession)\b", value)
        or re.match(r"^(?:depicts|depicting)\s+", value)
        or re.search(r"\b\w+ing\s+.+\b(?:across|into|through|towards?|over)\b", value)
        or re.search(r"\b\w+ing\s+(?:across|into|through|towards?|over)\b", value)
    )


def catalogue_sources(metadata, *, create=False):
    """Whole bounded sources, parsed spans and the exact alias lookup footprint."""
    raw = [("title", metadata["title"])] + [("tag", tag) for tag in metadata["tags"]]
    if metadata["filename"]:
        raw.append(("filename", metadata["filename"]))
    if len(raw) > 64 or len(json.dumps(raw, ensure_ascii=False).encode()) > 16000:
        raise ValueError("metadata_sources_too_large")
    sources = []
    for ref, original in raw:
        text, transformations = original, []
        if ref == "filename":
            for pattern, code in (
                (r"\.(?:png|jpe?g|webp|gif)$", "file_extension"),
                (r"_[A-Za-z0-9]{7}$", "storage_random_suffix"),
                (r"[-_]photopic[a-zA-Z0-9]+$", "product_suffix"),
            ):
                cleaned = re.sub(pattern, "", text)
                if cleaned != text:
                    transformations.append(code)
                    text = cleaned
            text = text.replace("_", " ").replace("-", " ")
        # Weak filenames resolve existing aliases but never mint identities.
        parsed = parse(text, create=create and ref != "filename")
        sources.append(
            {
                "source": ref,
                "classification": source_class(text),
                "identity_text": canonical_identity(text),
                "qualifiers": re.findall(r"\b(?:the|of)\s+[^,;]+", normalize(text)),
                "transformations": transformations,
                "weak": ref == "filename",
                "text": original,
                "parse_text": text,
                "parsed": parsed,
                "scene_hint": scene_hint(text, parsed),
            }
        )
    # Reject oversize data instead of silently dropping qualifiers/source spans.
    if len(json.dumps(sources, ensure_ascii=False).encode()) > 48000:
        raise ValueError("metadata_sources_too_large")
    return sources


def catalogue_claims(metadata):
    seed_vocabulary()
    claims = []
    for source in catalogue_sources(metadata, create=True):
        parsed = source["parsed"]
        for kind in ("subjects", "themes"):
            claims.extend({"concept": pk, "source": source["source"], "text": source["text"]} for pk in parsed[kind])
        for kind in ("event", "group"):
            if parsed[kind]:
                claims.append({"concept": parsed[kind], "source": source["source"], "text": source["text"]})
    return claims
