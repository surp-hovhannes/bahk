"""Versioned, Unicode-preserving inputs and private image derivatives."""

import hashlib
import json
import re
import unicodedata
from io import BytesIO

from django.conf import settings
from PIL import Image, ImageOps

NORMALIZER = "nfc-qualified-v2"
SCHEMA = "icon-evidence-v1"
PROMPT = "observation-comparison-v1"
RULES = "corroboration-v2"
ADAPTER = "themes-only-v1"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize(value):
    value = unicodedata.normalize("NFC", value).casefold()
    value = re.sub(r"\b(?:saints?|sts?\.?)\s+", "", value)
    value = re.sub(r"^(?:սուրբ|սրբոց|սբ\.?)\s+", "", value)
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())


def filename(value):
    value = unicodedata.normalize("NFC", str(value).replace("\\", "/").rsplit("/", 1)[-1])
    return "".join(c for c in value if not unicodedata.category(c).startswith("C"))[:255]


def recover_filename(value):
    match = re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{8}_(.+)\.[^.]+", filename(value))
    return match[1][:255] if match else ""


def versions():
    return dict(
        release=getattr(settings, "ICON_TAXONOMY_RELEASE", "catalogue-v1"),
        model=getattr(settings, "ICON_TAXONOMY_MODEL", "gpt-5.6-luna"),
        normalizer=NORMALIZER,
        schema=SCHEMA,
        prompt=PROMPT,
        rules=RULES,
        image_processor="exif-rgb-1536-v1",
    )


def dependency_snapshot(ids=(), terms=()):
    """Used meanings, their aliases and competitors; unrelated additions are inert."""
    from icons.models import TaxonomyConcept, TaxonomyAlias, TaxonomyRelation
    from django.db.models import Q

    release = versions()["release"]
    ids = sorted(set(ids))
    own_aliases = list(
        TaxonomyAlias.objects.filter(concept_id__in=ids, concept__release__version=release).values_list(
            "normalized", flat=True
        )
    )
    terms = sorted(set(terms) | set(own_aliases))
    aliases = list(
        TaxonomyAlias.objects.filter(Q(concept_id__in=ids) | Q(normalized__in=terms), concept__release__version=release)
        .order_by("pk")
        .values("id", "concept_id", "language", "text", "normalized", "source")
    )
    relations = list(
        TaxonomyRelation.objects.filter(source_concept_id__in=ids)
        .order_by("pk")
        .values("id", "source_concept_id", "target_id", "kind", "source")
    )
    meaning_ids = set(ids) | {a["concept_id"] for a in aliases} | {r["target_id"] for r in relations}
    meanings = list(
        TaxonomyConcept.objects.filter(pk__in=meaning_ids)
        .order_by("pk")
        .values("id", "release_id", "key", "kind", "label", "definition", "source")
    )
    return {
        "ids": ids,
        "terms": terms,
        "digest": digest({"meanings": meanings, "aliases": aliases, "relations": relations}),
    }


def dependencies_current(snapshot):
    return bool(snapshot and dependency_snapshot(snapshot["ids"], snapshot["terms"])["digest"] == snapshot["digest"])


def analysis_dependencies(inputs, claims, observation=None, prior=None):
    from icons.models import TaxonomyConcept, TaxonomyAlias

    ids = {c["concept"] for c in claims}
    terms = {normalize(inputs["title"]), *(normalize(t) for t in inputs["tags"])}
    # Fixed code-owned scene/theme definitions can add observations absent claims.
    for c in TaxonomyConcept.objects.filter(release__version=versions()["release"]):
        if c.kind in {"theme", "event"} and (
            c.source.get("definition_set") == "biblical-iconographic-scenes-v1"
            or c.source.get("rules") == "observable-themes-v2"
        ):
            ids.add(c.pk)
    if prior:
        ids.update(prior.get("ids", []))
        terms.update(prior.get("terms", []))
    if observation:
        terms.update(normalize(o["text"]) for o in observation["observations"] if o["kind"] == "inscription")
        ids.update(
            TaxonomyAlias.objects.filter(
                normalized__in=terms, concept__release__version=versions()["release"]
            ).values_list("concept_id", flat=True)
        )
    return dependency_snapshot(ids, terms)


def metadata(icon):
    return dict(
        title=icon.title,
        tags=sorted(tag.name for tag in icon.tags.all()),
        filename=icon.original_filename,
        filename_provenance=icon.filename_provenance,
        image=icon.image.name,
        image_digest=icon.image_content_digest,
        image_revision=icon.image_revision,
        church=icon.church_id,
    )


def fingerprint(icon):
    return digest(metadata(icon))


def image_input(icon):
    if not icon.image:
        raise ValueError("missing_image")
    with icon.image.storage.open(icon.image.name, "rb") as source:
        raw = source.read(25 * 1024 * 1024 + 1)
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("image_too_large")
    sha = hashlib.sha256(raw).hexdigest()
    with Image.open(BytesIO(raw)) as img:
        if img.width * img.height > 40_000_000:
            raise ValueError("image_too_large")
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1536, 1536))
        output = BytesIO()
        img.save(output, format="JPEG", quality=90)
    return sha, output.getvalue()
