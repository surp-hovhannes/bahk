# Phase 3: DSPy Optimization

Create the DSPy module, build a training dataset from human labels, and run MIPROv2 with the calibrated LLM judge to discover an optimized instruction for reading context generation that can be evaluated through the existing `LLMPrompt` runtime.

For the local PoC, the objective is to produce **one plausible optimized candidate** locally and save it as an inactive artifact for comparison. Activation and rollout planning come later.

## Prerequisites

Before starting this phase, verify:

- [ ] Phase 2 is complete (all gating conditions met)
- [ ] A calibrated judge prompt exists at `data/judge_prompt.txt` OR as an active `LLMPrompt` with `applies_to='judge'`. Verify: `LLMPrompt.objects.filter(active=True, applies_to='judge').exists()` or `os.path.exists('data/judge_prompt.txt')`
- [ ] The judge achieves Spearman >= 0.7 and adjacent agreement >= 80% (from Phase 2 output)
- [ ] At least **20-30 `ReadingContextEvaluation` records** exist locally (50+ recommended later). These will be split 20/80 into train/val.
- [ ] `dspy` is installed in the app container (add it to `requirements.txt`)
- [ ] Both `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` exist locally because settings import requires both, even if the optimization run uses a single provider
- [ ] You understand the active `LLMPrompt` for readings: run `LLMPrompt.objects.filter(active=True, applies_to='readings').values('model', 'prompt')` to see the current production prompt and model

## What to Build

### 1. DSPy Module: `hub/dspy_modules.py`

Create this new file with three components: the DSPy Signature, the Module, and the calibrated metric function.

#### Signature

```python
import dspy

class ReadingContextSignature(dspy.Signature):
    """Generate contextual understanding for a scripture reading passage."""
    passage_reference: str = dspy.InputField(
        desc="Biblical passage reference, e.g. 'Matthew 5:1-12'"
    )
    context: str = dspy.OutputField(
        desc="2-3 paragraph contextual summary covering the key themes and events "
             "leading up to the passage, historical and cultural context, and "
             "theological significance"
    )
```

The `OutputField` description is intentionally detailed -- MIPROv2 uses it as a starting point for instruction optimization.

#### Module

```python
class ReadingContextModule(dspy.Module):
    def __init__(self):
        self.generate = dspy.Predict(ReadingContextSignature)

    def forward(self, passage_reference: str) -> dspy.Prediction:
        return self.generate(passage_reference=passage_reference)
```

This is deliberately simple. MIPROv2 will optimize the instructions around the `Predict` call, and it can optionally bootstrap demonstrations when the dataset includes labeled outputs. Do not add a `ChainOfThought` wrapper unless you want the model to reason before answering (increases cost per call).

#### Calibrated Metric Function

```python
import json
import os
from django.conf import settings

def _load_judge_prompt() -> str:
    """Load the calibrated judge prompt from file or database."""
    # Try file first
    judge_path = os.path.join(settings.BASE_DIR, "data", "judge_prompt.txt")
    if os.path.exists(judge_path):
        with open(judge_path) as f:
            return f.read()

    # Fall back to database
    from hub.models import LLMPrompt
    judge_prompt_obj = LLMPrompt.objects.filter(
        active=True, applies_to="judge"
    ).first()
    if judge_prompt_obj:
        return judge_prompt_obj.prompt

    raise RuntimeError(
        "No calibrated judge prompt found. Run 'docker exec bahk_devcontainer-app-1 python manage.py calibrate_judge' first."
    )

# Use a cheaper/faster model for judging than for generation
JUDGE_MODEL = "gpt-4o-mini"

def reading_context_quality(example, pred, trace=None):
    """Score a generated context using the calibrated LLM judge.

    Called by MIPROv2 during optimization. Must return a float 0.0-1.0.

    Args:
        example: dspy.Example with passage_reference (and optionally context)
        pred: dspy.Prediction with context field
        trace: Optional trace for bootstrapping (not used here)
    """
    judge_prompt = _load_judge_prompt()

    judge_lm = dspy.LM(f"openai/{JUDGE_MODEL}")
    with dspy.context(lm=judge_lm):
        # Build a simple judge call
        response = judge_lm(
            messages=[
                {"role": "system", "content": judge_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Passage: {example.passage_reference}\n\n"
                        f"Context:\n{pred.context}"
                    ),
                },
            ]
        )

    # Parse score from JSON response
    try:
        raw = response[0] if isinstance(response, list) else response
        parsed = json.loads(raw)
        score = float(parsed["score"])
    except (json.JSONDecodeError, KeyError, TypeError, IndexError):
        # If parsing fails, try to extract a number
        import re
        match = re.search(r'"score"\s*:\s*(\d(?:\.\d+)?)', str(response))
        score = float(match.group(1)) if match else 3.0  # neutral fallback

    # Normalize 1-5 to 0.0-1.0
    return max(0.0, min(1.0, (score - 1.0) / 4.0))
```

**Note on caching**: DSPy caches LM calls by default. The judge will not re-score the same (passage, context) pair, which speeds up optimization.

### 2. Dataset Builder: `hub/services/dspy_dataset.py`

Create this new file. It converts DB evaluation records into `dspy.Example` objects.

```python
import random
import dspy
from hub.models import ReadingContextEvaluation


def build_dataset() -> list[dspy.Example]:
    """Build a list of dspy.Example objects from human evaluations.

    Each example has:
      - passage_reference (input): e.g. "Matthew 5:1-12"
      - context (optional label): the text of a highly-rated context

    Returns:
        List of dspy.Example objects with inputs marked.
    """
    examples = []
    seen_readings = set()

    # From individual ratings: group by reading, take the highest-rated context
    evals = (
        ReadingContextEvaluation.objects
        .select_related("context", "context__reading")
        .filter(evaluation_round="judge_calibration_v1")
        .order_by("-rating")
    )

    for eval_obj in evals:
        reading = eval_obj.context.reading
        if reading.id in seen_readings:
            continue
        seen_readings.add(reading.id)

        if eval_obj.rating < 4:
            continue

        example = dspy.Example(
            passage_reference=reading.passage_reference,
            context=eval_obj.context.text,
        )

        examples.append(example.with_inputs("passage_reference"))

    return examples


def split_dataset(
    examples: list[dspy.Example],
    train_ratio: float = 0.2,
    seed: int = 42,
) -> tuple[list[dspy.Example], list[dspy.Example]]:
    """Split examples into train/val sets.

    DSPy recommends 20% train / 80% val for prompt optimizers
    (they tend to overfit small training sets).

    Args:
        examples: Full list of examples
        train_ratio: Fraction for training (default 0.2)
        seed: Random seed for reproducibility

    Returns:
        (train_set, val_set) tuple
    """
    shuffled = list(examples)
    random.Random(seed).shuffle(shuffled)

    split_idx = max(1, int(len(shuffled) * train_ratio))
    return shuffled[:split_idx], shuffled[split_idx:]
```

### 3. Management Command: `optimize_reading_prompt`

Create `hub/management/commands/optimize_reading_prompt.py`.

For the first PoC, keep the dataset builder simple and ratings-driven. If you later add durable A/B data, you can extend `build_dataset()` to incorporate winning contexts from `ReadingContextComparison`, but that is not required for the initial local run.

#### Command Arguments

```bash
docker exec bahk_devcontainer-app-1 python manage.py optimize_reading_prompt [--auto MODE] [--threads N] [--dry-run]
```

- `--auto`: MIPROv2 search intensity: `light`, `medium`, or `heavy` (default: `medium`)
- `--threads`: Number of threads for parallel LM calls (default: 4)
- `--dry-run`: Print dataset stats and exit without running optimization

For the local PoC, start with `--auto light` and a low thread count. Increase cost and search intensity only if the first run looks promising.

#### Implementation

```python
import dspy
from django.core.management.base import BaseCommand
from django.conf import settings

from hub.models import LLMPrompt
from hub.dspy_modules import ReadingContextModule, reading_context_quality
from hub.services.dspy_dataset import build_dataset, split_dataset


class Command(BaseCommand):
    help = "Run DSPy MIPROv2 to optimize the reading context prompt"

    def add_arguments(self, parser):
        parser.add_argument(
            "--auto",
            choices=["light", "medium", "heavy"],
            default="medium",
        )
        parser.add_argument("--threads", type=int, default=4)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        # 1. Validate prerequisites
        active_prompt = LLMPrompt.objects.filter(
            active=True, applies_to="readings"
        ).first()
        if not active_prompt:
            self.stderr.write("No active LLMPrompt for readings. Aborting.")
            return

        # 2. Build dataset
        examples = build_dataset()
        if len(examples) < 10:
            self.stderr.write(
                f"Only {len(examples)} examples found. Need at least 10. "
                "Collect more human evaluations first."
            )
            return

        train, val = split_dataset(examples)
        self.stdout.write(
            f"Dataset: {len(examples)} total, {len(train)} train, {len(val)} val"
        )
        self.stdout.write(
            f"Examples with labeled context: "
            f"{sum(1 for e in examples if hasattr(e, 'context'))}"
        )

        if options["dry_run"]:
            self.stdout.write("Dry run complete. Exiting.")
            return

        # 3. Configure DSPy
        model_name = active_prompt.model
        if "gpt" in model_name:
            lm = dspy.LM(f"openai/{model_name}")
        elif "claude" in model_name:
            lm = dspy.LM(f"anthropic/{model_name}")
        else:
            self.stderr.write(f"Unsupported model: {model_name}")
            return

        dspy.configure(lm=lm)

        # 4. Run baseline evaluation
        self.stdout.write("\nEvaluating baseline...")
        baseline_module = ReadingContextModule()
        baseline_scores = []
        for ex in val[:10]:  # Sample for speed
            pred = baseline_module(passage_reference=ex.passage_reference)
            score = reading_context_quality(ex, pred)
            baseline_scores.append(score)
        baseline_avg = sum(baseline_scores) / len(baseline_scores)
        self.stdout.write(f"Baseline avg score: {baseline_avg:.3f}")

        # 5. Run MIPROv2
        self.stdout.write(f"\nRunning MIPROv2 (auto={options['auto']})...")
        optimizer = dspy.MIPROv2(
            metric=reading_context_quality,
            auto=options["auto"],
            num_threads=options["threads"],
        )
        optimized = optimizer.compile(
            ReadingContextModule(),
            trainset=train,
            valset=val,
        )

        # 6. Evaluate optimized
        self.stdout.write("\nEvaluating optimized prompt...")
        optimized_scores = []
        for ex in val[:10]:  # Same sample
            pred = optimized(passage_reference=ex.passage_reference)
            score = reading_context_quality(ex, pred)
            optimized_scores.append(score)
        optimized_avg = sum(optimized_scores) / len(optimized_scores)
        self.stdout.write(f"Optimized avg score: {optimized_avg:.3f}")
        self.stdout.write(
            f"Improvement: {optimized_avg - baseline_avg:+.3f} "
            f"({(optimized_avg - baseline_avg) / max(baseline_avg, 0.01) * 100:+.1f}%)"
        )

        # 7. Extract and display the optimized instruction
        # MIPROv2 stores optimized instructions in the predictor
        optimized_instruction = None
        for name, predictor in optimized.named_predictors():
            if hasattr(predictor, "signature"):
                optimized_instruction = predictor.signature.instructions
                break

        if optimized_instruction:
            self.stdout.write(f"\n{'='*60}")
            self.stdout.write("OPTIMIZED INSTRUCTION:")
            self.stdout.write(f"{'='*60}")
            self.stdout.write(optimized_instruction)
            self.stdout.write(f"{'='*60}")

            # 8. Save as inactive LLMPrompt
            # Preserve the baseline role because the live OpenAI path concatenates
            # role + prompt when building the system prompt.
            new_prompt = LLMPrompt(
                model=active_prompt.model,
                role=active_prompt.role,
                prompt=optimized_instruction,
                applies_to="readings",
                active=False,  # Keep inactive for the local PoC
            )
            new_prompt.save()
            self.stdout.write(
                f"\nSaved as LLMPrompt #{new_prompt.id} (inactive). "
                "Keep it inactive until a separate production rollout plan exists."
            )

            # 9. Save the full optimized program for later analysis/reproducibility
            optimized.save("data/optimized_reading_module.json")
            self.stdout.write(
                "Full optimized program saved to data/optimized_reading_module.json"
            )
        else:
            self.stderr.write("Could not extract optimized instruction from program.")
```

### 4. Add `dspy` to Dependencies

In `requirements.txt`, add:

```
dspy
```

After updating `requirements.txt`, ensure the app container has the dependency installed before running the command.

## Important Notes

### Cost Estimation

MIPROv2 cost depends on the `--auto` mode and dataset size:

| Mode | Approximate LLM Calls | Estimated Cost (gpt-4o-mini generation + judge) |
|------|----------------------|------------------------------------------------|
| light | ~100-200 | $0.50-2.00 |
| medium | ~300-500 | $2.00-5.00 |
| heavy | ~1000+ | $5.00-15.00 |

These are rough estimates. Actual cost depends on prompt/response lengths and the models used.

### What MIPROv2 Optimizes

MIPROv2 can optimize two things:

1. **Instructions**: The system prompt / task description. It proposes new instruction candidates, evaluates them, and uses Bayesian optimization to converge.
2. **Demonstrations**: Few-shot examples prepended to the prompt. It bootstraps these from the training set when the dataset provides labeled outputs.

For this project, only the optimized instruction text is part of the candidate artifact. The optimized prompt may look significantly different from the original seed prompt. This is expected.

### Extracting the Optimized Prompt

After optimization, the instruction lives in the DSPy program's predictor:

```python
for name, predictor in optimized.named_predictors():
    print(f"Predictor: {name}")
    print(f"Instruction: {predictor.signature.instructions}")
    if hasattr(predictor, "demos"):
        print(f"Demos: {len(predictor.demos)} few-shot examples")
```

The few-shot demos (if any) are stored separately. For this plan, deployment is explicitly **instruction only**:

- Save the optimized instruction to `LLMPrompt.prompt`
- Preserve the baseline `role`
- Keep the new `LLMPrompt` **inactive** during the local PoC
- Treat `data/optimized_reading_module.json` as an offline artifact for inspection and reproducibility, not as the live serving path

If you later decide you need deployed demos, that should be a separate architecture change in `hub/services/llm_service.py` rather than an implicit side effect of this optimization phase.

## Gating Conditions for Success

This phase is complete when ALL of the following are true:

- [ ] **`hub/dspy_modules.py` exists** with `ReadingContextSignature`, `ReadingContextModule`, and `reading_context_quality` metric
- [ ] **`hub/services/dspy_dataset.py` exists** with `build_dataset()` and `split_dataset()` functions
- [ ] **`optimize_reading_prompt` command exists** and runs with `--dry-run` (prints dataset stats without running optimization)
- [ ] **Optimization completes locally**: `docker exec bahk_devcontainer-app-1 python manage.py optimize_reading_prompt --auto light` runs to completion without errors (use `light` for initial validation; reserve `medium` or `heavy` for later iteration)
- [ ] **Improvement observed**: The optimized instruction's average validation score is higher than the baseline's average validation score (printed by the command)
- [ ] **Optimized prompt saved locally**: A new `LLMPrompt` row with `applies_to='readings'` and `active=False` exists in the local database, containing the DSPy-optimized instruction text while preserving the baseline role
- [ ] **Full program saved**: `data/optimized_reading_module.json` exists
- [ ] **Relevant tests pass**: Run focused Django tests via the container, for example `docker exec bahk_devcontainer-app-1 python manage.py test tests.unit.hub --settings=tests.test_settings`

### Proceeding to Phase 4

Phase 4 requires:
1. The new inactive `LLMPrompt` row from this phase (the optimized prompt)
2. The active `LLMPrompt` row (the baseline prompt to compare against)
3. A lightweight local comparison workflow from Phase 1, which may be the existing admin comparison page plus minimal persistence rather than a full new labeling UI
