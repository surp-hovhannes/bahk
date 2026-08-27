"""Recommend prayers for a fast intention (issue #450).

Two tag sources are unioned: a curated keyword->tag map (free, instant) and an
LLM pass stored on FastIntention.matched_tags (computed async at write time).
To tune the map, edit INTENTION_TAG_MAP below; no code changes needed.
"""
import json
import logging
import re

logger = logging.getLogger(__name__)

# Curated map: keywords matched against intention text -> production prayer tags.
# Tuned against real tags/intentions (issue #450); cheap, always-on fallback to the
# LLM tags stored on FastIntention.matched_tags.
INTENTION_TAG_MAP = {
    'peace': {
        'keywords': ['peace', 'calm', 'anxiety', 'anxious', 'worry'],
        'tags': ['peace', 'protection', 'trust'],
    },
    'healing': {
        'keywords': ['heal', 'health', 'sick', 'illness', 'recovery'],
        'tags': ['deliverance', 'protection', 'hope'],
    },
    'gratitude': {
        'keywords': ['gratitude', 'grateful', 'thank'],
        'tags': ['doxology', 'adoration'],
    },
    'family': {
        'keywords': ['family', 'children', 'child', 'parents', 'marriage', 'spouse', 'love'],
        'tags': ['intercession', 'guardian', 'love'],
    },
    'spiritual growth': {
        'keywords': ['closer', 'grow', 'faith', 'seek', 'follow jesus', 'christ', 'disciple'],
        'tags': ['faith', 'enlightenment', 'holiness'],
    },
    'guidance': {
        'keywords': ['guidance', 'clarity', 'direction', 'wisdom', 'discern', 'lead me'],
        'tags': ['guidance', 'wisdom', 'light'],
    },
    'strength': {
        'keywords': ['strength', 'patience', 'patient', 'temptation', 'persever', 'endure'],
        'tags': ['deliverance', 'purification', 'surrender'],
    },
    'repentance': {
        'keywords': ['repent', 'forgive', 'sin', 'confess'],
        'tags': ['repentance', 'forgiveness', 'confession'],
    },
}


def tags_for_intention(text):
    """Return prayer tags whose curated keywords appear in the intention text."""
    matched = []
    for entry in INTENTION_TAG_MAP.values():
        if any(re.search(r'\b' + re.escape(kw), text, re.IGNORECASE)
               for kw in entry['keywords']):
            matched.extend(tag for tag in entry['tags'] if tag not in matched)
    return matched


def live_prayer_tags():
    """All tag names currently used on prayers (the LLM's allowed output set)."""
    return list(prayer_tag_counts())


def prayer_tag_counts():
    """{tag_name: prayer_count} for tags actually used on prayers."""
    from django.db.models import Count
    from taggit.models import Tag
    from django.contrib.contenttypes.models import ContentType
    from prayers.models import Prayer
    ct = ContentType.objects.get_for_model(Prayer)
    return dict(
        Tag.objects.filter(taggit_taggeditem_items__content_type=ct)
        .annotate(n=Count('taggit_taggeditem_items'))
        .values_list('name', 'n')
    )


# Few-shot grounding examples (use only tags that exist in production).
_FEW_SHOT = [
    ("For my mother's surgery", ['intercession', 'protection', 'hope']),
    ("Grow closer to God this Lent", ['faith', 'adoration']),
    ("Peace in my family's conflict", ['peace', 'intercession']),
    ("Help me forgive a friend who hurt me", ['forgiveness', 'mercy']),
    ("Finish my work project on time", []),  # mundane intentions get nothing
]


def llm_tags_for_intention(text, llm_prompt=None):
    """LLM-tag an intention against the live prayer tags.

    Returns a list of validated tag names, [] if the model matches nothing,
    or None if no active 'intentions' LLMPrompt is configured (caller falls
    back to the curated map). Hallucinated tags are dropped.
    """
    from hub.models import LLMPrompt
    from hub.services.llm_service import get_llm_service

    if llm_prompt is None:
        llm_prompt = LLMPrompt.objects.filter(active=True, applies_to='intentions').first()
        if llm_prompt is None:
            return None

    tag_counts = prayer_tag_counts()
    tags = list(tag_counts)
    system_prompt = f"{llm_prompt.role}\n\n{llm_prompt.prompt}"
    tag_list = ', '.join(f'{name} ({n})' for name, n in tag_counts.items())
    examples = '\n'.join(
        f'Intention: {intent}\nTags: {json.dumps(example_tags)}'
        for intent, example_tags in _FEW_SHOT
        if all(t in tag_counts for t in example_tags)
    )
    user_message = (
        f"Available tags (with number of prayers each is used on): {tag_list}\n\n"
        f"Examples:\n{examples}\n\n"
        f"Intention: {text}\n\n"
        "Return ONLY a JSON array of tags from the available list that are "
        "relevant to the intention, e.g. [\"peace\", \"trust\"]. "
        "Return [] if none fit."
    )

    try:
        client = get_llm_service(llm_prompt.model).client
        if 'claude' in llm_prompt.model:
            resp = client.messages.create(
                model=llm_prompt.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=256,
            )
            raw = resp.content[0].text.strip() if resp.content else ''
        else:
            resp = client.chat.completions.create(
                model=llm_prompt.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=256,
            )
            raw = resp.choices[0].message.content.strip() if resp.choices else ''
    except Exception:
        logger.exception("LLM intention tagging failed")
        return None

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    valid = {t.lower(): t for t in tags}
    return [valid[t.lower()] for t in parsed
            if isinstance(t, str) and t.lower() in valid]


def tags_for_recommendation(intention):
    """Union of curated-map tags and stored LLM tags for an intention.

    Enqueues LLM tagging when matched_tags is still uncomputed (None) so the
    next read has it.
    """
    tags = tags_for_intention(intention.text)
    if intention.matched_tags is None:
        if intention.text.strip() and intention.pk:
            from hub.tasks.llm_tasks import tag_intention_prayers
            tag_intention_prayers.delay(intention.pk)
    else:
        tags = list(dict.fromkeys(tags + intention.matched_tags))
    return tags


def recommended_prayers(intention, fast, limit=5):
    """Prayers from the fast's church matching the intention, ranked by tag overlap.

    Prayers linked to this fast are boosted above untagged-to-the-fast ones.
    """
    tags = tags_for_recommendation(intention)
    if not tags:
        return []
    from django.db.models import Case, Count, IntegerField, Q, Value, When
    from prayers.models import Prayer
    return (
        Prayer.objects
        .filter(church=fast.church, tags__name__in=tags)
        .annotate(
            overlap=Count('tags', filter=Q(tags__name__in=tags), distinct=True),
            on_fast=Case(
                When(fast=fast, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by('-on_fast', '-overlap', '-created_at')
        .distinct()[:limit]
    )
