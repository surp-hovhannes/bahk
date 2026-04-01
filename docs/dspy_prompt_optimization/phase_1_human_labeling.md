# Phase 1: Human Labeling -- Minimal Local Dataset First

Build the smallest useful human-evaluation workflow for generated reading contexts. For the local PoC, the goal is to create a credible labeled dataset without prematurely building a production-grade staff tooling surface.

## Prerequisites

Before starting this phase, verify:

- [ ] The `bahk` Django project is running in the local dev container and reachable at `http://localhost:8000`
- [ ] You can run Django management commands inside the app container, for example: `docker exec bahk_devcontainer-app-1 python manage.py showmigrations`
- [ ] At least one active `LLMPrompt` exists for readings (`LLMPrompt.objects.filter(active=True, applies_to='readings').exists()` returns `True`)
- [ ] There are existing `Reading` objects with active `ReadingContext` instances in the database (these are what raters will evaluate)
- [ ] You understand the existing models: `Reading` (passage reference, text), `ReadingContext` (generated context text, thumbs_up/down, FK to LLMPrompt), and `LLMPrompt` (model, role, prompt, active flag) -- all in `hub/models.py`
- [ ] You understand the existing admin comparison view in `hub/views/admin.py` (`compare_reading_prompts`) and its template at `hub/templates/admin/compare_reading_prompts.html`
- [ ] Both `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are present in the local environment because `bahk/settings.py` requires both variables during settings import, even if the PoC only exercises one provider

## PoC-First Implementation Order

For the first local PoC, implement the phase in this order:

1. Reuse the existing prompt-comparison tooling in `hub/views/admin.py` to generate and inspect candidate outputs locally.
2. Record a small set of human judgments in the lightest durable way that supports Phase 2.
3. Only add new staff pages or API endpoints if the existing comparison page proves too limiting for the local loop.

This phase does **not** need to start with a full rating API plus a full A/B labeling UI. It only needs to produce a local dataset trustworthy enough to calibrate a judge.

## What to Build

### 1. Start with Existing Local Comparison Tooling

Use the existing `compare_reading_prompts` admin view as the first PoC entry point:

- Pick a small set of representative readings from the local database.
- Compare the current active reading prompt against one or more candidate prompts.
- Use this flow to learn what label schema is actually useful before adding more UI.

For the first pass, manual note-taking is acceptable while you validate the evaluation criteria. Once the fields stabilize, persist the labels.

### 2. Add Minimal Persistence for Local Labels

Persist local human ratings in the database so Phase 2 and Phase 3 can reuse them. For the first PoC, `ReadingContextEvaluation` is the minimum required model. `ReadingContextComparison` is useful, but optional until you need durable A/B comparison records.

#### `ReadingContextEvaluation` in `hub/models.py`

Add this model after the existing `ReadingContext` model:

```python
class ReadingContextEvaluation(models.Model):
    """A human evaluator's rating of a generated reading context."""

    RATING_CHOICES = [
        (1, "Poor"),
        (2, "Fair"),
        (3, "Good"),
        (4, "Very Good"),
        (5, "Excellent"),
    ]

    context = models.ForeignKey(
        ReadingContext,
        on_delete=models.CASCADE,
        related_name="evaluations",
    )
    evaluator = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    rating = models.IntegerField(choices=RATING_CHOICES)
    evaluation_round = models.CharField(
        max_length=64,
        blank=True,
        help_text="Optional label set identifier, e.g. 'judge_calibration_v1'",
    )
    explanation = models.TextField(
        blank=True,
        help_text="Optional free-text justification for the rating",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Eval {self.rating}/5 for {self.context.reading.passage_reference}"
```

If you add this model, create the migration and apply it via the local app container:

```bash
docker exec bahk_devcontainer-app-1 python manage.py makemigrations hub
docker exec bahk_devcontainer-app-1 python manage.py migrate
```

#### Optional: `ReadingContextComparison`

Add this model only if the local PoC needs durable A/B records in the database instead of manual notes or ad hoc spreadsheets. It becomes more valuable once you move from exploratory comparisons to repeatable validation rounds.

Captures an A/B preference between two contexts for the same reading.

```python
class ReadingContextComparison(models.Model):
    """A human evaluator's preference between two contexts for the same reading."""

    PREFERENCE_CHOICES = [
        ("a", "Context A"),
        ("b", "Context B"),
        ("tie", "Tie"),
    ]
    COMPARISON_TYPE_CHOICES = [
        ("exploration", "Prompt exploration / variance check"),
        ("validation", "Optimization validation"),
    ]

    reading = models.ForeignKey(
        Reading,
        on_delete=models.CASCADE,
        related_name="comparisons",
    )
    context_a = models.ForeignKey(
        ReadingContext,
        on_delete=models.CASCADE,
        related_name="comparisons_as_a",
    )
    context_b = models.ForeignKey(
        ReadingContext,
        on_delete=models.CASCADE,
        related_name="comparisons_as_b",
    )
    prompt_a = models.ForeignKey(
        LLMPrompt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reading_comparisons_as_prompt_a",
    )
    prompt_b = models.ForeignKey(
        LLMPrompt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reading_comparisons_as_prompt_b",
    )
    preferred = models.CharField(max_length=3, choices=PREFERENCE_CHOICES)
    comparison_type = models.CharField(
        max_length=16,
        choices=COMPARISON_TYPE_CHOICES,
        default="exploration",
    )
    validation_run_id = models.CharField(
        max_length=64,
        blank=True,
        help_text="Identifier for a specific blinded validation round",
    )
    evaluator = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    explanation = models.TextField(
        blank=True,
        help_text="Optional explanation for the preference",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Comparison for {self.reading.passage_reference}: preferred {self.preferred}"
```

Add model validation to ensure:

- `context_a.reading_id == reading_id`
- `context_b.reading_id == reading_id`
- `context_a_id != context_b_id`
- `prompt_a` and `prompt_b` default from `context_a.prompt` / `context_b.prompt` if omitted
- `validation_run_id` is required when `comparison_type == "validation"`

If you add `ReadingContextComparison`, run the same containerized migration commands above.

### 3. Local Collection Workflow

For the first PoC, prefer this workflow:

1. Seed or curate a local set of readings and contexts.
2. Compare outputs using the existing admin page.
3. Record ratings first, because they are enough to calibrate a judge.
4. Add durable A/B comparisons only after you start validating optimized candidates.

Aim for:

- **Exploratory pass**: 10-20 readings to test whether the rubric is sensible
- **Calibration set**: 20-30 durable ratings minimum, ideally with explanations
- **Expanded local set**: 30-50 ratings if the PoC looks promising

### 4. Optional API Endpoints

If manual or admin-only collection becomes too slow, add lightweight staff-only endpoints. These are helpful, but they are **not the first requirement** for a local PoC.

Add two new API views in `hub/views/readings.py` (or a new file `hub/views/evaluations.py`):

#### `ReadingContextEvaluationView`

- **URL**: `POST /api/readings/<pk>/evaluate/`
- **Auth**: Staff only (`IsAdminUser` or `@staff_member_required`)
- **Request body**: `{"rating": 4, "explanation": "Good narrative specificity...", "evaluation_round": "judge_calibration_v1"}`
- **Behavior**:
  1. Look up the `Reading` by `pk`
  2. Get its `active_context` (return 404 if none)
  3. Create a `ReadingContextEvaluation` with the authenticated user as evaluator
  4. Return `{"status": "success", "evaluation_id": <id>}`

#### `ReadingContextComparisonView`

- **URL**: `POST /api/readings/<pk>/compare/`
- **Auth**: Staff only
- **Request body**: `{"context_a_id": 12, "context_b_id": 15, "preferred": "a", "comparison_type": "validation", "validation_run_id": "opt_v1", "explanation": "..."}`
- **Behavior**:
  1. Look up the `Reading` by `pk`
  2. Validate both context IDs belong to this reading
  3. Create a `ReadingContextComparison`, recording `prompt_a`, `prompt_b`, `comparison_type`, and `validation_run_id`
  4. Return `{"status": "success", "comparison_id": <id>}`

Register these in `hub/urls.py` under the existing reading URL patterns. They will be exposed at `/api/...` through `bahk/urls.py`.

### 5. Optional Labeling UI

Build dedicated staff-only HTML pages only if the existing comparison flow is not enough. For the local PoC, keep the UI surface as small as possible and reuse current admin affordances where you can.

#### Optional Rating Mode Page

- **URL**: `GET /hub/evaluate-contexts/`
- **View**: New function in `hub/views/admin.py`
- **Template**: `hub/templates/admin/evaluate_reading_context.html`
- **Behavior**:
  1. Query for readings that have an active context but fewer than 2 evaluations (or a configurable threshold). Order randomly or by date.
  2. Display: passage reference, the full context text, and a form with radio buttons for rating 1-5, a textarea for explanation, and a submit button.
  3. On POST: save the evaluation via the model, then redirect to the next unevaluated reading.
  4. Show progress: "12 of 50 readings evaluated" at the top.
  5. If no more readings need evaluation, show a completion message.

#### Optional A/B Comparison Mode Page

- **URL**: `GET /hub/compare-contexts/`
- **View**: New function in `hub/views/admin.py`
- **Template**: `hub/templates/admin/compare_reading_contexts.html` (new or extend existing)
- **Behavior**:
  1. Select a reading that has an active context.
  2. Generate a second context using a different `LLMPrompt` (or the same prompt to test variance). Store it as an inactive `ReadingContext`.
  3. Randomly assign the two contexts to "Context A" and "Context B" positions (the evaluator should not know which is the current production context).
  4. Display both contexts side-by-side with the passage reference. Include radio buttons: "Prefer A", "Prefer B", "Tie". Include a textarea for explanation.
  5. On POST: save the comparison via the model, mapping the randomized positions back to the actual context IDs and recording whether this comparison is exploratory or part of a formal validation run. Redirect to the next reading.
  6. Show progress counter.

**Important**: The randomization of A/B positions must be stored in the session or as hidden form fields so the POST handler can de-anonymize which context was which.

### 6. Admin Registration

Register whichever new evaluation models you add in `hub/admin.py` so they are visible in the Django admin:

```python
@admin.register(ReadingContextEvaluation)
class ReadingContextEvaluationAdmin(admin.ModelAdmin):
    list_display = ["context", "rating", "evaluation_round", "evaluator", "created_at"]
    list_filter = ["rating", "evaluation_round", "evaluator"]
    readonly_fields = ["created_at"]

@admin.register(ReadingContextComparison)
class ReadingContextComparisonAdmin(admin.ModelAdmin):
    list_display = [
        "reading",
        "comparison_type",
        "validation_run_id",
        "preferred",
        "evaluator",
        "created_at",
    ]
    list_filter = ["comparison_type", "validation_run_id", "preferred", "evaluator"]
    readonly_fields = ["created_at"]
```

## Gating Conditions for Success

This phase is complete for the **local PoC** when ALL of the following are true:

- [ ] **Local comparison workflow works**: A staff user can generate and inspect candidate reading contexts locally using the existing comparison tooling or a minimal extension of it.
- [ ] **Durable ratings exist**: At least 20-30 `ReadingContextEvaluation` records exist in the local database, or an equivalent durable local label source has been consolidated into the database before Phase 2 begins.
- [ ] **Label schema is stable enough**: The team has settled on a 1-5 scale and knows whether free-text explanations are valuable enough to keep collecting.
- [ ] **Minimal persistence works**: If new evaluation models were added, `docker exec bahk_devcontainer-app-1 python manage.py makemigrations --check` reports no pending migrations and the new tables exist locally.
- [ ] **Optional tooling is justified**: Any new API endpoints or staff pages added in this phase solve a real local bottleneck rather than speculative future needs.
- [ ] **Experiment metadata captured**: Ratings are tagged with an `evaluation_round`, and any durable comparisons capture enough metadata to separate exploratory comparisons from later validation rounds.
- [ ] **Relevant tests pass**: Run focused Django tests with the repo-standard command pattern, for example `docker exec bahk_devcontainer-app-1 python manage.py test tests.unit.hub --settings=tests.test_settings`.
