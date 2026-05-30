# Prayer Set JSON Import

Admin import flow for bulk-importing prayer sets and prayers from a JSON file, with optional AI-powered icon matching.

## Quick Start

A valid import file needs:

```json
{
  "prayer_sets": [
    {
      "title": "Morning Prayers",
      "category": "morning",
      "description": "Optional set description",
      "prayers": [
        {
          "title": "Morning Thanksgiving",
          "text": "We thank You, O Lord...",
          "category": "morning",
          "tags": "thanksgiving,morning"
        }
      ]
    }
  ]
}
```

Upload through the Django admin at **Prayers → Import Prayer Sets**. The import is a two-step process: preview conflicts → confirm import.

## JSON Structure

| Top-level key | Type | Required | Description |
|---|---|---|---|
| `prayer_sets` | `array` | ✅ | Non-empty array of prayer set objects |

### Prayer Set Object

| Key | Type | Required | Max Length | Description |
|---|---|---|---|---|
| `title` | `string` | ✅ | 128 | Display title (case-insensitive unique per church) |
| `category` | `string` | ✅ | — | One of: `morning`, `evening`, `general` |
| `description` | `string` | ❌ | — | Optional description text |
| `prayers` | `array` | ✅ | — | Non-empty array of prayer objects |

### Prayer Object

| Key | Type | Required | Max Length | Description |
|---|---|---|---|---|
| `title` | `string` | ✅ | 200 | Display title (case-insensitive unique across all prayers in the file) |
| `text` | `string` | ✅ | — | Prayer body text |
| `category` | `string` | ✅ | — | One of: `morning`, `evening`, `general` |
| `tags` | `string` or `array` | ❌ | — | Comma-separated tag string or array of tag strings |

## Validation Rules

All validation runs at **upload time** (before preview) and re-runs at **confirm time** (before import).

### Enforced at upload

- `prayer_sets` must be a non-empty array
- Every prayer set and prayer must be an object (not a scalar or array)
- Required string fields (`title`, `text`, `category`) must be non-empty strings
- `description` must be a string if present (empty/null is allowed)
- `title` must not exceed max length (128 for sets, 200 for prayers)
- `category` must be one of `morning`, `evening`, `general`
- `tags` must be a string, array of strings, empty string, or empty array — numbers/objects will be rejected
- Duplicate titles (case-insensitive) within the same file are rejected

### Enforced at confirm

- **Conflict detection**: case-insensitive title match against existing prayer sets and prayers in the same church
- **Re-validation**: the full `validate_import_json` runs again before `execute_import` to guard against stale or manipulated session data
- **Concurrent import guard**: the church row is locked (`SELECT FOR UPDATE`) and conflicts are rechecked inside the atomic transaction
- **Idempotency**: each confirm gets a unique `import_id`; duplicate submits (double-click, parallel tabs) are blocked via a cache lock

## Categories

| Category | Typical use |
|---|---|
| `morning` | Morning prayers, daily devotionals |
| `evening` | Evening prayers, compline |
| `general` | Prayers not tied to a specific time of day |

## Translations (i18n)

Translation fields follow Django modeltrans's virtual-field convention. Supported languages are determined by `settings.LANGUAGES`.

### Inline Translation Keys

Add language-code suffixes directly to translatable fields:

```json
{
  "title": "Morning Prayers",
  "title_hy": "Առաւօտեան Աղօթք",
  "description_en": "A collection of morning prayers",
  "description_hy": "Առաւօտեան աղօթքների հաւաքածու",
  "prayers": [
    {
      "title": "Morning Thanksgiving",
      "title_hy": "Առաւօտեան Գոհութիւն",
      "text": "We thank You, O Lord...",
      "text_hy": "Գոհանում ենք Քեզնից, Տէր...",
      "category": "morning"
    }
  ]
}
```

### Translations Object

Alternatively, use a `translations` (or `i18n`) block:

```json
{
  "title": "Morning Prayers",
  "translations": {
    "hy": {
      "title": "Առաւօտեան Աղօթք",
      "description": "Առաւօտեան աղօթքների հաւաքածու"
    }
  }
}
```

Translatable fields for **prayer sets**: `title`, `description`
Translatable fields for **prayers**: `title`, `text`

## Tag Discovery

When generating import JSON (manually or via LLM), prefer tags that already exist in the system for consistency. The public `/api/tags/` endpoint lists all unique tags currently in use.

```bash
# Get all prayer tags as a flat sorted array
curl https://api.fastandpray.app/api/tags/?model=prayer
# → ["daily", "evening", "fasting", "morning", "thanksgiving"]

# Get tags for all supported models
curl https://api.fastandpray.app/api/tags/
# → {"prayer": [...], "icon": [...], "patristic_quote": [...]}
```

**LLM workflow tip:** When an LLM generates a prayer set import, it should first call `/api/tags/?model=prayer` to discover existing tags, then use those (plus any new ones needed) in the `"tags"` field of each prayer. This keeps the tag vocabulary consistent and makes prayers discoverable through existing filters.

## AI Icon Matching

The import form includes a checkbox: **"Use AI to match icons to imported prayers"**. When checked:

1. After import completes, a background Celery task runs `match_icons_for_imported_prayers_task`
2. Each new prayer's title + tags are sent to the LLM-based icon matcher (`_match_icons_with_llm`)
3. The best match is assigned **only if** the confidence score meets `ICON_MATCH_CONFIDENCE_THRESHOLD` (low/medium/high)
4. If no icon meets the threshold, the prayer is left without an icon

The AI option uses the same LLM-based matcher as feast icon matching — not a simple keyword overlap.

## Conflict Resolution

When title conflicts are detected at preview:

1. A conflict page shows which existing prayer sets and prayers share titles with the import
2. The admin can either:
   - **Cancel** and edit the JSON to rename conflicting items
   - **Remove** the conflicting entries from the existing database first, then re-upload

Conflicts are checked again at confirm time under a database lock to prevent races.

## File Upload Limits

- **Max file size**: 5 MB
- **Allowed extensions**: `.json`

## Example: Complete Import File

```json
{
  "prayer_sets": [
    {
      "title": "Morning Prayers",
      "title_hy": "Առաւօտեան Աղօթք",
      "category": "morning",
      "description": "Daily morning prayer collection",
      "prayers": [
        {
          "title": "Morning Thanksgiving",
          "title_hy": "Առաւօտեան Գոհութիւն",
          "text": "We thank You, O Lord our God...",
          "text_hy": "Գոհանում ենք Քեզնից, Տէր Աստուած մեր...",
          "category": "morning",
          "tags": "thanksgiving,morning,start"
        },
        {
          "title": "Prayer for Guidance",
          "title_hy": "Առաջնորդութեան Աղօթք",
          "text": "Guide us, O Lord, in all our ways...",
          "text_hy": "Առաջնորդեա մեզ, Տէր, ամենայն ճանապարհս մեր...",
          "category": "morning",
          "tags": ["guidance", "wisdom"]
        }
      ]
    },
    {
      "title": "Evening Prayers",
      "category": "evening",
      "prayers": [
        {
          "title": "Evening Thanksgiving",
          "text": "We give thanks for this day...",
          "category": "evening"
        }
      ]
    }
  ]
}
```

## Error Messages

Validation errors are surfaced as Django messages in the admin. Common errors:

| Error | Cause |
|---|---|
| "Import JSON must be an object" | Top-level JSON is not an object |
| "Import JSON must include a non-empty prayer_sets array" | Missing or empty `prayer_sets` |
| "Prayer set N must be an object" | An element in `prayer_sets` is not an object |
| "Prayer set 'X' is missing required field 'Y'" | Missing title/category/prayers |
| "Prayer set 'X' field 'title' must be a string" | Title is not a string |
| "Prayer set 'X' field 'title' exceeds maximum length of 128 characters" | Title too long |
| "Prayer set 'X' has invalid category 'Y'" | Category not in morning/evening/general |
| "Duplicate prayer set title 'X'" | Two sets share a title (case-insensitive) |
| "Import blocked because title conflicts were detected" | Existing records match imported titles |
| "This import has already been submitted" | Duplicate confirm POST |
