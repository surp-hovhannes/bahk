# Phase 5: Iteration Loop — Re-calibrate Judge & Re-optimize

## Summary

Six rounds of blinded human evaluation (90 total comparisons) tested whether DSPy re-optimization, updated judge criteria, and hand-crafted prompts could outperform baselines. A critical labeling bug was discovered and fixed partway through. **The hand-written prompt (#27) is the clear winner**, beating the original baseline 7-0-8 (100% win rate) and decisively outperforming all DSPy-optimized variants.

## Rounds

### Pre-bug-fix rounds (unreliable labels)

These four rounds were conducted before the display-order labeling bug was discovered. All results are unreliable — the ~50% noise floor makes them uninterpretable.

| Round | Prompt Strategy | Opt Wins | Base Wins | Ties | Win Rate (excl. ties) |
|-------|----------------|----------|-----------|------|-----------------------|
| opt_v1 | DSPy MIPROv2 (initial, from Phase 4) | 6 | 8 | 1 | 43% |
| opt_v2 | DSPy + stricter judge (anti-summarization, anti-over-quoting) | 5 | 5 | 5 | 50% |
| opt_v3 | DSPy + 70-word cap + modern English quotes + no headers | 6 | 7 | 2 | 46% |
| hand_v1 (buggy) | Hand-crafted prompt distilled from human feedback | 4 | 5 | 6 | 44% |

### Post-bug-fix rounds (reliable labels)

| Round | Prompt Strategy | Baseline | Opt Wins | Base Wins | Ties | Win Rate (excl. ties) |
|-------|----------------|----------|----------|-----------|------|-----------------------|
| hand_v1 | Hand-crafted prompt (#27) vs original baseline (#21) | #21 | **7** | **0** | 8 | **100%** |
| dspy_v1 | DSPy MIPROv2 (#28) vs hand-written (#27) | #27 | 0 | **11** | 4 | **0%** |
| dspy_v2 | DSPy MIPROv2 + conciseness penalty (#29) vs hand-written (#27) | #27 | 3 | **6** | 6 | **33%** |

## Changes per round

**opt_v2** (LLMPrompt #25):
- Strengthened CONTEXTUAL SCOPE in judge: hard rule capping score at 2 if passage content is summarized
- Rewrote CITATION PRECISION: 1-3 quotes ideal, 4+ caps at score 3
- Added READABILITY AND FLOW criterion (new)
- Added 2 calibration examples: passage summarization failure (Isaiah 2:5-11) and over-quoting failure (Lamentations 3:22-56)
- Updated DSPy signature: "Include 1-3 direct Scripture quotes maximum"

**opt_v3** (LLMPrompt #26):
- Tightened word limit from 90 to 70 words
- Added modern English quote guidance (paraphrase archaic KJV phrasing)
- Added "no headers, titles, or labels" to signature and judge
- Judge now penalizes archaic KJV language in quotes

**hand_v1** (LLMPrompt #27):
- Bypassed DSPy entirely; hand-crafted prompt based on patterns from 30+ human evaluations
- Key instructions: storytelling of preceding events, at most one Scripture quote, end with transition sentence, no headers/markdown, introduce book context for first-chapter passages

**dspy_v1** (LLMPrompt #28):
- DSPy signature updated to mirror hand-written prompt's key insights (70 words, 1 quote max, no headers, storytelling focus)
- MIPROv2 light optimization: best trial 71.4% (Instruction 2 + Few-Shot Set 2/5)
- Compared against hand-written #27 as baseline

**dspy_v2** (LLMPrompt #29):
- Added word count penalty to metric function: outputs >75 words get linearly penalized (up to 60% penalty at 100+ words)
- Added hard verbosity rule to judge: >100 words caps score at 3, >120 words caps at 2
- Added "shoehorned quote" penalty to judge
- MIPROv2 light optimization: best trial 39.79% (Instruction 2 + Few-Shot Set 5)

## Critical Bug: Randomized Display Labels Not Remapped

### The bug

A labeling bug in the validation comparison pipeline caused approximately half of all preference labels to be inverted, making all pre-fix evaluation data unreliable.

**Root cause** (`hub/views/admin.py`):

1. **Generation** (`generate_validation_contexts.py:171-176`): Always stores `context_a = baseline, context_b = optimized`
2. **Display** (`admin.py:207-209`): Randomizes display order 50% of the time for blinding — correct for preventing evaluator bias
3. **Storage** (`admin.py:151`): Stores `preferred = "a"` or `"b"` in **display order**, not DB order — **bug**
4. **Analysis** (`analyze_validation.py:54-68`): Interprets `preferred` in **DB order**, always assuming `prompt_b = optimized`

When the display is swapped and the evaluator picks the optimized context (shown as "Context A"), `preferred="a"` is stored. But `context_a` in the DB is always baseline, so the analysis counts it as a baseline win.

### Proof

**Daniel 7:2-27** (hand_v1, stored as `preferred=a` → counted as baseline win):
- `context_a` (baseline): Describes the four beasts, the Ancient of Days, the Son of Man — all content FROM Daniel 7:2-27
- `context_b` (optimized): Describes Nebuchadnezzar's dream from chapter 2, setting up the vision without revealing it
- Evaluator's explanation: "Great storytelling and context and **no summarization of current passage**"
- The evaluator clearly preferred context_b (no summarization), but it was displayed as "Context A" due to the random swap

**1 Timothy 1:1-11** (hand_v1, stored as `preferred=a` → counted as baseline win):
- `context_a` (baseline): No direct quotes with quotation marks
- `context_b` (optimized): Contains `"my own son" (1 Timothy 1:2)` — a direct quote with verse reference
- Evaluator's explanation: "Great flow, concise, a **tasteful Bible verse quote** and final sentence sets the stage"
- The "tasteful Bible verse quote" only exists in context_b

### Fix

Applied in `hub/views/admin.py`: the POST handler now compares `context_a_real_id` (the ID of what was displayed as "Context A") against `comp.context_a_id` (the DB record). If they don't match, the display was swapped and `preferred` is remapped (`a` ↔ `b`) before storage. Ties are unaffected.

```python
# Remap display-order preference back to DB order.
if preferred in ("a", "b"):
    displayed_a_is_db_a = (str(comp.context_a_id) == str(context_a_real_id))
    if not displayed_a_is_db_a:
        comp.preferred = "b" if preferred == "a" else "a"
```

## Per-Passage Results (Post-Bug-Fix Rounds Only)

| Passage | hand_v1 vs #21 | dspy_v1 vs #27 | dspy_v2 vs #27 |
|---------|---------------|----------------|----------------|
| 2 Chronicles 4:2-6 | T | B | B |
| 2 Maccabees 6:18-7:42 | T | B | B |
| Baruch 4:36-5:9 | T | B | T |
| Daniel 7:2-27 | O | B | B |
| Isaiah 2:5-11 | T | B | B |
| Joel 2:12-13 | O | T | O |
| Joshua 1:1-9 | T | T | T |
| Lamentations 3:22-56 | O | B | B |
| Micah 7:7-10 | O | T | T |
| Psalms 23:1-6 | T | B | T |
| St. James 1:13-27 | O | B | T |
| Colossians 3:16-4:4 | O | T | O |
| Hebrews 11:32-40 | O | B | T |
| 1 Timothy 1:1-11 | T | B | O |
| 2 Peter 2:9-22 | T | B | T |

*B = baseline win, O = optimized win, T = tie*

## Word Count Analysis

The dominant feedback across DSPy rounds was "more concise." Here are the average word counts:

| Prompt | Avg Words | Range |
|--------|-----------|-------|
| Original baseline (#21) | ~106 | 74-140 |
| Hand-written (#27) | ~106 | 74-140 |
| DSPy dspy_v1 (#28) | ~155 | 110-190 |
| DSPy dspy_v2 (#29) | ~139 | 119-167 |

Despite explicit "70 words max" instructions and a word count penalty in the metric, DSPy-optimized prompts consistently produce outputs 30-50% longer than the hand-written prompt. The word count penalty improved from dspy_v1 to dspy_v2 (155 → 139 avg, and win rate from 0% → 33%), but could not close the gap.

## Key Findings

### 1. Hand-written prompt is the clear winner

LLMPrompt #27 beat the original baseline 7-0-8 (100% win rate excluding ties) and decisively outperformed both DSPy variants. It should be activated in production.

### 2. DSPy MIPROv2 is poorly suited for this task

DSPy's optimization excels at structured reasoning tasks (chain-of-thought, RAG, classification) where more structure improves quality. For creative/prose generation where **brevity and naturalness** are paramount, DSPy's tendency to produce verbose, bullet-pointed instructions backfires:

- **Instruction style mismatch**: DSPy generates structured instructions with bullet points and explicit requirements. This encourages the LLM to be comprehensive (addressing each point) rather than concise.
- **Word count penalty only affects optimization scoring**: The penalty helps DSPy select better instruction/few-shot combinations during MIPROv2's search, but at generation time the LLM doesn't know about the penalty.
- **Few-shot examples may reinforce verbosity**: Training examples from human evaluations tend to be longer (they were the "good" examples), which may set length expectations for the LLM.

### 3. Conciseness is the single most important differentiator

Across all post-fix evaluation rounds, "more concise" was the most common explanation for baseline wins. The hand-written prompt's plain prose style naturally produces more focused output, while DSPy's structured instructions encourage verbosity.

### 4. The automated judge doesn't capture human preferences well

The judge gave DSPy dspy_v2 a 39.79% metric score (highest trial) vs 24.6% baseline during optimization, suggesting DSPy was improving quality. But human evaluation showed the opposite (33% win rate). The judge's criteria are correct but don't sufficiently weight the conciseness preference that dominates human judgment.

### 5. Storytelling quality remains hard to encode

Evaluator explanations praise "great storytelling", "compelling context", and "tasteful verses" — qualities that emerge from a good writer's instinct rather than rule-following. The hand-written prompt was crafted by reading 30+ evaluations and internalizing the patterns; DSPy's search over instruction space cannot replicate this.

## Conclusion

**Activate LLMPrompt #27 (hand-written) in production.** DSPy MIPROv2 is not the right tool for optimizing creative prose generation prompts. The hand-written prompt, distilled from 30+ human evaluations, captures qualitative preferences (storytelling, conciseness, natural quoting) that automated optimization cannot discover.

For future prompt improvements, iterate manually: generate outputs, evaluate blindly, identify patterns in evaluator feedback, and rewrite the prompt by hand. The evaluation infrastructure (blinded A/B comparison with label-order remapping) is now reliable and can support this workflow.

## Files Modified

| File | Change |
|------|--------|
| `data/judge_prompt.txt` | Strengthened CONTEXTUAL SCOPE, rewrote CITATION PRECISION, added READABILITY AND FLOW, added CONCISENESS criterion, added calibration examples, added archaic language/header/verbosity/shoehorned-quote penalties |
| `hub/dspy_modules.py` | Updated signature (70-word cap, 1 quote max, modern English, no headers); added `_word_count_multiplier()` penalty to metric |
| `hub/views/admin.py` | **Bug fix**: remap display-order preference to DB order before storing |

## LLMPrompts Created

| ID | Round | Status |
|----|-------|--------|
| #25 | opt_v2 | Inactive |
| #26 | opt_v3 | Inactive |
| #27 | hand_v1 | **Active** |
| #28 | dspy_v1 | Inactive |
| #29 | dspy_v2 | Inactive |
