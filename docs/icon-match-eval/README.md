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

## Next steps

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
