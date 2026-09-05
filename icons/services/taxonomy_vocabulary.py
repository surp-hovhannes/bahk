"""Sourced meanings plus explicit metadata grammar; no calendar or fuzzy identity.

A grammatical source claim can introduce an ordinary qualified person without
registration. Arbitrary multiword labels cannot. Definitions below describe
iconographic meanings, not feasts or dates.
"""

import re

from django.conf import settings

from icons.models import TaxonomyAlias, TaxonomyConcept, TaxonomyRelease, TaxonomyRelation
from icons.services.taxonomy_inputs import digest, normalize

THEMES = {
    "prayer": (["prayer", "praying"], ["praying"]),
    "charity": (["charity", "generosity", "sharing bread"], ["sharing_food", "giving_alms"]),
    "repentance": (["repentance", "penitence", "contrition"], ["kneeling_in_repentance"]),
    "teaching": (["teaching", "instruction"], ["teaching"]),
    "healing": (["healing", "care for the sick"], ["caring_for_sick"]),
    "mercy": (["mercy", "compassion", "forgiveness"], ["forgiving", "comforting", "caring_for_sick"]),
    "service": (["service", "serving others"], ["washing_feet", "caring_for_sick", "giving_alms"]),
    "gratitude": (["gratitude", "thanksgiving", "thankfulness"], ["giving_thanks"]),
    "humility": (["humility", "humble service"], ["washing_feet"]),
    "trust": (["trust", "faith", "reliance on god"], ["entrusting_to_god"]),
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
ROLE = r"(?:bishop|archbishop|catholicos|patriarch|apostle|evangelist|martyr|deacon|abbot|elder|king|queen|prophet|վարդապետ|կաթողիկոս)"
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
    return getattr(settings, "ICON_TAXONOMY_RELEASE", "catalogue-v1")


def release():
    return TaxonomyRelease.objects.get_or_create(
        version=release_version(),
        defaults={
            "source": {"rules": "qualified-label-grammar-v2", "calendar": False},
        },
    )[0]


def concept(kind, label, definition=None, *, language="und", source=None):
    normalized = normalize(label)
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
            "normalized": normalize(label)[:500],
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
    for text in ("Mother of God", "Theotokos"):
        alias(mary, text, source={"definition_set": "christian-iconographic-titles-v1"})
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


def qualified(text, *, person_context=False):
    """Full identity grammar: qualified epithet/place/role or sourced person label.

    Two arbitrary words are never sufficient. A saint/person source marker plus
    two name components is accepted; a bare Saint Peter remains unqualified.
    """
    raw = text.strip()
    saint = bool(SAINT.match(raw))
    stripped = SAINT.sub("", raw)
    value = normalize(stripped)
    words = value.split()
    if re.match(r"^(?:a|an|the|prayer|reflection|meditation|thought|request)\b", value):
        return False
    if not 2 <= len(words) <= 12 or NEGATION.search(value) or set(words) & UNSUITABLE:
        return False
    if any(value.startswith(action + " ") for action in EVENT_ACTIONS):
        return False
    # Preserve the entire identity string; do not reduce to a bare name.
    proper_source = bool(stripped and stripped[0].isupper()) or saint or person_context
    if proper_source and QUALIFIER.search(stripped) and not re.match(r"^(?:the|of)\b", value):
        return True
    if re.match(r"^" + ROLE + r"\s+\w+\s+\w+", value):
        return True
    if saint or person_context:
        # At least two explicit name components, without generic label words.
        return all(w not in {"of", "the", "and", "saint", "saints"} for w in words)
    return False


def identity_constrained(text):
    text = text.strip()
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
    qs = TaxonomyAlias.objects.filter(concept__release__version=release_version(), normalized=normalize(text))
    if kinds:
        qs = qs.filter(concept__kind__in=kinds)
    found = list(TaxonomyConcept.objects.filter(pk__in=qs.values("concept_id")).order_by("pk"))
    return found[0] if len(found) == 1 else None


def parse(text, *, create=False, person_context=False):
    original = text
    value = normalize(text)
    result = {
        "subjects": [],
        "event": None,
        "event_intent": None,
        "themes": [],
        "group": None,
        "unresolved": [],
        "identity_constraint": identity_constrained(original),
    }
    if NEGATION.search(value) or not value:
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
    subject_text = event[2] if event else value
    parts = re.split(r"\s+(?:and|և|եւ)\s+", subject_text)
    # Recover saint source markers from the original text for qualification.
    original_parts = re.split(r"\s+(?:and|և|եւ)\s+", original, flags=re.I)
    for index, part in enumerate(parts):
        found = resolve(part, kinds=["subject", "group"])
        source_text = original_parts[index] if index < len(original_parts) else part
        source_text = re.sub(r"^(?:portrait of|icon of) ", "", source_text, flags=re.I)
        source_text = re.sub(r" portrait$", "", source_text, flags=re.I)
        if event:
            source_text = re.sub(r"^.*?\bof\s+", "", source_text, count=1, flags=re.I)
        if (
            not found
            and create
            and qualified(source_text, person_context=person_context or bool(SAINT.match(source_text)))
        ):
            found = concept("subject", part, {"qualified": True})
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


def catalogue_claims(metadata):
    seed_vocabulary()
    claims = []
    tags = metadata["tags"]
    person_context = any(normalize(tag) in {"portrait", "saint", "saints", "սուրբ"} for tag in tags)
    sources = [("title", metadata["title"])] + [("tag", tag) for tag in tags]
    if metadata["filename"]:
        sources.append(("filename", re.sub(r"\.[^.]+$", "", metadata["filename"]).replace("_", " ").replace("-", " ")))
    for ref, text in sources:
        parsed = parse(text, create=True, person_context=person_context or normalize(text).startswith("portrait of "))
        for kind in ("subjects", "themes"):
            claims.extend({"concept": pk, "source": ref, "text": text} for pk in parsed[kind])
        for kind in ("event", "group"):
            if parsed[kind]:
                claims.append({"concept": parsed[kind], "source": ref, "text": text})
    return claims
