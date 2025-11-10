# Feasts Feature Implementation Summary

## Overview

Successfully implemented a complete feasts feature for the Bahk application that provides:

- **Multilingual support** (English/Armenian) using modeltrans
- **Web scraping** from sacredtradition.am to automatically import feast data
- **AI-powered context generation** using LLM for feast descriptions
- **AI-powered designation classification** to categorize feasts
- **AI-powered icon matching** to automatically match icons to feasts
- **Daily automated feast creation** via Celery scheduled tasks
- **User feedback system** for context quality (thumbs up/down)
- **REST API endpoints** for retrieving feast data

The feature integrates seamlessly with the existing Day, Church, and Fast models, and follows the same patterns established in the Readings feature.

## Implementation Details

### 1. Models

#### Feast Model (`hub/models.py`)

**Fields:**
- `day` (ForeignKey to Day) - The date/church combination for this feast
- `name` (CharField, max_length=256) - Feast name (English)
- `designation` (CharField, nullable) - AI-determined classification
- `icon` (ForeignKey to Icon, nullable) - Matched icon for this feast
- `i18n` (TranslationField) - Multilingual support for `name` field

**Designation Choices:**
- `Sundays, Dominical Feast Days`
- `St. Gregory the Illuminator, St. Hripsime and her companions, the Apostles, the Prophets`
- `Patriarchs, Vartapets`
- `Nativity of Christ, Feasts of the Mother of God, Presentation of the Lord`
- `Martyrs`

**Constraints:**
- Unique constraint: One feast per day (enforced at database level)

**Properties:**
- `active_context` - Returns the currently active FeastContext for this feast

#### FeastContext Model (`hub/models.py`)

**Fields:**
- `feast` (ForeignKey to Feast) - The feast this context describes
- `text` (TextField) - Long-form AI-generated context text
- `short_text` (TextField) - Short 2-sentence summary
- `prompt` (ForeignKey to LLMPrompt, nullable) - The prompt used for generation
- `thumbs_up` (PositiveIntegerField) - User feedback count
- `thumbs_down` (PositiveIntegerField) - User feedback count
- `time_of_generation` (DateTimeField) - When context was generated
- `active` (BooleanField) - Whether this is the active context
- `i18n` (TranslationField) - Multilingual support for `text` and `short_text`

**Behavior:**
- Only one active context per feast (enforced in `save()` method)
- Automatically deactivates other contexts when a new one is marked active

### 2. LLMPrompt Model Updates

**New Field:**
- `applies_to` (CharField) - Indicates whether prompt is for 'readings' or 'feasts'

**Behavior:**
- Only one active prompt per `applies_to` type (enforced in `save()` method)
- Allows separate prompts for readings and feasts

### 3. REST API Endpoints

#### GetFeastForDate (`GET /api/feasts/`)

**Purpose:** Retrieve feast information for a specific date.

**Query Parameters:**
- `date` (optional, str) - Date in format `YYYY-MM-DD` (defaults to today)
- `lang` (optional, str) - Language code ('en' or 'hy', defaults to request language)

**Response Structure:**
```json
{
  "date": "2025-11-10",
  "feast": {
    "id": 1,
    "name": "Feast Name",
    "designation": "Martyrs",
    "icon": {
      "id": 5,
      "title": "Icon Title",
      "thumbnail_url": "https://...",
      ...
    },
    "text": "AI-generated context text for the feast",
    "short_text": "Short 2-sentence summary",
    "context_thumbs_up": 10,
    "context_thumbs_down": 2
  }
}
```

**Behavior:**
- Automatically creates feast if it doesn't exist (via web scraping)
- Uses authenticated user's church if available, otherwise defaults to default church
- Automatically triggers context generation if missing or incomplete
- Skips context generation for feasts with "Fast" in the name
- Returns `{"feast": null}` if no feast exists for the date

**Example Requests:**
```bash
# Get today's feast
GET /api/feasts/

# Get feast for specific date
GET /api/feasts/?date=2025-12-25

# Get feast in Armenian
GET /api/feasts/?date=2025-12-25&lang=hy
```

#### FeastContextFeedbackView (`POST /api/feasts/{pk}/feedback/`)

**Purpose:** Submit user feedback (thumbs up/down) for feast context.

**Request Body:**
```json
{
  "feedback_type": "up"  // or "down"
}
```

**Response:**
```json
{
  "status": "success",
  "regenerate": false  // true if regeneration was triggered
}
```

**Behavior:**
- Increments `thumbs_up` or `thumbs_down` count
- When `thumbs_down` reaches threshold (default: 5), triggers context regeneration
- Threshold configurable via `settings.FEAST_CONTEXT_REGENERATION_THRESHOLD`

**Example Request:**
```bash
POST /api/feasts/1/feedback/
{
  "feedback_type": "down"
}
```

### 4. Management Commands

#### import_feasts (`hub/management/commands/import_feasts.py`)

**Purpose:** Import feasts for a date range by scraping sacredtradition.am.

**Usage:**
```bash
python manage.py import_feasts --church "Armenian Apostolic Church" --start_date 2025-01-01 --end_date 2025-12-31
```

**Arguments:**
- `--church` (required) - Name of church to import feasts for
- `--start_date` (optional) - Start date in YYYY-MM-DD format (defaults to today)
- `--end_date` (optional) - End date in YYYY-MM-DD format (defaults to today + 10 days)

**Behavior:**
- Creates Day objects for each date if they don't exist
- Scrapes feast data from sacredtradition.am for each date
- Creates Feast objects with English and Armenian translations
- Updates existing feasts with missing translations
- Skips dates with no feast data

#### regenerate_feast_contexts (`hub/management/commands/regenerate_feast_contexts.py`)

**Purpose:** Regenerate AI-generated contexts for feasts.

**Usage:**
```bash
# Regenerate all feasts
python manage.py regenerate_feast_contexts --all

# Regenerate specific feast
python manage.py regenerate_feast_contexts --feast-id 123
```

**Arguments:**
- `--all` - Regenerate contexts for all feasts
- `--feast-id` - Regenerate context for a specific feast ID

**Behavior:**
- Enqueues Celery tasks to regenerate contexts
- Uses `force_regeneration=True` to overwrite existing contexts

### 5. Background Tasks (Celery)

#### create_feast_date_task (`hub/tasks/feast_tasks.py`)

**Purpose:** Daily automated feast creation for today's date.

**Schedule:** Runs daily at 5 minutes past midnight (00:05)

**Behavior:**
- Gets today's date
- Gets default church
- Checks if Day exists, if Fast is associated (skips if so)
- Checks if Feast already exists
- If not, scrapes and creates feast
- Monitored by Sentry with slug `daily-feast-date-creation`

**Configuration:**
Defined in `bahk/celery.py`:
```python
'create-feast-date-daily': {
    'task': 'hub.tasks.create_feast_date_task',
    'schedule': crontab(hour=0, minute=5),
    'options': {
        'sentry': {
            'monitor_slug': 'daily-feast-date-creation',
        }
    }
}
```

#### generate_feast_context_task (`hub/tasks/llm_tasks.py`)

**Purpose:** Generate AI context for a feast in all available languages.

**Parameters:**
- `feast_id` (int) - ID of feast to generate context for
- `force_regeneration` (bool, default=False) - Force regeneration even if context exists
- `language_code` (deprecated) - Ignored, all languages are always generated

**Behavior:**
- Uses active LLM prompt for feasts (`applies_to='feasts'`)
- Generates both `text` and `short_text` for each language
- Creates new FeastContext or updates existing one
- Skips if context already exists and all translations are present (unless `force_regeneration=True`)
- Retries up to 3 times on failure

**Triggered By:**
- View endpoint when context is missing or incomplete
- Feedback endpoint when thumbs_down threshold is reached
- Management command for bulk regeneration
- Manual task invocation

#### determine_feast_designation_task (`hub/tasks/llm_tasks.py`)

**Purpose:** Automatically determine and set feast designation using AI.

**Parameters:**
- `feast_id` (int) - ID of feast to determine designation for

**Behavior:**
- Uses LLM service to analyze feast name and determine designation
- Validates designation against available choices
- Skips if designation is already set (doesn't overwrite manual assignments)
- Uses active feast prompt's model, or defaults to Claude Sonnet 4.5

**Triggered By:**
- `post_save` signal when feast is created (if designation not set)

#### match_icon_to_feast_task (`hub/tasks/icon_tasks.py`)

**Purpose:** Automatically match an icon to a feast using AI-powered matching.

**Parameters:**
- `feast_id` (int) - ID of feast to match icon for

**Behavior:**
- Uses feast name as prompt for icon matching
- Filters icons by feast's church
- Uses LLM-based matching with confidence scoring
- Only assigns icon if confidence meets threshold (`ICON_MATCH_CONFIDENCE_THRESHOLD`)
- Skips if icon is already assigned

**Confidence Threshold:**
- Configurable via `hub.constants.ICON_MATCH_CONFIDENCE_THRESHOLD` (default: 'high')
- Valid values: 'high', 'medium', 'low'
- Only icons with confidence >= threshold are assigned

**Triggered By:**
- `post_save` signal when feast is created

### 6. Web Scraping (`hub/utils.py`)

#### scrape_feast()

**Purpose:** Scrape feast data from sacredtradition.am.

**Parameters:**
- `date_obj` (date) - Date to scrape feast for
- `church` (Church) - Church object (must be in SUPPORTED_CHURCHES)
- `date_format` (str, default="%Y%m%d") - Format for date in URL

**Returns:**
```python
{
    "name": "Default name (English or Armenian)",
    "name_en": "English name or None",
    "name_hy": "Armenian name or None"
}
```
or `None` if no feast found

**Behavior:**
- Scrapes both English (language code 2) and Armenian (language code 3) versions
- Extracts feast name from HTML elements with `class="dname"`
- Strips HTML tags from extracted content
- Returns None if no feast found in either language
- Uses English as default name, falls back to Armenian if English unavailable

**Supported Churches:**
- Currently only supports churches in `SUPPORTED_CHURCHES` list
- Returns None for unsupported churches

#### get_or_create_feast_for_date()

**Purpose:** Shared utility to get or create a feast for a date and church.

**Parameters:**
- `date_obj` (date) - Date to get/create feast for
- `church` (Church) - Church object
- `check_fast` (bool, default=True) - Skip feast lookup if Fast is associated

**Returns:**
Tuple of `(feast_obj, created, status_dict)`:
- `feast_obj` - Feast instance or None
- `created` - Boolean indicating if feast was created
- `status_dict` - Dict with status information

**Behavior:**
- Gets or creates Day for date and church
- Optionally checks if Fast is associated (skips if `check_fast=True`)
- Checks if Feast already exists
- Scrapes feast data if needed
- Creates or updates Feast with translations
- Returns status information for logging/monitoring

### 7. Signals (`hub/signals.py`)

#### handle_feast_save()

**Trigger:** `post_save` signal on Feast model

**Behavior:**
- On creation: Triggers `determine_feast_designation_task` if designation not set
- On creation: Triggers `match_icon_to_feast_task`
- Only triggers on creation (not updates) to avoid duplicate task enqueuing

### 8. LLM Service Integration (`hub/services/llm_service.py`)

#### generate_feast_context()

**Purpose:** Generate context text for a feast using LLM.

**Parameters:**
- `feast` (Feast) - Feast instance
- `llm_prompt` (LLMPrompt) - Prompt to use for generation
- `language_code` (str) - Language to generate for ('en' or 'hy')

**Returns:**
```python
{
    "text": "Long-form context text",
    "short_text": "Short 2-sentence summary"
}
```

**Behavior:**
- Formats feast information for LLM
- Generates both long and short text in single call
- Supports multiple LLM providers (OpenAI, Anthropic)
- Handles errors and retries

#### determine_feast_designation()

**Purpose:** Determine feast designation using LLM.

**Parameters:**
- `feast` (Feast) - Feast instance
- `model_name` (str, optional) - Specific model to use

**Returns:**
- Designation string (one of the valid choices) or None

**Behavior:**
- Analyzes feast name to determine classification
- Returns one of the predefined designation choices
- Validates response against available choices

### 9. Constants (`hub/constants.py`)

**ICON_MATCH_CONFIDENCE_THRESHOLD:**
- Default: `'high'`
- Minimum confidence level required for icon matching
- Valid values: `'high'`, `'medium'`, `'low'`

### 10. Admin Interface (`hub/admin.py`)

#### FeastAdmin

**Features:**
- List display: name, day (with date), church, designation, icon
- Filters: year, church, designation, icon
- Search: name
- Readonly fields: designation (can be manually set)
- Inline editing for FeastContext

#### FeastContextAdmin

**Features:**
- List display: feast, active, thumbs_up, thumbs_down, time_of_generation
- Filters: active, feast, time_of_generation
- Readonly fields: time_of_generation

## Key Features

1. **Automated Feast Import:** Daily task automatically creates feasts for today
2. **Web Scraping:** Automatically scrapes feast data from sacredtradition.am
3. **Multilingual Support:** English and Armenian translations for names and contexts
4. **AI Context Generation:** LLM generates contextual descriptions for feasts
5. **AI Designation Classification:** Automatically categorizes feasts
6. **AI Icon Matching:** Automatically matches icons to feasts with confidence scoring
7. **User Feedback:** Thumbs up/down system with automatic regeneration threshold
8. **REST API:** Complete API for retrieving feast data
9. **Management Commands:** Tools for bulk import and regeneration
10. **Signal-Based Automation:** Automatic task triggering on feast creation

## Testing

Comprehensive test coverage includes:

### Test Files:
- `hub/tests/test_feast_model.py` - Model tests (13 tests)
- `hub/tests/test_import_feasts.py` - Management command tests (8 tests)
- `hub/tests/test_scrape_feast.py` - Utility function tests (11 tests)
- `hub/tests/test_feast_designation.py` - Designation determination tests
- `hub/tests/test_feast_icon_matching.py` - Icon matching tests

**Total:** 32+ test cases covering all major functionality

**Run Tests:**
```bash
python manage.py test hub.tests.test_feast_model hub.tests.test_import_feasts hub.tests.test_scrape_feast hub.tests.test_feast_designation hub.tests.test_feast_icon_matching --exclude-tag=performance --settings=tests.test_settings
```

## Usage Examples

### Create a Feast (via Management Command)
```bash
python manage.py import_feasts --church "Armenian Apostolic Church" --start_date 2025-12-25 --end_date 2025-12-25
```

### Get Feast via API
```bash
# Get today's feast
curl http://localhost:8000/api/feasts/

# Get specific date
curl http://localhost:8000/api/feasts/?date=2025-12-25

# Get in Armenian
curl http://localhost:8000/api/feasts/?date=2025-12-25&lang=hy
```

### Submit Feedback
```bash
curl -X POST http://localhost:8000/api/feasts/1/feedback/ \
  -H "Content-Type: application/json" \
  -d '{"feedback_type": "up"}'
```

### Regenerate Contexts
```bash
# Regenerate all
python manage.py regenerate_feast_contexts --all

# Regenerate specific feast
python manage.py regenerate_feast_contexts --feast-id 123
```

### Create Feast Programmatically
```python
from hub.models import Church, Day, Feast
from datetime import date

church = Church.objects.get(name="Armenian Apostolic Church")
day, _ = Day.objects.get_or_create(date=date(2025, 12, 25), church=church)
feast = Feast.objects.create(
    day=day,
    name="Nativity of Christ"
)
feast.name_hy = "Քրիստոսի Ծնունդ"  # Armenian translation
feast.save()
```

## Files Created/Modified

### New Files:
- `hub/models.py` - Added Feast and FeastContext models
- `hub/views/feasts.py` - API views for feasts
- `hub/tasks/feast_tasks.py` - Daily feast creation task
- `hub/tasks/llm_tasks.py` - Added feast context and designation tasks
- `hub/tasks/icon_tasks.py` - Added icon matching task
- `hub/management/commands/import_feasts.py` - Feast import command
- `hub/management/commands/regenerate_feast_contexts.py` - Context regeneration command
- `hub/migrations/0040_add_feast_models_and_llmprompt_applies_to.py` - Initial migration
- `hub/migrations/0041_add_designation_to_feast.py` - Designation field migration
- `hub/migrations/0042_feast_icon_alter_llmprompt_model.py` - Icon field migration
- `hub/tests/test_feast_model.py` - Model tests
- `hub/tests/test_import_feasts.py` - Command tests
- `hub/tests/test_scrape_feast.py` - Utility tests
- `hub/tests/test_feast_designation.py` - Designation tests
- `hub/tests/test_feast_icon_matching.py` - Icon matching tests
- `hub/tests/README_FEAST_TESTS.md` - Test documentation

### Modified Files:
- `hub/models.py` - Added Feast, FeastContext models, updated LLMPrompt
- `hub/admin.py` - Added FeastAdmin and FeastContextAdmin
- `hub/urls.py` - Added feast endpoints
- `hub/signals.py` - Added feast post_save signal handler
- `hub/utils.py` - Added scrape_feast() and get_or_create_feast_for_date()
- `hub/services/llm_service.py` - Added generate_feast_context() and determine_feast_designation()
- `hub/constants.py` - Added ICON_MATCH_CONFIDENCE_THRESHOLD
- `bahk/celery.py` - Added daily feast creation task schedule
- `hub/tasks/__init__.py` - Exported new tasks
- `icons/views.py` - Updated icon matching to return confidence levels

## Dependencies

All required dependencies were already present in the project:
- `django-modeltrans` - For multilingual field support
- `celery` - For background tasks
- `sentry-sdk` - For task monitoring
- `openai` / `anthropic` - For LLM services
- `urllib` - For web scraping (standard library)
- `djangorestframework` - For REST API

## Configuration

### Settings

**FEAST_CONTEXT_REGENERATION_THRESHOLD:**
- Default: 5
- Number of thumbs_down votes required to trigger context regeneration
- Can be overridden in Django settings

**ICON_MATCH_CONFIDENCE_THRESHOLD:**
- Default: 'high'
- Minimum confidence level for icon matching
- Defined in `hub/constants.py`

### Celery Schedule

Daily feast creation task runs at 00:05 (5 minutes past midnight):
```python
'create-feast-date-daily': {
    'task': 'hub.tasks.create_feast_date_task',
    'schedule': crontab(hour=0, minute=5),
}
```

## Integration Points

### With Existing Features:

1. **Day Model:** Feasts are linked to Day objects (date + church)
2. **Church Model:** Feasts are church-specific
3. **Fast Model:** Feasts are skipped if a Fast is associated with the Day
4. **Icon Model:** Feasts can have matched icons
5. **LLMPrompt Model:** Separate prompts for readings vs feasts
6. **ReadingContext Model:** Similar structure to FeastContext for consistency

## Future Enhancements (Optional)

Potential improvements that could be added:
- Support for multiple feasts per day (if needed)
- Feast categories or tags
- Historical feast data import
- Feast calendar export
- Feast notifications/reminders
- Feast-related readings integration
- Analytics on popular feasts
- Admin bulk operations for feasts

## Conclusion

The feasts feature is fully implemented, tested, and ready for use. All requirements have been met:
- ✅ Multilingual support (English/Armenian)
- ✅ Web scraping from sacredtradition.am
- ✅ AI-powered context generation
- ✅ AI-powered designation classification
- ✅ AI-powered icon matching
- ✅ Daily automated feast creation
- ✅ User feedback system
- ✅ Complete REST API
- ✅ Management commands
- ✅ Comprehensive test coverage
- ✅ Admin interface with full CRUD operations

The implementation follows Django best practices and integrates seamlessly with existing features, particularly the Readings feature which shares similar patterns and structure.

