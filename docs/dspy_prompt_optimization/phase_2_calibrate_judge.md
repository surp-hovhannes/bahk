# Phase 2: Calibrate the LLM Judge

Build an LLM-as-judge metric that scores reading contexts the same way human raters do. This judge will serve as the automated metric inside DSPy's optimization loop in Phase 3, so its reported quality must come from a held-out evaluation set rather than the same examples used to derive the rubric.

For the local PoC, keep this phase lightweight: use the local database, one inexpensive judge model, and enough labels to learn whether the optimization loop is promising.

## Prerequisites

Before starting this phase, verify:

- [ ] Phase 1 is complete (all gating conditions met)
- [ ] At least **20-30 `ReadingContextEvaluation` records** exist in the local database (50+ is ideal later). Check: `ReadingContextEvaluation.objects.count()` >= 20
- [ ] Evaluations span a range of ratings (not all 3s). Check that there are at least 5 ratings of 4-5 and at least 5 ratings of 1-2: `ReadingContextEvaluation.objects.filter(rating__gte=4).count()` >= 5 and `ReadingContextEvaluation.objects.filter(rating__lte=2).count()` >= 5
- [ ] Both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` exist in the local environment because settings import requires both values; the judge itself can still use a single cheap model like `gpt-4o-mini` or `claude-haiku-4-5-20251001`
- [ ] `scipy` is available in the app container -- needed for Spearman correlation
- [ ] You have read the existing `LLMPrompt` model in `hub/models.py` and understand its `applies_to` choices

## What to Build

### 1. Management Command: `calibrate_judge`

Create `hub/management/commands/calibrate_judge.py`. This command runs the full calibration loop interactively.

#### Command Arguments

```bash
docker exec bahk_devcontainer-app-1 python manage.py calibrate_judge [--judge-model MODEL] [--iterations N] [--output PATH]
```

- `--judge-model`: LLM model to use for judging (default: `gpt-4o-mini`)
- `--iterations`: Max calibration iterations before stopping (default: 5)
- `--output`: Path to save the calibrated judge prompt (default: `data/judge_prompt.txt`)

For the local PoC, start with:

- `--judge-model gpt-4o-mini` or `--judge-model claude-haiku-4-5-20251001`
- `--iterations 2` or `3` before doing more expensive tuning

#### Step-by-step Implementation

**Step 1: Load and split human labels**

```python
from hub.models import ReadingContextEvaluation

all_evals = (
    ReadingContextEvaluation.objects
    .select_related("context", "context__reading")
    .filter(evaluation_round="judge_calibration_v1")
    .order_by("?")  # random order for unbiased splits
)

all_evals = list(all_evals)
split_idx = max(5, int(len(all_evals) * 0.8))
train_evals = all_evals[:split_idx]
holdout_evals = all_evals[split_idx:]

high_rated = [e for e in train_evals if e.rating >= 4]
low_rated = [e for e in train_evals if e.rating <= 2]
```

If there are fewer than 5 high-rated or 5 low-rated, print a warning and exit -- insufficient signal to calibrate.

Also require at least 5 examples in `holdout_evals`. If not, either collect more labels or use a larger holdout fraction only after documenting the tradeoff.

**Step 2: Discover quality criteria from training labels**

Use an LLM to analyze what distinguishes high from low ratings. Build a prompt like:

```
Here are reading contexts from the TRAINING SET that human raters scored HIGHLY (4-5 out of 5):

[For each of up to 10 high-rated evals:]
Passage: {eval.context.reading.passage_reference}
Context: {eval.context.text[:500]}
Human rating: {eval.rating}
Human explanation: {eval.explanation or "No explanation provided"}

---

Here are reading contexts from the TRAINING SET that human raters scored POORLY (1-2 out of 5):

[For each of up to 10 low-rated evals:]
Passage: {eval.context.reading.passage_reference}
Context: {eval.context.text[:500]}
Human rating: {eval.rating}
Human explanation: {eval.explanation or "No explanation provided"}

---

Analyze both groups. What specific, actionable criteria distinguish the highly-rated contexts from the low-rated ones?
Return exactly 4-6 criteria, each with:
- A short name (e.g., "Narrative Specificity")
- A description of what it means
- What a score of 5 looks like for this criterion
- What a score of 1 looks like for this criterion

Return as JSON: [{"name": "...", "description": "...", "score_5": "...", "score_1": "..."}, ...]
```

Call this with the configured judge model. Parse the JSON response to get the criteria list.

**Step 3: Build the judge prompt**

Construct a rubric-based judge prompt from the discovered criteria:

```python
def build_judge_prompt(criteria: list[dict]) -> str:
    rubric_lines = []
    for c in criteria:
        rubric_lines.append(
            f"{c['name'].upper()}:\n"
            f"- {c['description']}\n"
            f"- 5: {c['score_5']}\n"
            f"- 1: {c['score_1']}\n"
        )
    rubric = "\n".join(rubric_lines)

    return f"""You are evaluating a contextual summary for a Bible reading.
You will receive a passage reference and the generated context.

Score the context on a scale of 1-5 based on these criteria:

{rubric}

IMPORTANT: Use the full 1-5 range. Do not default to middling scores.

Return a JSON object: {{"score": <1-5>, "reasoning": "<1-2 sentences>"}}"""
```

**Step 4: Select few-shot calibration examples from the training set**

Pick 3-5 examples that span the rating range (one at each of scores 1, 2, 3, 4, 5 if available). Prefer examples where the human provided an explanation. All few-shot examples must come from `train_evals`, never the holdout set. Format them as:

```
CALIBRATION EXAMPLES (scored by human evaluators):

Example 1 - Human Score: {rating}
Passage: {passage_reference}
Context: "{context_text[:400]}"
Why this scored {rating}: {explanation or auto-generated reason}
```

Append these to the judge prompt.

**Step 5: Freeze the prompt and score the held-out set**

```python
from scipy.stats import spearmanr

def run_judge(judge_prompt: str, passage_reference: str, context_text: str, model: str) -> dict:
    """Call the LLM with the judge prompt and return parsed score."""
    # Use openai or anthropic client depending on model
    # System message = judge_prompt
    # User message = f"Passage: {passage_reference}\n\nContext:\n{context_text}"
    # Parse JSON response for "score" key
    ...

judge_scores = []
human_scores = []

for eval in holdout_evals:
    result = run_judge(
        judge_prompt=judge_prompt,
        passage_reference=eval.context.reading.passage_reference,
        context_text=eval.context.text,
        model=judge_model,
    )
    judge_scores.append(result["score"])
    human_scores.append(eval.rating)

correlation, p_value = spearmanr(judge_scores, human_scores)

adjacent_agreement = sum(
    1 for j, h in zip(judge_scores, human_scores) if abs(j - h) <= 1
) / len(judge_scores)

exact_agreement = sum(
    1 for j, h in zip(judge_scores, human_scores) if j == h
) / len(judge_scores)
```

Print a summary:

```
Judge Calibration Results (Iteration 1):
  Spearman correlation:  0.72
  Adjacent agreement:    83%  (target: >= 80%)
  Exact agreement:       48%
  Score distribution:    Judge mean=3.4 std=1.1 | Human mean=3.2 std=1.3
  Evaluation split:      24 train / 6 holdout
```

**Step 6: Diagnose disagreements (if targets not met)**

If adjacent agreement < 80% or Spearman < 0.7 on the holdout set, find and print the worst disagreements:

```python
disagreements = sorted(
    zip(holdout_evals, judge_scores, human_scores),
    key=lambda x: abs(x[1] - x[2]),
    reverse=True,
)

print("\nWorst disagreements (judge vs human):")
for eval_obj, j_score, h_score in disagreements[:10]:
    print(f"  Passage: {eval_obj.context.reading.passage_reference}")
    print(f"  Human: {h_score}, Judge: {j_score} (diff: {j_score - h_score:+d})")
    if eval_obj.explanation:
        print(f"  Human explanation: {eval_obj.explanation[:200]}")
    print(f"  Context: {eval_obj.context.text[:150]}...")
    print()
```

Also check for systematic biases:

```python
# Does the judge consistently score higher or lower?
mean_diff = sum(j - h for j, h in zip(judge_scores, human_scores)) / len(judge_scores)
print(f"  Mean difference (judge - human): {mean_diff:+.2f}")

# Score range compression?
import statistics
print(f"  Judge score std: {statistics.stdev(judge_scores):.2f}")
print(f"  Human score std: {statistics.stdev(human_scores):.2f}")
```

**Step 7: Iterate**

Based on the diagnosis, adjust the judge prompt using only what you learned from the training set plus the pattern of holdout errors. Do not add the holdout examples themselves as new few-shot examples during the same calibration run; that would invalidate the held-out metric. Common adjustments:

| Diagnosis | Adjustment |
|-----------|------------|
| Judge scores too high on average | Add: "Be critical. Many contexts will deserve a 2 or 3." |
| Judge scores compressed (all 3-4) | Add: "You MUST use the full 1-5 range. Aim for a roughly uniform distribution." |
| Judge misses factual errors | Add a few-shot example of a context with a factual error that humans rated 1-2 |
| Judge penalizes brevity | Adjust depth criterion: "Brevity is acceptable when the content is specific and accurate." |
| Systematic bias on certain books | Add criterion: "Score should not depend on which biblical book is referenced." |

After adjusting, re-run Step 5 on the same holdout set. Repeat until targets are met or `--iterations` limit is reached.

**Step 8: Save the calibrated judge prompt**

```python
output_path = options.get("output", "data/judge_prompt.txt")
with open(output_path, "w") as f:
    f.write(final_judge_prompt)

# Optionally save as an LLMPrompt row
LLMPrompt.objects.update_or_create(
    applies_to="judge",
    active=True,
    defaults={
        "model": judge_model,
        "role": "Judge for reading context quality",
        "prompt": final_judge_prompt,
    },
)
```

Print final summary and file path.

### 2. Add `applies_to` Choice for Judge

In `hub/models.py`, the `LLMPrompt` model has `APPLIES_TO_CHOICES`. Add `"judge"` as a valid choice:

```python
APPLIES_TO_CHOICES = [
    ("readings", "Readings"),
    ("feasts", "Feasts"),
    ("judge", "Judge"),  # Add this
]
```

This requires a migration if the field uses `choices` for validation.

## Important Calibration Rule

The command should report two kinds of metrics separately:

- **Training diagnostics**: useful while iterating on the rubric
- **Held-out metrics**: the only metrics that count toward the gating threshold

If you later want a better final judge after calibration succeeds, run a second pass that rebuilds the final prompt using the full labeled set and save it as the production judge, but keep the held-out evaluation report from the pre-final pass as the trustworthy calibration result.

### 3. Dependencies

Add to `requirements.txt`:

```
scipy
```

After updating `requirements.txt`, make sure the local dev container has the new dependency available before running the command.

## Gating Conditions for Success

This phase is complete when ALL of the following are true:

- [ ] **`calibrate_judge` command runs locally**: `docker exec bahk_devcontainer-app-1 python manage.py calibrate_judge` executes without errors, loads evaluations from the local database, splits them into train and holdout subsets, and produces output
- [ ] **Criteria are discovered**: The command prints 4-6 quality criteria derived from human label patterns
- [ ] **Agreement targets met**: The calibrated judge achieves:
  - Held-out Spearman correlation >= 0.7
  - Held-out adjacent agreement (within 1 point) >= 80%
- [ ] **Judge prompt is saved**: The final judge prompt is saved to `data/judge_prompt.txt` AND/OR as an `LLMPrompt` row with `applies_to='judge'`
- [ ] **Judge prompt includes few-shot examples**: The saved prompt contains 3-5 scored examples spanning the 1-5 range, all drawn from the training subset only
- [ ] **Disagreement report is clean**: The top 10 worst held-out disagreements have been reviewed and none reveal a systematic flaw the judge cannot handle
- [ ] **Relevant tests pass**: Run focused Django tests via the container, for example `docker exec bahk_devcontainer-app-1 python manage.py test tests.unit.hub --settings=tests.test_settings`

### Proceeding to Phase 3

Phase 3 depends on:
1. A calibrated judge prompt file at the expected path (or an active `LLMPrompt` with `applies_to='judge'`)
2. At least 20-30 human evaluations in the local database, with clear filtering metadata so the optimization step can reuse the intended label set without mixing in unrelated exploratory or validation comparisons
