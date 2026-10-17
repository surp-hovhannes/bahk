# Icon matching: labelled-case evaluation

`evaluate_icon_matching_paired` compares two profiles but reports only *agreement*: its own
output calls itself "Exploratory paired audit; **no accuracy labels or winner**". It can say two
arms disagreed; it cannot say which was right.

`evaluate_icon_matching_cases` adds the missing half: hand-labelled cases, so a run reports
accuracy. The labels are a reviewer's editorial judgement about one catalogue, **not ground
truth**, and are meant to be argued with and corrected. Because they will change, any saved
report can be re-scored against the current labels for free (`--rescore-json`).

## Inputs

**Catalogue.** `hub/tests/data/icon_catalogue_snapshot_2026-09-13.json`: all 440 production
icons (id, title, tags) from the public `/api/icons/` endpoint on 2026-09-13, committed so runs
are reproducible as the live catalogue grows. Every production icon belongs to one church, so
this is also the catalogue the feast task searches; once icons span churches, export one
church's icons instead (`export_icon_match_cases`).

**Cases.** `hub/tests/data/icon_match_cases_armenian_12.json`: 12 feasts from the 2026 calendar,
chosen (not sampled) to cover three situations the matcher must tell apart:

| Expectation | Cases | A good answer |
|---|---|---|
| `exact_exists` | 4 | top-1 is a `correct` icon |
| `fallback_only` | 4 | top-1 is an `acceptable` icon |
| `nothing_suitable` | 4 | nothing returned |

Each case has three label lists and a `note`:

- `correct`: depicts the commemoration.
- `acceptable`: a reasonable suggestion when nothing correct exists.
- `assignable`: may be written to the feast with no human in the loop. **Defaults to
  `correct`**, so a fallback is only assignable when a reviewer adds it here. When present it
  replaces that default, and every id in it must also be `correct` or `acceptable`. Policy: a portrait
  of the commemorated saint is assignable when no more specific depiction (such as the event
  itself) exists; a scene in which the saint merely appears is not.

## Scoring

Per case, the top-ranked match gets one verdict: `exact`, `acceptable`, `wrong`, `abstained`
(nothing returned, and nothing fits), `missed` (nothing returned, but something fits) or
`error` (unavailable, or a partial run that returned nothing: the matcher stopped looking, so
its silence is not an abstention). `desirable_count` is `exact + acceptable + abstained`; `by_expectation` breaks the
verdicts down per row of the table above.

Assignment is scored the way production writes: `hub/tasks/icon_tasks.py` writes an icon only
when the outcome is `complete` **and** its first match is `auto_assignable`. That one icon is
`assigned_id`; `unsafe_assignment` means it is not in the case's `assignable` list. This is the
costly error, since it writes to production without review.

## Running it

Live, billable (needs `OPENAI_API_KEY`; limits default to the Celery feast path's):

```bash
python manage.py evaluate_icon_matching_cases \
  --catalogue-json hub/tests/data/icon_catalogue_snapshot_2026-09-13.json \
  --cases-json     hub/tests/data/icon_match_cases_armenian_12.json \
  --profile        control-v1 \
  --output-json    /tmp/control-v1.json \
  --live
```

Feast names must be unique within a case file, because report rows are matched to labels by
name. The output file must not exist yet; both are checked before any billable call.

Offline, after editing labels:

```bash
python manage.py evaluate_icon_matching_cases \
  --cases-json  hub/tests/data/icon_match_cases_armenian_12.json \
  --rescore-json docs/icon-match-eval/results-sonnet-control-v1.json \
  --output-json /tmp/rescored.json
```

## Results so far

Three result files are committed verbatim from surp-hovhannes/bahk#549:

- `results-production-gpt-4.1-mini.json`: the deployed matcher, called through
  `POST /api/icons/match/` (outcome rows, not a harness report; `--rescore-json` accepts both).
- `results-sonnet-control-v1.json`: Sonnet 5 with the production prompts.
- `results-sonnet-rubric-v1.json`: Sonnet 5 plus a curator rubric on the assess/verify prompts.

The two Sonnet profiles and the Anthropic provider that produced them are on #549's branch
(`feat/icon-matching-sonnet-5-and-rubric-eval`), not on `main`.

Re-scored with the scoring above and the labels as committed:

| | production gpt-4.1-mini | Sonnet 5 | Sonnet 5 + rubric |
|---|---|---|---|
| Desirable top-1 | 7 / 12 | 9 / 12 | 8 / 12 |
| `exact_exists`: exact | **4 / 4** | 3 / 4 | **4 / 4** |
| `fallback_only`: acceptable | 1 / 4 | **4 / 4** | **4 / 4** |
| `nothing_suitable`: abstained | **2 / 4** | **2 / 4** | 0 / 4 (1 error) |
| Wrong top-1 | **2** | 3 | 3 |
| `status == "complete"` | 8 / 12 | 11 / 12 | 11 / 12 |
| Assigned / unsafe | n/a | 3 / 0 | 4 / 0 |

Read it with these caveats:

1. **This is not a model comparison.** The production column went through the public endpoint
   with a 20s budget and `auto_assign_policy="none"` (so it can never assign), and without the
   coverage fix that stops one malformed row from turning a batch `partial`. Three of its four
   `partial` outcomes were exactly that bug. The Sonnet arms ran through this harness with 300s,
   `feast_strict` and the fix. The like-for-like control is `--profile control-v1` through this
   harness; see the live baseline below.
2. **Sonnet's gain is all in `fallback_only`.** It returns a reasonable fallback where
   gpt-4.1-mini returned nothing. On `exact_exists` gpt-4.1-mini was as good or better
   (it ranked the Hripsime companions icon first; plain Sonnet did not), and it made fewer wrong
   top-1 picks.
3. **Both Sonnet arms auto-assigned a portrait to an event feast**: a St John the Baptist
   portrait (icon 36) to *Beheading of St. John the Forerunner*, where no beheading scene
   exists. Under the portrait policy above that is allowed, so the case lists the three Baptist
   portraits as `assignable` and neither arm has an unsafe assignment.
4. **n = 12, single run, no repeats**, and the cases over-represent hard ones. One label was
   already wrong once (Elijah: icon 499's tags include `elijah`) and two are still debatable
   (*Feast of the Holy Cross*, *Remembrance of the Dead*).
5. **Latency.** Sonnet took 15-200s per case and one rubric case ran 707s against a 300s
   budget. Only the Celery feast path could carry it.

### Live baseline: gpt-4.1-mini through this harness

`results-control-v1-run{1,2,3}.json`: the production profile (`control-v1`, gpt-4.1-mini),
three runs on 2026-10-16 through this harness with the coverage fix
(`fix/icon-match-coverage-cascade`) applied and the Celery path's default limits.

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| Desirable top-1 | 6 / 12 | 7 / 12 | 6 / 12 |
| `exact_exists`: exact | 3 / 4 | 4 / 4 | 4 / 4 |
| `fallback_only`: acceptable | 2 / 4 | 1 / 4 | 0 / 4 |
| `nothing_suitable`: abstained | 1 / 4 | 2 / 4 | 2 / 4 |
| Errors (`unavailable`) | 2 | 0 | 0 |
| Assigned / **unsafe** | 3 / **2** | 3 / 0 | 4 / **1** |
| Median seconds per feast | 6.3 | 6.2 | 6.4 |

The production matcher **writes wrong icons**: in two of three runs it auto-assigned
*Crucifixion* (icon 124) to *Feast of the Holy Cross*, and once *Bishop Mark of Arethusa and the
Deacon Cyril* (icon 76) to *Holy Voskian Priests*, as `exact_subject` with high confidence,
because both are "clergy martyrs". Neither involved a discarded row, so the coverage fix did not
cause them; the same outcomes assign on `main`. It is also unstable: the same feast flips between
verdicts from run to run, so a single run of this matcher is not evidence of much.

## Clef judge

`hub/services/icon_clef_judge.py` replaces the LLM's written verdicts with Cloudflare Clef
probabilities and thresholds in code (the `prayers/clef_moderation.py` pattern). Two engines:

- `--engine clef`: a lexical shortlist (words shared with the feast name, also matched inside
  run-together titles), judged by Clef. No LLM call. Needs `CLOUDFLARE_WORKERSAI_ACCOUNT_ID`
  and `CLOUDFLARE_WORKERSAI_API_KEY`.
- `--engine llm+clef --profile control-v1`: the LLM matcher's positives, judged by Clef. Needs
  both OpenAI and Cloudflare credentials.

```bash
python manage.py evaluate_icon_matching_cases --engine clef \
  --catalogue-json hub/tests/data/icon_catalogue_snapshot_2026-09-13.json \
  --cases-json     hub/tests/data/icon_match_cases_armenian_12.json \
  --output-json    /tmp/clef.json --live
```

`--clef-images` also sends each shortlisted icon's image, downloaded from the `thumbnail_url`
on each catalogue record (public S3 thumbnails, added to the snapshot from `/api/icons/` on
2026-10-16).

Each shortlisted icon costs one Clef call (three yes/no questions) plus one per feast. On the
12 cases the lexical shortlist holds every labelled icon and is empty for *Holy Voskian
Priests* and *Remembrance of the Dead*, so those two abstain without any call. That says
nothing yet about the rest of the calendar, where Armenian names and synonyms will miss.

### Live results

`results-clef-run{1,2}.json` (lexical shortlist, no LLM) and `results-llm-clef-run1.json`
(gpt-4.1-mini positives judged by Clef), 2026-10-16, full `clef` model, thresholds as committed.

| | gpt-4.1-mini (3 runs) | Clef, lexical shortlist | gpt-4.1-mini shortlist + Clef |
|---|---|---|---|
| Desirable top-1 | 6, 7, 6 / 12 | **10 / 12** (both runs) | 6 / 12 |
| `exact_exists`: exact | 3, 4, 4 / 4 | 3 / 4 | 2 / 4 |
| `fallback_only`: acceptable | 2, 1, 0 / 4 | **3 / 4** | 1 / 4 |
| `nothing_suitable`: abstained | 1, 2, 2 / 4 | **3 / 4** | 2 / 4 |
| `complete` | 10, 12, 12 | **12** | 11 |
| Assigned / **unsafe** | 3/**2**, 3/0, 4/**1** | 1 / **0** | 1 / **0** |
| Median seconds per feast | ~6.3 | **~2** | ~8 |

- **Clef with the lexical shortlist is the best arm here**: more desirable answers, no unsafe
  writes, every outcome `complete`, about three times faster, and the two runs were identical
  apart from timing. The gpt-4.1-mini runs disagreed with each other on several feasts.
- **The LLM shortlist is the bottleneck in `llm+clef`.** Clef can only judge what gpt-4.1-mini
  returned, so its misses (Elijah, Sunday of the Judge, Lazarus) stay misses.
- **Clef is too cautious to assign.** It wrote only Palm Sunday. The correct icons for Lazarus
  (0.79), Hripsime (0.78) and the Forty Martyrs (0.63-0.71) fall under `ASSIGN_THRESHOLD`
  (0.85). Re-scoring the saved probabilities offline: at 0.75 Clef assigns Palm Sunday, Lazarus
  and Hripsime with no unsafe write; at 0.70 it also writes a Crucifixion icon to the Holy
  Cross, and icon 218 (the run-together Forty Martyrs title, labelled acceptable but not
  assignable) to the Forty Martyrs. The margin is a few hundredths on one case each, so the threshold stays at 0.85 until
  a larger labelled set can set it.
- **Its errors are the debatable cases.** Top picks of a Crucifixion for the Holy Cross and
  St. Gregory of Tatev icons for a feast that commemorates him (the label wants icon 175, which
  shows two of the four saints) are scored `wrong` under the current labels. It also rated
  *St. Elisha* 0.73 as depicting the Prophet Elijah, so similar names can still mislead it.
- *Palm Sunday 2* (icon 325), ranked second, is probably a correct Palm Sunday icon missing
  from the labels.

### Closing the assignment gap

Five of the 12 cases have an icon that may be written automatically (the four `exact_exists`
cases, and the Beheading of St. John once portraits were allowed). What each change did, live,
with `ASSIGN_THRESHOLD` at 0.85:

| Run | Desirable top-1 | Auto-assigned (of 5) | Unsafe |
|---|---|---|---|
| gpt-4.1-mini, 3 runs | 6, 7, 6 | 3, 3, 3 | 2, 0, 1 |
| Clef, metadata only (`results-clef-run3-portraits.json`) | 10 | 2 | 0 |
| **Clef + icon image** (`results-clef-images-run1.json`, `--clef-images`) | **10** | **3** | **0** |
| Clef + image + the feast's generated description | 8 | 2 | 1 |
| Clef + image + explicit yes/no `criteria` | 9 | 3 | 0 |

- **Images help.** Lazarus (`IlluminatedManuscript1450(5)`) rises from 0.79 to 0.90 and is
  assigned, and the *St. Elisha* name confusion disappears. Clef now matches gpt-4.1-mini's
  assignment rate with no unsafe write.
- **Descriptions hurt**: a long description dilutes the yes/no questions. Not kept.
- **Explicit criteria are noise at this size**: they traded the Lazarus assignment for Hripsime
  and turned Elijah wrong. Not kept.
- **The remaining gap is calibration, not wording.** With images, the remaining correct icons
  (Forty Martyrs 0.81, Hripsime 0.77) score level with the wrong ones (a Crucifixion for the Holy
  Cross 0.80, the Last Judgment for the Sunday of the Judge 0.82), so no threshold separates
  them on these 12 cases. More labelled feasts are what will move it.

Use full `clef`, not `clef-flash`, for anything that writes.

### Calibration on the reviewed 2026 feasts

`ASSIGN_THRESHOLD` is now set from the staff review of 2026-10-16 (the decisions
`apply_icon_review` applied), not from the 12 cases above.

**Inputs.**
- `hub/tests/data/icon_match_cases_2026_reviewed.json`: 104 feasts.
  - 38 have a reviewer-chosen icon (`correct`).
  - 11 have an icon chosen as "only a fallback" (`acceptable`). These count as assignable,
    because the reviewer applied them.
  - 8 kept their stored icon.
  - 47 were marked "no icon fits" (`assignable: []`, so any write there is unsafe).
- The reviewer picked one icon per feast. Where Clef's pick shows the same scene (another
  Ascension, another Pentecost, and so on), it was added as assignable on inspection. Each of
  these 8 additions says so in its `note`, so they can be told apart from the reviewer's labels.
- `hub/tests/data/icon_catalogue_snapshot_2026-10-16.json`: the live catalogue after the
  review's 62 title/tag fixes.

**Clef is deterministic.** Two full runs returned identical probabilities on every feast, so a
threshold can be set to the hundredth without run-to-run noise. The probabilities for every
judged icon are in `clef-probabilities-reviewed-2026-10-16.jsonl`. Icons Clef put below 0.5 on
every question are omitted, since no rule here can place them. This lets you re-sweep offline.

**A threshold alone could not be made safe.** The rule as first written, with images, scored
like this (assigned / correct / unsafe):

| Threshold | 0.85 | 0.90 | 0.92 | 0.93 |
|---|---|---|---|---|
| Rule as first written | 16 / 13 / 3 | 13 / 11 / 2 | 9 / 8 / 1 | 6 / 6 / 0 |
| **Event gate + related cap** | 16 / 15 / 1 | **14 / 14 / 0** | 10 / 10 / 0 | 8 / 8 / 0 |

The wrong writes were mostly one pattern: shown an image, Clef calls the nearest Gospel scene
a depiction of a day that remembers no event.
- Entry into Jerusalem scored 0.93 for the Median day of Great Lent, 0.85 for the Sunday of
  the Advent and 0.82 for the Eve of Great Lent.
- Clef's own feast question had already said none of those days names an event.

Two changes to `place()`, both from what the questions mean rather than from a fitted constant:

1. **Event gate.** `depicts_event` places an icon only when the feast names an event
   (`names_event`). Otherwise the icon is judged as a portrait or as related.
2. **Related cap.** A depiction or a portrait implies `related`, so its strength is
   `min(rung probability, related)`. The Last Judgment for the Sunday of the Judge scored 0.90
   as the event but 0.76 as related, and the reviewer chose a different icon.

With both, the highest-scoring wrong top pick is St. Elisha for the Eve of the Fast of the
Prophet Elijah, at 0.853.
- 0.86 would add one more feast, the Discovery of the Holy Cross at 0.865, with a 0.01 margin
  over that name confusion.
- **`ASSIGN_THRESHOLD` = 0.90** keeps the same 14 assignments as 0.88 and leaves a 0.05 margin.

A missed assignment costs a manual pick; a wrong one is shown in the app.

**Checks beyond the 104.**

| Set | Assigned | Unsafe |
|---|---|---|
| 12 cases, 2026-09-13 snapshot (`results-clef-calibrated-12.json`) | 2 | 0 |
| 73 unreviewed 2026 feasts, live catalogue | 24 | 0 apparent |
| 104 reviewed feasts, harness end to end (`results-clef-calibrated-reviewed-2026-10-16.json`) | 14 | 0 |

- **12 cases.** Lazarus scores just under 0.90 on the old snapshot. Against the reviewed
  catalogue, where its icon is titled "Resurrection of Lazarus", it scores 0.94 and is assigned.
- **73 unreviewed feasts.** These are the ones a matcher agreed with. 13 of the 24 match the
  stored icon. The other 11 were checked by hand: each is another depiction of the same feast,
  or the named saint where nothing more specific exists. "Untitled Icon #4" for the Holy
  Translators is in fact Sts. Sahak and Mesrop, and its title should be fixed.
- **Production matcher, same 104 feasts.** gpt-4.1-mini, `max_results=1` as in `icon_tasks`,
  auto-assigned 19. 3–4 of those are wrong after crediting same-scene picks: Patriarch Kevork IV
  for the Holy Voskian Priests, the Soukiasants for other martyrs, the Armenian Genocide Martyrs
  for the Holy Cross of Varag, and possibly the Seven Archangels.

**What still limits coverage is upstream of the threshold.** Of the 57 reviewed feasts with an
assignable icon, Clef assigns 14. Of the rest:

| Where it falls short | Feasts |
|---|---|
| The right icon was judged but outranked | 14 |
| The right icon was not shortlisted, or scored under 0.5 on every question | 9 |
| The right icon is top but only `related_specific` (one saint of several, which is never written) | 9 |
| The right icon is top but under the threshold | 11 |

Lowering the threshold reaches only the last group, and only by giving up the margin above.

### Shortlist and ranking (2026-10-17)

The calibration left most of the gap upstream of the threshold. Two changes ship:

1. **A weighted shortlist.** Each shared word counts by how rare it is in the catalogue, title
   words count double, and spelling variants of a name match (one edit from five letters, two
   from eight).
   - The plain word overlap ranked by shared-word count, then by id. Every "St. Gregory the
     Illuminator" icon outranked the one St. Aristakes for his sons' feast, and the Exaltation
     icon fell past the 24-icon cut for the Appearance of the Holy Cross.
   - Spelling variants catch names like Onuphrius/Onophrius and Ghevond/Ghevont.
   - Measured on the 130 feasts with a known icon (the 57 reviewed plus the 73 unreviewed
     stored icons), the right icon reaches Clef for 123, up from 117, with about the same
     number of icons judged.
2. **The feast question covers eves.** "The eve of a feast, or of the fast before it,
   remembers that feast's event." The Eves of the Fasts of the Assumption and of the Nativity
   were closed by the event gate, so their feast icons could not place as depictions.

On the 104 reviewed feasts, with the labels extended as described below:

| | Before | After |
|---|---|---|
| Auto-assigned / unsafe | 14 / 0 | **16 / 0** |
| Right top suggestion, 57 feasts with an icon | 40 | **45** |
| Missed (no suggestion where an icon exists) | 2 | 1 |
| Silent where nothing fits (of 47) | 19 | 11 |

Other checks:
- **12 original cases** (`results-clef-shortlist-12.json`): still 2 assigned, 0 unsafe.
- **73 unreviewed feasts:** 24 assigned. 12 match the stored icon; the other 12 are each
  another icon of the same feast or saint on inspection.

Lower silence means more suggestions where nothing fits. A suggestion is never written, so
it costs a reviewer a glance, not a wrong icon.

**Labels.** The reviewer picked one icon per feast, so every variant surfaced other icons of
the same scene that scored as wrong: another Ascension, another St. John the Baptist, and so on.
- 23 such alternatives are now `assignable`, and 15 more that fit without being the chosen
  depiction are `acceptable` only.
- Each is marked "added on inspection (not by the reviewer)" in its `note`.
- The before/after table uses these extended labels for both columns.

**Clef is deterministic, but not stable under rewording.** Identical text gets identical
answers. Reordering the same tags on an icon moved its probabilities by a median 0.03, often
0.10–0.15, and at most 0.4.

`clef-probabilities-reviewed-2026-10-17.jsonl` holds every judged icon with its tags both in
the judge's sorted order and reversed:
- In both orders, no wrong top pick on a labelled feast reaches 0.90. The highest are 0.872
  sorted and 0.878 reversed, and both are icons judged acceptable rather than wrong.
- Among the unreviewed feasts, one does: the Forty Martyrs of Sebastia for the Holy Sukiasian
  Martyrs, at 0.915 reversed but 0.813 in the order production uses.

Read that as the residual risk of a stray title or tag edit, roughly one feast in two hundred.
A second pass in the other order before writing would not catch it: a correct pick (Nativity
and Baptism for the Eve of the Nativity, 0.923 and 0.805) looks the same.

**Tried and not kept:**

| Idea | Why not |
|---|---|
| A Clef multiple-choice question ranking the whole catalogue in one call | It found Doubting Thomas for New Sunday. But icons that share no word with the feast are unsafe to judge: for the Median day of Lent, which names no one, St. Gregory the Theologian scored 0.95 on "shows every person named". Only suggestions could use it, and it added more noise than right answers. |
| A bonus for shared word pairs ("holy cross") | Same recall. In a catalogue with 30 "St. Gregory the Illuminator" icons, it pushed the one Aristakes past the cut. |
| Armenian ending normalisation (-ian/-ants) | No recall gain, and it joins "christ" with "christian". |
| Ranking by strength across rungs, or confident claims first | Each gained one feast and lost two. |
| Averaging or taking the minimum over both tag orders | Fewer assignments at any threshold with no unsafe write. |

**What remains:** feasts whose icon shares no word with their name (New Sunday → Thomas, the
Sunday of the Advent → the Last Judgment, the Remembrance of the Dead → the Harrowing of
Hades), and Clef name confusions (Elisha for Elijah, the prophet Zechariah for Zachariah the
father of John). Multi-saint feasts are also unassigned where the best icon shows one of the
saints named (`related_specific`); whether those may be written is a policy decision.

### One saint of several (2026-10-17)

Staff policy, decided after the shortlist work: for a feast naming several saints, an icon of
one of them may be written. The reviewer had already chosen such icons for many feasts
(St. Andrew for Andrew and Philip, St. Matthew for the four Evangelists).

These icons sit on the `related_specific` rung, and `related` alone was unsafe. It scores
namesakes as high as the right saint, 0.88–0.95:
- St. Grigoris of Aghvank for Sts. Gordius, Polyeuctus and Grigoris;
- St. Sergius (Sarkis) for the Martyrs Sergius and Bacchus;
- St. Jacob of Nisibis for Sts. Jacoc and Themistocles;
- the Forty Martyrs of Sebastia for the Forty-Five Martyrs.

A dedicated question ("at least one of the persons named, not someone who merely shares a
name") scored those same namesakes 0.82–0.98, mostly above 0.95, and was dropped. Clef does not know which
Grigoris is which.

What separates them is in the text. Every namesake's title carries a word the feast never
uses: "of Aghvank", "(Sarkis)", "of Nisibis", "of Sebastia", "the Theologian", "the
Protomartyr". So the top icon is written only when all of the following hold:
- `related` ≥ `ASSIGN_THRESHOLD`;
- every distinctive word of its title appears in the feast's name
  (`title_names_only_the_feast`). Spelling variants count, and words found in one title in
  twenty, medium words like "icon" or "manuscript", and numbers are ignored;
- Clef answers that the feast names two or more persons or groups. This is one extra call,
  made only when the first two hold. It changed nothing on these feasts, but it keeps a
  single-saint feast out.

With the title check, no one-saint pick on a reviewed feast is wrong, at any threshold from
0.85 to 0.90 and with tags in either order.

| | Shortlist and ranking | + one saint of several |
|---|---|---|
| 104 reviewed: assigned / unsafe | 16 / 0 | **26 / 0** |
| 12 original cases: assigned / unsafe | 2 / 0 | 3 / 0 |
| 73 unreviewed: assigned | 24 | 34 |

Of the five new one-saint picks on unreviewed feasts that differ from the stored icon:
- **Four are right:** Sarkis, Nerses the Great, Thaddeus, and Joachim and Anna. The stored
  icon for Joachim and Anna is a Resurrection icon.
- **One is debatable:** St. Theodosius for Sts. Theodosius and the Children of Ephesus, where
  the stored Seven Sleepers icon shows more of the commemoration.

The title check also costs four right picks, whose titles add a word the feast doesn't use:
- a Crucifixion for Longinus and his companions;
- Ephrem, which the feast spells Ephraim;
- King David Praying;
- Mesrop Mashtots of Kapan.

Labels: St. Gregory the Theologian for the Twelve Holy Doctors, another St. Matthew and
another St. John the Baptist are added as assignable on inspection. In the 12 original cases,
the four-saint feast with Gregory of Tatev now has his icons assignable, as the 2026 review
chose.

## In production

`FEAST_ICON_MATCH_ENGINE` chooses who decides a new feast's icon in
`match_icon_to_feast_task`:

| Value | Who writes | Notes |
|---|---|---|
| `llm` (default) | the LLM matcher, through the icon match router | unchanged |
| `clef_shadow` | the LLM matcher | Clef runs beside it and logs `Clef shadow for feast …: llm writes X, clef would write Y`, marked `[differs]` when they disagree |
| `clef` | the Clef judge | see below |

Whichever engine runs, only a complete outcome's first, auto-assignable match is written,
under the same row lock and stale-row checks.

With `clef`:
- The judge sees each icon's `cached_thumbnail_url`, the 400×300 rendition it was calibrated
  on.
- An icon without a web URL (local development stores a relative path) is judged on title and
  tags alone, which the thresholds were not calibrated for.
- A Clef outage makes the outcome unavailable and writes nothing. It does not fall back to the
  LLM.

Suggested rollout: run `clef_shadow` for a few weeks of new feasts, read the `[differs]` lines,
then switch to `clef`. The full `clef` model is used; the thresholds were not calibrated on
`clef-flash`.

## Next steps

- Re-run the calibration above after any change to a question, the placement rule, the
  shortlist or the catalogue's titles, and after the remaining 73 feasts are reviewed.
- Coverage now depends on ranking and shortlisting, not on the threshold (see the tables
  above): feasts whose icon shares no word with their name, and Clef's name confusions.
- Run a newer OpenAI profile through this harness for comparison.
- Extend the cases: 91 of the 2026 calendar's 177 distinct feasts already carry a stored icon.
  Reviewing those is a gold set and a production data cleanup in one pass. Against production:

  ```bash
  python manage.py export_icon_match_cases --stored-icon-only \
    --catalogue-json catalogue.json --cases-json cases.json
  ```

  Every exported case is `"reviewed": false` with empty labels and the currently stored icon
  shown for reference. Fill in the labels, flip `reviewed` to `true`, and the scorer picks the
  case up; unreviewed cases are skipped and listed in `unreviewed_cases`.
- Put the catalogue before the request in `provider_payload()`; the assess payload is ~37k
  tokens, 96% of them the catalogue, identical across feasts, so prefix caching would cut most
  of the input cost of a full run.
