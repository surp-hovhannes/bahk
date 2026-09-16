# Staff FeastContext API

These endpoints are staff-only (`IsAdminUser`) and are mounted below `/hub/`. Public feast reads and `/hub/feasts/<id>/feedback/` are unchanged.

Set CLI variables:

```bash
BASE_URL=https://example.test
FEAST_ID=123
TOKEN='staff-jwt-access-token'
AUTH="Authorization: Bearer $TOKEN"
```

## Read the active version

```bash
curl -sS -H "$AUTH" \
  "$BASE_URL/hub/feasts/$FEAST_ID/context/"
```

The response includes version, operation, actor, prompt metadata, editorial instructions, restore lineage, feedback counters, and every configured language body.

## Read bounded history

History is newest-first, defaults to 20 rows per page, and caps `page_size` at 100. Bodies are omitted unless explicitly requested.

```bash
curl -sS -H "$AUTH" \
  "$BASE_URL/hub/feasts/$FEAST_ID/context/history/?page=1&page_size=20"

curl -sS -H "$AUTH" \
  "$BASE_URL/hub/feasts/$FEAST_ID/context/history/?include_text=true&page_size=20"
```

## Manually edit one language

Both body fields and an explicit configured language are required. A partial language edit copies every active translation into a new version before replacing the selected language.

```bash
curl -sS -X PATCH -H "$AUTH" -H 'Content-Type: application/json' \
  "$BASE_URL/hub/feasts/$FEAST_ID/context/" \
  --data '{
    "language": "en",
    "text": "Corrected long context.",
    "short_text": "Corrected short context."
  }'
```

## Regenerate

Instructions are optional, but if supplied they must be nonblank.

```bash
response=$(curl -sS -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$BASE_URL/hub/feasts/$FEAST_ID/context/regenerate/" \
  --data '{"additional_instructions":"Emphasize the liturgical meaning."}')

task_id=$(printf '%s' "$response" | jq -r .task_id)
printf '%s\n' "$response"
```

A new request returns `202` with `task_id`, `status`, and `feast_id`. While that feast has an in-flight regeneration, a duplicate returns `409` and the original task id.

## Safely verify a task

```bash
curl -sS -H "$AUTH" \
  "$BASE_URL/hub/feasts/$FEAST_ID/context/task/$task_id/"
```

The status endpoint returns only `task_id`, `feast_id`, `state`, `ready`, and a safe `error` value. It never echoes generated bodies, prompts, or editorial instructions. Status is maintained in the Django cache and therefore remains available even when the Celery result backend is disabled; clients should not query Celery results directly.

Terminal states set `ready=true`. Task status metadata expires after 24 hours. The per-feast in-flight lock is tied to the task time limit and retry allowance and is cleared by terminal task paths.

## Restore

Restore copies the selected historical version into a new active version. It never mutates or reactivates the old row.

```bash
curl -sS -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$BASE_URL/hub/feasts/$FEAST_ID/context/restore/" \
  --data '{"version":3}'
```

## Version and feedback guarantees

- Manual edits, generation, regeneration, and restore append a new monotonically allocated version under a per-feast database lock.
- The prior active row becomes inactive but is otherwise unchanged.
- New versions start with zero thumbs-up and zero thumbs-down.
- Operator actions do **not** synthesize feedback or require a synthetic thumbs-down.
- Existing public feedback continues to increment only the active version.
- Feast API cache invalidation is registered with `transaction.on_commit`, so rolled-back writes do not invalidate successful cached reads.
