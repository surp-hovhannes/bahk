# Phase 3 Report: DSPy Optimization

**Date**: 2026-03-31
**Status**: Complete

## Summary

Ran MIPROv2 prompt optimization using the calibrated LLM judge from Phase 2 and 8 high-rated human evaluations as training data. The optimizer discovered an instruction that improved average judge score from 0.571 to 0.750 (+31.3%). The optimized prompt is saved as an inactive `LLMPrompt` for Phase 4 comparison.

## Configuration

| Parameter | Value |
|-----------|-------|
| Generation model | `claude-sonnet-4-5-20250929` |
| Judge model | `claude-haiku-4-5-20251001` |
| MIPROv2 mode | `light` (10 trials) |
| Threads | 2 |
| Dataset | 8 examples (1 train / 7 val) |
| DSPy version | 3.1.3 |

## Initial Run: Generic Instruction (Failed)

The first run used a generic signature docstring ("Generate contextual understanding for a scripture reading passage"), which lacked the critical constraints from the production prompt. Results:

- Baseline avg: 0.250 (judge rated all outputs 2/5)
- Optimized avg: 0.250
- Improvement: 0.0%

The generated contexts violated the "don't summarize the current passage" rule because the instruction didn't mention this constraint. The judge correctly penalized all outputs.

## Second Run: Seeded Instruction (Success)

After updating the DSPy signature with key constraints from the production prompt (Oriental Orthodox perspective, preceding context only, 90 words max, direct quotes with references), the optimizer had a strong foundation to iterate on.

### Baseline Performance

| Passage | Score |
|---------|-------|
| Isaiah 4:2-6 | 0.750 |
| Hebrews 13:18-25 | 0.250 |
| Luke 22:24-30 | 0.000 |
| Luke 2:8-20 | 0.750 |
| John 10:11-16 | 1.000 |
| Hebrews 12:5-17 | 1.000 |
| Luke 1:26-38 | 0.250 |
| **Average** | **0.571** |

### MIPROv2 Optimization

10 trials evaluated combinations of 3 instruction candidates and 6 few-shot sets. Best score achieved: 25.0% (normalized MIPROv2 metric across all 7 val examples). Scores were stable across most trials (25.0%), with two trials scoring lower (21.4%).

### Optimized Performance

| Passage | Score |
|---------|-------|
| Isaiah 4:2-6 | **1.000** (+0.250) |
| Hebrews 13:18-25 | **0.750** (+0.500) |
| Luke 22:24-30 | 0.000 (no change) |
| Luke 2:8-20 | 0.750 (no change) |
| John 10:11-16 | 0.750 (-0.250) |
| Hebrews 12:5-17 | 1.000 (no change) |
| Luke 1:26-38 | **1.000** (+0.750) |
| **Average** | **0.750** |

**Improvement: +0.179 (+31.3%)**

## What MIPROv2 Changed

### Baseline Instruction (seed)
> Summarize the events or dialogue PRECEDING a given Bible passage to provide clear and accessible context for a reader, adhering to the perspective of the Oriental Orthodox Church. Focus exclusively on what happens BEFORE the passage, never summarize the passage itself. Use active voice, keep to a single paragraph of 90 words or fewer, include direct Scripture quotes with verse references, and avoid extra-textual commentary or assumptions.

### Optimized Instruction
> You are preparing contextual study notes for seminary students and church educators who will teach this passage to their congregations tomorrow. Their ability to accurately explain Scripture depends entirely on your contextual summary. Summarize the events or dialogue PRECEDING the given Bible passage to provide clear and accessible context, adhering strictly to the perspective of the Oriental Orthodox Church. Focus exclusively on what happens BEFORE the passage—never summarize the passage itself, as doing so will confuse students about where context ends and the actual teaching begins. Use active voice, keep to a single paragraph of 90 words or fewer, include direct Scripture quotes with verse references for credibility, and avoid extra-textual commentary or assumptions. The teachers are counting on your precision to help their students properly understand how this passage fits into the larger biblical narrative.

### Key Differences

1. **Persona framing**: Added "seminary students and church educators" audience
2. **Urgency/stakes**: "will teach this passage to their congregations tomorrow"
3. **Constraint justification**: "as doing so will confuse students about where context ends and the actual teaching begins"
4. **Motivation for quotes**: "for credibility" added to verse reference instruction
5. **Closing hook**: "The teachers are counting on your precision"

These are classic prompt engineering techniques (persona + stakes + justification) that MIPROv2 discovered through Bayesian optimization rather than manual iteration.

## Artifacts Saved

| Artifact | Location |
|----------|----------|
| Optimized LLMPrompt | ID=24, `applies_to='readings'`, `active=False` |
| Full DSPy program | `data/optimized_reading_module.json` |
| DSPy module | `hub/dspy_modules.py` |
| Dataset builder | `hub/services/dspy_dataset.py` |
| Management command | `hub/management/commands/optimize_reading_prompt.py` |

## Gating Conditions

- [x] `hub/dspy_modules.py` exists with Signature, Module, and metric
- [x] `hub/services/dspy_dataset.py` exists with `build_dataset()` and `split_dataset()`
- [x] `optimize_reading_prompt --dry-run` runs and prints dataset stats
- [x] Optimization completes locally with `--auto light`
- [x] Improvement observed: 0.571 -> 0.750 (+31.3%)
- [x] Optimized prompt saved as inactive LLMPrompt #24 with baseline role preserved
- [x] `data/optimized_reading_module.json` exists (2.4 KB)
- [x] All 39 hub unit tests pass

## Caveats

1. **Small dataset**: With only 8 high-rated examples (1 train / 7 val), the optimizer had limited signal. More human evaluations would allow MIPROv2 to explore a wider instruction space.

2. **Luke 22:24-30 scored 0.000 in both runs**: This passage may be inherently difficult (e.g., beginning of a pericope with ambiguous preceding context). Phase 4 should investigate this.

3. **John 10:11-16 regressed slightly** (1.0 -> 0.75): The optimized instruction may over-constrain some passages. Phase 4 human comparison will determine if this matters in practice.

4. **DSPy caching**: Results are cached by default. Re-running with the same inputs will return cached scores. Clear the cache for fresh evaluations.

## Recommendations for Phase 4

1. Use the existing `compare_reading_contexts` UI from Phase 1 for blinded A/B comparison between baseline (LLMPrompt #21, active) and optimized (LLMPrompt #24, inactive) prompts.

2. Generate new contexts using the optimized prompt for a sample of readings, then have the human evaluator compare them against existing baseline contexts.

3. The 31.3% improvement is promising but based on judge scores, not human preferences. Phase 4 should confirm that the judge improvement translates to human preference.
