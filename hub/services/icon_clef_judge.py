"""Judge an icon shortlist for a feast with Cloudflare's Clef decision model.

The LLM matcher asks a model to write a ranked JSON shortlist and then validates
it row by row; most of its failure handling exists because those rows can be
malformed. Here each shortlisted icon gets the same few yes/no questions, Clef
returns a probability for each, and suggestion and assignment are thresholds in
code. This is the pattern ``prayers/clef_moderation.py`` uses for moderation.

The assignment threshold is calibrated against the staff-reviewed 2026 feasts
(hub/tests/data/icon_match_cases_2026_reviewed.json); see docs/icon-match-eval/
README.md. Re-run that calibration before changing a question, a threshold or
the placement rule.
"""

import base64
import contextvars
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
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
            "a finding or exaltation, or a feast of the Lord, rather than only honouring one or more persons? "
            "The eve of a feast, or of the fast before it, remembers that feast's event."
        ),
    },
}

# Asked only when one saint of a multi-saint feast is within reach of assignment.
SEVERAL_QUESTIONS = {
    "names_several": {
        "type": "noul",
        "instructions": "Does this commemoration name two or more persons or groups?",
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
# Assignment needs a near-certain answer on the rung that justifies it. Calibrated on
# 2026-10-16 and re-checked with the weighted shortlist on 2026-10-17: no wrong top pick
# among the reviewed feasts reached it, with each icon's tags in either order.
ASSIGN_THRESHOLD = 0.90
SHORTLIST_LIMIT = 24
RANK = ("exact_event", "exact_subject", "subject_portrait", "related_specific")
MAX_WORKERS = 8

STOPWORDS = frozenset(
    "a and day feast for great her his holy in of remembrance saint saints st sts sunday the their to".split()
)
# Title words that describe an icon's medium rather than whom it shows.
DESCRIPTIVE_TITLE_WORDS = frozenset("fresco icon illuminated manuscript miniature painting untitled".split())
WORD = re.compile(r"\w+")


def _words(text):
    return [t for t in WORD.findall(unicodedata.normalize("NFKC", text).casefold()) if len(t) > 1]


def spelling_variant(a, b):
    """Whether two words are one name spelled two ways: one edit apart from five letters, two from eight.

    Transliterated names drift (Onuphrius/Onophrius, Ghevond/Ghevont); an exact match would miss them.
    Loose on purpose: it only widens the shortlist, and Clef judges what it lets in.
    """
    if a == b:
        return True
    shorter = min(len(a), len(b))
    if shorter < 5 or abs(len(a) - len(b)) > 2 or a[:2] != b[:2]:
        return False
    return _within_edits(a, b, 2 if shorter >= 8 else 1)


def _within_edits(a, b, limit):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def lexical_shortlist(feast, records, limit=SHORTLIST_LIMIT):
    """Icons sharing words with the feast name, the most distinctive overlap first.

    Deliberately cheap and recall-oriented: Clef rejects what does not fit, but it
    never sees what this leaves out. A word counts by how rare it is in the catalogue,
    so the one icon of Aristakes is not crowded out by the dozens sharing "Gregory" and
    "Illuminator". A word in the title counts double, and a spelling variant counts as
    the word. Words of four or more letters also match inside run-together titles such
    as "StGregoryofTatevandStJohnofVorodn".
    """
    docs, frequency = [], {}
    for record in records:
        title = set(_words(record["title"])) - STOPWORDS
        tag_words = [_words(tag) for tag in record["tags"]]
        tags = set().union(*tag_words) - STOPWORDS
        texts = " ".join([record["title"], *record["tags"]])
        compact = "".join(WORD.findall(unicodedata.normalize("NFKC", texts).casefold()))
        docs.append((record, title, tags, compact))
        for word in title | tags:
            frequency[word] = frequency.get(word, 0) + 1

    def weight(word):
        return math.log((len(records) + 1) / (frequency.get(word, 0) + 1)) + 1

    wanted = list(dict.fromkeys(w for w in _words(feast) if w not in STOPWORDS))
    variants = {w: {v for v in frequency if spelling_variant(w, v)} | {w} for w in wanted}
    scored = []
    for record, title, tags, compact in docs:
        score = 0.0
        for word in wanted:
            if variants[word] & title:
                score += 2 * weight(word)
            elif variants[word] & tags or (len(word) >= 4 and word in compact):
                score += weight(word)
        if score:
            scored.append((-score, record["id"], record))
    return [record for _, _, record in sorted(scored, key=lambda item: item[:2])[:limit]]


def _same_name(word, feast_word):
    """Stricter than spelling_variant, which would join Simeon and Simon, or Theodosius and Theodotus:
    the same word, a plural, or one letter apart in a name of eight letters or more (Onophrius)."""
    if word == feast_word or word + "s" == feast_word or feast_word + "s" == word:
        return True
    return min(len(word), len(feast_word)) >= 8 and _within_edits(word, feast_word, 1)


def title_names_only_the_feast(feast, record, records):
    """Whether the icon's title names someone, and only someone, the feast's name names.

    Clef cannot tell namesakes apart. It related St. Grigoris of Aghvank, St. Sergius (Sarkis)
    and St. Jacob of Nisibis, at 0.88 to 0.95, to feasts naming a different Grigoris, the
    Sergius of Sergius and Bacchus, and a Jacoc. Each title carries a word the feast never
    uses, so a title adding "of Aghvank" or "the Theologian" names someone else. A title with
    no distinctive word at all ("Untitled Icon #2") names no one, and fails. Words in at
    least ten titles and one title in twenty (Christ, Gregory, Theotokos), words describing
    the medium, and numbers do not count.
    """
    counts = {}
    for other in records:
        for word in set(_words(other["title"])):
            counts[word] = counts.get(word, 0) + 1
    common = {w for w, n in counts.items() if n >= max(10, 0.05 * len(records))} | DESCRIPTIVE_TITLE_WORDS
    feast_words = set(_words(feast))
    distinctive = [
        word for word in _words(record["title"]) if word not in STOPWORDS and word not in common and not word.isdigit()
    ]
    return bool(distinctive) and all(any(_same_name(word, f) for f in feast_words) for word in distinctive)


IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
# Clef accepts images of up to 4 MiB; thumbnails are tens of kilobytes.
IMAGE_MAX_BYTES = 4 * 1024 * 1024


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A thumbnail URL is fetched as stored: a redirect could lead off the bucket, or off http(s)."""

    def redirect_request(self, *args, **kwargs):
        return None


_THUMBNAIL_OPENER = urllib.request.build_opener(_RefuseRedirects)


def thumbnail_image_loader(urls, *, timeout=20):
    """``image_for`` for ``judge_icons``, from each icon's thumbnail URL.

    ``urls`` maps icon id to the thumbnail's public URL: the cached 400x300 rendition
    the thresholds were calibrated on. Returns a base64 data URL, or None when an
    icon has no web URL, or the download is not a PNG, JPEG or WebP image within
    Clef's size limit. A redirect, a network error, or a download still running
    after ``timeout`` seconds raises. Downloads are cached for the loader's lifetime.
    """
    cache = {}

    def image_for(icon_id):
        if icon_id not in cache:
            cache[icon_id] = None
            url = urls.get(icon_id)
            if url and urllib.parse.urlsplit(url).scheme in ("http", "https"):
                deadline = time.monotonic() + timeout
                chunks, size = [], 0
                with _THUMBNAIL_OPENER.open(url, timeout=timeout) as response:
                    content_type = response.headers.get_content_type()
                    while size <= IMAGE_MAX_BYTES:
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"Thumbnail download exceeded {timeout}s")
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                if content_type in IMAGE_TYPES and size <= IMAGE_MAX_BYTES:
                    cache[icon_id] = f"data:{content_type};base64,{base64.b64encode(b''.join(chunks)).decode()}"
        return cache[icon_id]

    return image_for


def icon_state(feast, record, *, with_image=False):
    tags = ", ".join(record["tags"]) or "(none)"
    state = f"Commemoration: {feast}\nIcon title: {record['title']}\nIcon tags: {tags}"
    return state + "\nThe icon's image is attached." if with_image else state


def place(probabilities, names_event):
    """Map one icon's probabilities onto the matcher's relation ladder, or None to omit it.

    An icon depicts the event only when the commemoration names one. Shown an image,
    Clef otherwise calls the nearest Gospel scene a depiction of a day that remembers
    no event: Entry into Jerusalem scored 0.93 for the Median day of Great Lent.

    A depiction or a portrait implies ``related``, so its strength is capped by it:
    answers that disagree are not near-certain, however high one of them is. The Last
    Judgment for the Sunday of the Judge scored 0.90 as the event but 0.76 as related,
    and the reviewer chose a different icon.
    """
    related = probabilities["related"]
    if names_event and probabilities["depicts_event"] >= RUNG_THRESHOLD:
        return "exact_event", min(probabilities["depicts_event"], related)
    if probabilities["all_figures"] >= RUNG_THRESHOLD:
        return ("subject_portrait" if names_event else "exact_subject"), min(probabilities["all_figures"], related)
    if related >= RUNG_THRESHOLD:
        return "related_specific", related
    return None, 0.0


def assignable(relation, strength, *, event_shortlisted=False):
    """Only a near-certain depiction is written without a human.

    A portrait of the commemorated saint for an event feast (subject_portrait)
    is written only when no shortlisted icon reached the event rung: if the
    catalogue may hold the event itself, that is the more specific depiction.
    One saint of a multi-saint feast (related_specific) is decided in
    judge_icons, because it needs the feast and the catalogue as well.
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
    Clef then judges the picture as well as its title and tags. The thresholds
    were calibrated with every icon's image, so when ``image_for`` is given and
    any shortlisted icon had to be judged without one (no image, or a failed
    download), the outcome only suggests: nothing in it is auto-assignable.
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

    outcome.call_count = 1
    try:
        names_event = ask(f"Commemoration: {feast}", FEAST_QUESTIONS)["names_event"] >= RUNG_THRESHOLD
    except (ClefError, RuntimeError):
        outcome.diagnostics.append("clef_feast_question_failed")
        outcome.elapsed_seconds = round(time.monotonic() - started, 3)
        return outcome

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
    images_complete = True
    if image_for:
        imaged = sum(1 for *_, has_image in judged if has_image)
        outcome.diagnostics.append(f"clef_images:{imaged}/{len(judged)}")
        if imaged < len(judged):
            images_complete = False
            outcome.diagnostics.append("clef_images_incomplete")
    matches, strengths = [], {}
    by_record = {record["id"]: record for record, _, _ in judged}
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
    top = matches[0] if matches else None
    if (
        not failures
        and images_complete
        and top is not None
        and top["relation"] == "related_specific"
        and strengths[top["id"]] >= ASSIGN_THRESHOLD
        and title_names_only_the_feast(feast, by_record[top["id"]], records)
    ):
        # Review policy: one saint of a multi-saint feast may be written, when nothing
        # depicts them all. The top is related_specific only if no icon reached a higher rung.
        try:
            several = ask(f"Commemoration: {feast}", SEVERAL_QUESTIONS)["names_several"] >= RUNG_THRESHOLD
        except (ClefError, RuntimeError):
            several = False
            outcome.diagnostics.append("clef_names_several_failed")
        outcome.call_count += 1
        if several:
            top["auto_assignable"] = True
            outcome.diagnostics.append("clef_one_of_several")
    if not images_complete:
        for m in matches:
            m["auto_assignable"] = False
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
