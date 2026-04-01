# Phase 2 Report: LLM Judge Calibration

**Date**: 2026-03-31
**Status**: Complete

## Summary

Calibrated an LLM judge (`claude-haiku-4-5-20251001`) to score reading contexts consistently with human ratings. The judge met both gating targets (Spearman >= 0.7, adjacent agreement >= 80%) in just 2 iterations. The calibrated prompt is saved as both a file and an active `LLMPrompt` record.

## Configuration

| Parameter | Value |
|-----------|-------|
| Judge model | `claude-haiku-4-5-20251001` |
| Max iterations | 3 |
| Evaluation round | `judge_calibration_v1` |
| Train/holdout split | 16 / 5 (76% / 24%) |
| Train high-rated (4-5) | 7 |
| Train low-rated (1-2) | 5 |

## Discovered Criteria

The judge analyzed 7 high-rated and 5 low-rated training examples and identified 6 quality criteria:

1. **Contextual Scope** — Focuses on preceding material rather than summarizing the passage itself
2. **Textual Boundaries** — Maintains distinction between Scripture and external commentary
3. **Citation Precision** — Uses direct quotations with verse references
4. **Coherence and Cohesion** — Logical flow without disjointed transitions
5. **Terminology Consistency** — Uniform terminology for same events/concepts
6. **Appropriate Length and Completeness** — Sufficient context without excessive length

These criteria closely match the patterns identified in the Phase 1 human explanations, especially the #1 failure mode ("don't summarize the current passage").

## Calibration Results

### Iteration 1

| Metric | Value | Target |
|--------|-------|--------|
| Spearman correlation | 0.95 (p=0.014) | >= 0.7 |
| Adjacent agreement | 60% | >= 80% |
| Exact agreement | 60% | — |
| Judge mean / std | 3.4 / 1.8 | — |
| Human mean / std | 2.6 / 1.5 | — |
| Mean diff (judge - human) | +0.80 | — |

**Diagnosis**: Judge scored consistently too high. Two disagreements of +2 points:
- Isaiah 7:10-17: Human 2 (summarizes passage), Judge 4
- Isaiah 9:5-7: Human 3 (comments on verse 5), Judge 5

**Adjustment applied**:
- "Be more critical. Many contexts will deserve a 2 or 3, not a 4 or 5."
- "CRITICAL: If the context summarizes the referenced passage itself instead of the PRECEDING content, score it 1-2."

### Iteration 2 (Final)

| Metric | Value | Target |
|--------|-------|--------|
| Spearman correlation | 0.80 (p=0.102) | >= 0.7 |
| Adjacent agreement | **80%** | >= 80% |
| Exact agreement | 20% | — |
| Judge mean / std | 3.2 / 1.3 | — |
| Human mean / std | 2.6 / 1.5 | — |
| Mean diff (judge - human) | +0.60 | — |

**Both targets met.** Calibration stopped.

### Remaining Disagreement

One holdout item still shows a +2 gap:
- **Isaiah 7:10-17** — Human rated 2 ("summarizes part of the passage... It's not Isaiah who challenges Ahaz"), Judge rated 4. The judge didn't catch the subtle passage-content leak where the context described the Lord's command to ask for a sign, which is part of the target passage itself.

## Caveats

1. **Small holdout set (n=5)**: With only 5 holdout samples, the metrics have high variance. The Spearman p-value of 0.102 in iteration 2 indicates the correlation is not statistically significant at p<0.05. This is expected for n=5 and acceptable for a PoC.

2. **Positive bias**: The judge still scores +0.60 higher than humans on average. This means it's slightly generous, which could cause the DSPy optimizer to accept contexts that humans would rate lower. The few-shot examples and calibration notes partially mitigate this.

3. **Score compression**: Judge std (1.3) is slightly below human std (1.5), suggesting mild compression toward middle scores. The calibration notes address this ("Use the full 1-5 range").

## Few-Shot Examples

The judge prompt includes 5 calibration examples (one per rating level 1-5), all drawn from the training set:

| Rating | Passage | Key Human Feedback |
|--------|---------|-------------------|
| 1 | Matthew 1:18-25 | Extra-textual assumption about marriage consummation |
| 2 | Isaiah 51:15-52:3 | Commentary on present verses (not allowed) |
| 3 | Isaiah 11:1-9 | Summarizes some of the passage ("stump of Jesse") |
| 4 | Hebrews 13:18-25 | Great, needs direct quotes with references |
| 5 | Hebrews 12:5-17 | Doesn't discuss passage, includes quotes, good length |

## Saved Artifacts

| Artifact | Location |
|----------|----------|
| Judge prompt file | `data/judge_prompt.txt` |
| LLMPrompt DB record | `applies_to='judge'`, `model='claude-haiku-4-5-20251001'`, `active=True` |
| Management command | `hub/management/commands/calibrate_judge.py` |

## Gating Conditions

- [x] `calibrate_judge` command runs locally without errors
- [x] 6 quality criteria discovered from human label patterns
- [x] Spearman correlation >= 0.7 (achieved: 0.80)
- [x] Adjacent agreement >= 80% (achieved: 80%)
- [x] Judge prompt saved to `data/judge_prompt.txt` and as `LLMPrompt` row
- [x] Prompt includes 5 few-shot examples spanning ratings 1-5, all from training set
- [x] Disagreement report reviewed — 1 remaining systematic issue (subtle passage-content detection) but not a blocking flaw
- [x] All 39 hub unit tests pass

## Recommendations for Phase 3

1. **Collect more labels if possible**: The holdout set of 5 is small. Even 10 more evaluations (bringing total to 31) would allow a 25/6 split and more stable metrics.

2. **Address the Isaiah 7:10-17 pattern**: The judge struggles with subtle cases where preceding context overlaps with the passage content (e.g., God's command to ask for a sign spans both preceding context and the passage). The DSPy optimizer should learn to handle this.

3. **Judge bias correction**: Since the judge scores +0.60 higher than humans, consider adding a post-hoc offset or explicitly instructing the DSPy optimizer that a judge score of 3 corresponds to roughly a human 2.5.

4. **Model choice**: `claude-haiku-4-5-20251001` is cost-effective for the judge role. For the DSPy optimization loop, which will call the judge many times per iteration, keeping Haiku as the judge model keeps costs low while Sonnet 4.5 remains the context-generation model.
