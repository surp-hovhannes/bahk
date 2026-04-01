# Phase 1 Report: Human Labeling

**Date**: 2026-03-31
**Status**: Complete

## Summary

Built the evaluation infrastructure and collected 21 human-labeled reading context evaluations. The dataset has good rating distribution and detailed explanations that will inform judge calibration in Phase 2.

## What Was Built

### Models
- `ReadingContextEvaluation` — 1-5 star rating with evaluator, round tag, and free-text explanation
- `ReadingContextComparison` — A/B preference between two contexts (available for Phase 4 validation)
- Migration: `0050_add_reading_context_evaluation_and_comparison`

### Staff Pages
- `/hub/evaluate-contexts/` — Sequential rating UI with progress bar, passage info, and 1-5 radio buttons
- `/hub/compare-contexts/` — Blinded A/B comparison with randomized position assignment

### API Endpoints
- `POST /api/readings/<pk>/evaluate/` — Staff-only rating endpoint
- `POST /api/readings/<pk>/compare/` — Staff-only comparison endpoint

### Admin
- Both models registered in Django admin with filters and raw_id_fields

### Tests
- 29 tests covering models (validation, cascade, auto-populate), views (auth, GET, POST, edge cases), and API endpoints (auth, CRUD, error handling) — all passing

## Data Collection

### Setup
- **Model**: `claude-sonnet-4-5-20250929` (LLMPrompt ID=21)
- **Prompt**: Custom Oriental Orthodox perspective prompt focusing on preceding-context summarization
- **Contexts generated**: 50 new + 10 pre-existing = 60 total active ReadingContexts
- **Readings imported**: 1,520 readings for all of 2026 (Armenian Apostolic Church calendar)

### Prompt Used for Context Generation

**Role**: Summarize the events or dialogue preceding a given Bible passage to provide clear and accessible context for a novice to intermediate reader, adhering to the perspective of the Oriental Orthodox Church.

**Key instructions**: Single paragraph, active voice, <=90 words, no spoilers of current passage, fact-based, identify speaker/audience when applicable.

### Evaluation Dataset

| Metric | Value |
|--------|-------|
| Total evaluations | 21 |
| Evaluation round | `judge_calibration_v1` |
| Evaluator | 1 (staff user) |
| Average rating | 3.05 / 5 |

### Rating Distribution

| Rating | Count | Percentage |
|--------|-------|------------|
| 1 (Poor) | 2 | 9.5% |
| 2 (Fair) | 6 | 28.6% |
| 3 (Good) | 5 | 23.8% |
| 4 (Very Good) | 5 | 23.8% |
| 5 (Excellent) | 3 | 14.3% |

Distribution spans the full 1-5 range with 8 ratings <= 2 and 8 ratings >= 4. This meets the Phase 2 prerequisite of at least 5 in each tail.

### Key Patterns from Human Explanations

Themes that emerged from the evaluator's free-text explanations:

1. **Summarizing the current passage is the #1 failure mode** — Multiple ratings of 1-2 explicitly flag this: "This just summarized the current reading. This is not allowed." The prompt instructs to summarize *preceding* content, not the referenced passage.

2. **Direct quotes with verse references are valued** — Ratings of 4-5 note: "would be good to put direct quotes in quotation marks with verse references." High-rated contexts that include quoted text scored well.

3. **Extra-textual claims penalized** — Rating 1 for Matthew 1:18-25: "How do you know if Joseph consummated the marriage? This is extra-textual. Do NOT assume."

4. **Beginning-of-book passages are hard** — When a passage starts at chapter 1, there is no preceding narrative to summarize. The evaluator noted this needs different handling (book background is acceptable).

5. **Inconsistent terminology noticed** — "Refers to 'Last Supper' and 'Lord's Supper' (inconsistent terminology)."

6. **Commentary vs. summary distinction matters** — "Good summary but comments a little on verse 5... Should also put direct quotes."

## Gating Conditions

- [x] Local comparison workflow works (evaluate-contexts page functional)
- [x] Durable ratings exist (21 ReadingContextEvaluation records in DB)
- [x] Label schema is stable (1-5 scale with explanations confirmed useful)
- [x] Minimal persistence works (migration applied, tables exist)
- [x] Optional tooling justified (rating page + API needed for efficient labeling)
- [x] Experiment metadata captured (all tagged `judge_calibration_v1`)
- [x] Tests pass (29/29)

## Files Created/Modified

| File | Action |
|------|--------|
| `hub/models.py` | Added `ReadingContextEvaluation`, `ReadingContextComparison` |
| `hub/admin.py` | Registered both new models |
| `hub/views/admin.py` | Added `evaluate_reading_contexts`, `compare_reading_contexts` views |
| `hub/views/evaluations.py` | New — API endpoints for evaluation and comparison |
| `hub/urls.py` | Added routes for new views and API endpoints |
| `hub/templates/admin/evaluate_reading_context.html` | New — rating UI template |
| `hub/templates/admin/compare_reading_contexts.html` | New — A/B comparison template |
| `hub/migrations/0050_add_reading_context_evaluation_and_comparison.py` | New migration |
| `tests/unit/hub/test_evaluations.py` | New — 29 tests |

## Recommendations for Phase 2

1. The human explanations are rich enough to derive quality criteria automatically. Key dimensions to expect: passage-scope accuracy, use of direct quotation, factual grounding, and appropriate handling of dialogue attribution.

2. With 21 evaluations, an 80/20 split gives ~17 train / 4 holdout. This is tight for holdout metrics. Consider collecting 9 more evaluations to reach 30 (24 train / 6 holdout) if the initial calibration run shows unstable holdout metrics.

3. The strongest signal in the labels is the "don't summarize the current passage" rule. The judge must learn this distinction — it accounts for most 1-2 ratings.
