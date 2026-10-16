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
are reproducible as the live catalogue grows.

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
  `correct`**, so a fallback is only assignable when a reviewer adds it here. Policy: a portrait
  of the commemorated saint is assignable when no more specific depiction (such as the event
  itself) exists; a scene in which the saint merely appears is not.

## Scoring

Per case, the top-ranked match gets one verdict: `exact`, `acceptable`, `wrong`, `abstained`
(nothing returned, and nothing fits), `missed` (nothing returned, but something fits) or
`error`. `desirable_count` is `exact + acceptable + abstained`; `by_expectation` breaks the
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

## Next steps

- Label more feasts, then set `ASSIGN_THRESHOLD` from them rather than from these 12.
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
