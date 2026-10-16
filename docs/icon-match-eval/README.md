# Icon matching: labelled-case evaluation (Sept 2026)

Reproducible evidence for the changes in the PR that added this directory. It exists so the
next person — or the next agent — can re-run the measurement, disagree with the labels, and
extend the case set rather than starting from a blank page.

## Why this exists

`evaluate_icon_matching_paired` compares two profiles but reports only *agreement*: its own
output calls itself "Exploratory paired audit; **no accuracy labels or winner**". It can tell
you two arms disagreed; it cannot tell you which was right. That is why earlier attempts at
improving the matcher were hard to judge.

`evaluate_icon_matching_cases` adds the missing half: hand-labelled cases, so a run reports
accuracy. The labels are one reviewer's editorial judgement about a specific catalogue —
**not ground truth** — and are meant to be argued with and corrected.

## Method

**Catalogue.** `hub/tests/data/icon_catalogue_snapshot_2026-09-13.json` — all 440 production
icons (id, title, tags) from the public `/api/icons/` endpoint on 2026-09-13. Committed so
runs are reproducible as the live catalogue grows.

**Cases.** `hub/tests/data/icon_match_cases_armenian_12.json` — 12 feasts drawn from the 2026
calendar, deliberately *not* a random sample. They were chosen to cover three situations the
matcher has to tell apart:

| Expectation | Cases | What a good answer looks like |
|---|---|---|
| `exact_exists` | 4 | The catalogue holds a real depiction; top-1 should be it |
| `fallback_only` | 3 | No exact depiction; a portrait/related icon is a reasonable suggestion |
| `nothing_suitable` | 5 | Nothing in the catalogue fits; returning nothing is the right answer |

Each case carries a `note` explaining the label so it can be challenged.

**Scoring.** Per case, the top-ranked recommendation is classified `exact` / `acceptable` /
`wrong` / `silent` / `error`. Two aggregate numbers matter more than the tally:

- **`unsafe_assignment_count`** — cases where the pipeline marked something `auto_assignable`
  that the reviewer says does not fit. This is the costly error; it writes to production.
- **`complete_count`** — cases reaching `status == "complete"`. `icon_tasks.py:79` refuses to
  assign anything that is not `complete`, so a low number means assignment is blocked
  regardless of how good the ranking is.

Note `silent` is *good* on a `nothing_suitable` case and *bad* on the others; read it against
the `expectation` column, not on its own.

## Running it

```bash
python manage.py evaluate_icon_matching_cases \
  --catalogue-json hub/tests/data/icon_catalogue_snapshot_2026-09-13.json \
  --cases-json     hub/tests/data/icon_match_cases_armenian_12.json \
  --profile        sonnet-control-v1 \
  --output-json    /tmp/result.json \
  --live
```

Without `--live` no network call is made and every case reports `error` — the same offline
guard the paired evaluator uses. A 12-case live arm costs roughly **$1–2** on Sonnet 5.
Registered profiles: `control-v1`, `luna-v1`, `luna-v2`, `sonnet-control-v1`,
`sonnet-rubric-v1`.

## Results

Committed verbatim in this directory:

- `results-production-gpt-4.1-mini.json` — the deployed matcher, via `POST /api/icons/match/`
- `results-sonnet-control-v1.json` — arm A: Sonnet 5, **prompts unchanged**
- `results-sonnet-rubric-v1.json` — arm B: Sonnet 5 + the curator rubric

Arms A and B differ by **exactly one thing**: the rubric text appended to the `assess` and
`verify` stage prompts. Same model, same ranking function, same `positive_limit`, same limits.
So A−production isolates the model and B−A isolates the prompt.

| | production (gpt-4.1-mini) | A: sonnet-5 | B: sonnet-5 + rubric |
|---|---|---|---|
| `status == "complete"` | 8 / 12 | **11 / 12** | 11 / 12 |
| Desirable top-1 | — | **9 / 12** | 8 / 12 |
| exact / acceptable | — | 3 / 4 | **4** / 4 |
| wrong top-1 | — | 3 | 3 |
| correctly silent | — | **2** | 0 |
| errors | — | 0 | 1 (timeout) |
| **unsafe assignments** | — | **0** | **0** |

Production auto-assignment is not comparable: those runs went through the public endpoint,
which builds its request without `auto_assign_policy` and therefore defaults to `"none"`
(`icons/views.py`, `hub/services/icon_matching.py`). The A/B arms use `feast_strict`.

### What changed, case by case

**The model fixed pipeline health.** Production returned `partial` on 4/12 with
`invalid_assessed_candidate` — the model's positives failed exact-quote grounding. Sonnet 5
returns `complete` with **empty diagnostics** on 11/12. Since assignment requires `complete`,
this is what unblocks assignment at all.

**The model fixed under-matching.** Three cases went from silence to a sensible suggestion:

| Feast | production | sonnet-5 |
|---|---|---|
| Beheading of St. John | silent | `St. John the Baptist` (subject_portrait, r=80) |
| Sunday of the Judge | silent | `Toros Roslin Last Judgment` (thematic) |
| Feast of the Holy Cross | silent | `Crucifixion 4` (related_specific) |

**The rubric fixed one ranking case and cost one abstention.**

- *Sts. Hripsime and Her Companions*: A ranked `St. Hripsime and St. Gayane` first
  (`related_specific`, r=65 — the wrong pairing). B ranked `St Hripsime and Companions` first
  (`exact_subject`, r=88, auto-assignable). The rubric's "every requested figure present" band
  did what it was designed to do.
- *Sts. Atom and His Soldiers*: A correctly returned nothing. B surfaced
  `40 Martyrs of Sebastia` at `thematic` r=35 — a different saint. This is the forced-scoring
  pressure the rubric creates. The relation ladder still floored it at `thematic` and the
  assignment gate refused it, so it never became an assignment.
- B widens the shortlist generally (5 candidates where A returns 1) and inflates weak scores
  (Elijah r=25 → 55).
- B is 3–5× slower (28–173s per case vs A's mostly-fast); one case ran 707s and timed out.
  The public endpoint budget is 20s.

## Known limits of this evidence

1. **n = 12, single run, no repeats.** The Hripsime win and the Atom loss are one sample each.
   LLM non-determinism is unmeasured.
2. **The labels are one reviewer's opinion and one was already wrong.** `Remembrance of the
   Prophet Elijah` was first labelled "nothing suitable" on the belief that no Elijah icon
   exists — icon 499's tags include `elijah` (he appears at the Transfiguration), so both arms
   were right and the label was not. It is corrected here. Two labels remain genuinely
   debatable and are marked in the notes: `Feast of the Holy Cross` (is `Crucifixion 4` an
   acceptable stand-in?) and `Remembrance of the Dead`.
3. **The 12 cases over-represent hard cases.** They were picked because production got them
   wrong or interestingly right. Absolute rates here will not match the full calendar.
4. **Not yet run at scale.** 177 distinct feasts exist in the 2026 calendar; a full arm is
   ~531 calls, roughly $12–15 on Sonnet 5 before prompt caching.

## Obvious next steps

- **Prompt caching.** The assess payload measures 36,865 input tokens, **96% of it the
  catalogue**, which is byte-identical across every feast. `provider_payload()` currently
  serialises `request` before `catalogue`, putting the volatile part in the cache prefix;
  reordering it would cut input cost by roughly 90% on a full run.
- **Extend the case set.** 91 of the 177 feasts already carry a stored icon; reviewing those
  diffs is both the gold set and a production data cleanup in one pass.
- **Feed the matcher more request context.** `icon_tasks.py` sends only `feast.name`, while the
  feasts API already returns `designation` and a `short_text` paragraph per feast.
