# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bahk (also known as Fast & Pray) is a Django-based web application for Christians to track fasting participation, connect with their church community, and access devotional resources. The app supports multiple churches, fasts, and users with social features like activity feeds and milestone tracking.

## Architecture

### Core Django Apps

- **hub**: Main application containing core models (Church, Fast, Profile, Participation, Feast) and primary API endpoints
- **events**: User activity tracking, analytics, event logging, and activity feeds
- **notifications**: Email and push notifications using Mailgun and Expo
- **learning_resources**: Devotional content, videos, readings, and bookmarks
- **prayers**: Prayer resources and content
- **icons**: Icon management system
- **app_management**: Application-level utilities and management

### Tech Stack

- **Backend**: Django 4.2.11 with Django REST Framework
- **Database**: PostgreSQL (production), SQLite (development)
- **Cache/Queue**: Redis with django-redis
- **Background Tasks**: Celery with Celery Beat for scheduled tasks
- **Storage**: AWS S3 (production), local filesystem (development)
- **Monitoring**: Sentry for error tracking and performance monitoring
- **Translations**: django-modeltrans for multilingual support (English, Armenian)
- **Frontend Assets**: Sass for CSS, Tailwind CSS for styling

### Key Features

1. **Multilingual Support**: Uses `django-modeltrans` with JSON-based translations
   - Default language: English (`en`), Additional: Armenian (`hy`)
   - Access translated fields via `_i18n` properties (e.g., `fast.name_i18n`)
   - Language selection via `?lang=hy` query param or `Accept-Language` header

2. **Activity Tracking**: Custom events system tracks user actions (login, fast join/leave, milestones)

3. **Caching**: Heavy use of Redis caching for performance optimization
   - Activity feed caching, analytics caching, thumbnail URL caching
   - Use `select_related` and `prefetch_related` for query optimization

4. **Background Tasks**: Celery tasks for daily notifications, map generation, activity feed cleanup

5. **Push Notifications**: Expo push notifications for mobile app integration

6. **Scripture Text**: English from the [API.Bible](https://scripture.api.bible/) REST API (NKJV; KJVAIC for Apocrypha); Armenian composed offline from the `BibleVerse` corpus. Client in `hub/services/bible_api_service.py`, language registry in `hub/services/reading_text_service.py`, tasks in `hub/tasks/bible_api_tasks.py`.

   **Text is stored per passage, not per reading.** `Reading.passage_key` (`"{USFM}.{ch}.{v}-{ch}.{v}"`, derived in `save()` via `hub.constants.passage_key`) identifies the passage; `PassageText` holds one row per `(passage_key, language)`. The lectionary emits ~1,500 readings a year forever but resolves to only ~1,124 distinct passages across all years, so retrieval cost is a constant instead of growing with the table. Never key text by date — that is the bug this design exists to prevent.

   Book *names* are normalised onto USFM, so spelling variants share a key. Per-edition versification is deliberately **not**: KJVAIC splits Greek Esther into `ESG 1-7` while the Armenian corpus keeps it inline as `EST 10-16`, so each fetcher applies its own and the shared key stays the citation as written.

   Adding a language is two steps — write a `fetch_<lang>` returning `{text, version, copyright, fums_token}` or `None`, and register it in `TEXT_FETCHERS`. Optionally add a resource preparer to `RESOURCE_PREPARERS`, and an entry in `LANGUAGE_TEXT_MAX_AGE_DAYS` if its licence caps how old served text may be (absent = never expires). No new columns, no modeltrans registration. `store_passage_text` is the single writer for every language.

   Spend against the plan quota (5,000/month) is bounded in four places:
   - The weekly Celery Beat task re-retrieves at most `READING_REFRESH_LIMIT` **distinct passages** (not readings) that are stale beyond `READING_TEXT_REFRESH_DAYS`. It logs a dedup ratio each run — if that drifts toward 1.0, passage keying has regressed and spend is scaling with the table again.
   - `GetDailyReadingsForDate` retrieves missing or expired `(passage, language)` pairs on demand, capped per day by `READING_FETCH_DAILY_BUDGET`. A date never requested before costs nothing if its passages are already stored.
   - `BIBLE_API_MONTHLY_BUDGET` is a hard ceiling over **both** paths (`hub/services/api_budget.py`). Post-change it should never be reached; alert on it rather than raising it.
   - The refresh task aborts after `READING_REFRESH_MAX_CONSECUTIVE_FAILURES` consecutive failures on metered languages, so a rejecting API costs ~10 calls instead of a full run.

   `get_reading_text_fields` blanks text/version/copyright/FUMS token once it exceeds that language's cap, so nothing past API.Bible's freshness window is ever served. Armenian is locally composed and never expires.

   Commands: `warm_passage_texts` (enumerate the whole corpus once, then no date is ever cold; run `--dry-run` first), `backfill_reading_passage_keys` (`--all` after any change to the key derivation), `fetch_reading_texts`, `fetch_armenian_reading_texts`.

   Env vars: `BIBLE_API_KEY`, `READING_TEXT_REFRESH_DAYS`, `READING_TEXT_MAX_AGE_DAYS`, `READING_REFRESH_LIMIT`, `READING_REFRESH_MAX_CONSECUTIVE_FAILURES`, `READING_FETCH_DAILY_BUDGET`, `BIBLE_API_MONTHLY_BUDGET`.

7. **Feast Names**: The day's commemorations come from the offline `armenian_lectionary`
   engine (`hub/services/feast_service.py`), recomputed per request. Nothing is imported ahead of
   time and there is no daily task.

   **A day is a LIST of observances, and only its commemorations become feasts.** The engine
   serves an `Observances` array — one entry per component, each `{id, name, is_fast, is_comm}` —
   so bahk never splits the joined `Liturgical Day` string. `is_comm` is a human-reviewed mark
   saying the component commemorates a person or an event rather than merely locating the day in
   the calendar; `get_feast_for_date` filters on it and returns **one dict per commemoration**.

   Do not filter on `is_fast`: the two marks are independent, and the six ids carrying both
   include Great Friday. Do not classify by name shape either — `Sixth Sunday of Great Lent:
   Sunday of the Advent` and `Sixth day of Nativity` read identically and answer oppositely.

   **Most days commemorate nobody.** Of the engine's 9,861 supported days, 5,070 carry no
   commemoration (the weekly Wed/Fri fasts alone are 1,334), 4,606 carry one, and 185 carry two.
   So `feasts: []` is the commonest answer and means "nothing to show today" — the API serves it,
   caches it, and the app renders no card. It is not an error.

   **Feasts are keyed by observance, not by date and not by name.** `Feast` is unique on
   `(church, observance_id)` — one published engine id, so a row is one commemoration. It does
   not hang off `Day`. It exists to hold the parts the engine has no notion of — the AI
   `designation`, the matched `icon`, and the generated `FeastContext`s — and those are
   properties of the commemoration, not of the day it lands on. One row serves every recurrence,
   so the LLM context and icon match behind it run **once**, not once a year. Same rule as
   `PassageText`: never key by date.

   **The name is display text, derived from the id and refreshed from the engine.** It used to
   be the key, and that was the bug: display text gets corrected (1.3.0 alone folded `Saint(s)`
   to `St(s).` and fixed 122 spellings; 2.0.0 renamed the Sunday families and retired the bare
   `Fast day` marker), and every correction silently orphaned a row. An id, once published, keeps
   meaning the same observance — so a correction is now an `UPDATE`.

   The two are not interchangeable in the other direction either: the engine distinguishes
   observances English conflates. `"Fast day"` was three distinct ids in range, because the source
   heads the Fast of St. Gregory the Illuminator's days with their ordinal in Armenian and
   flattens them all to `Fast day` in English. That is why the unique constraint had to move off
   the name.

   **Nothing on the feast path classifies by name.** `is_feast_context_generation_eligible` is
   just `designation != FAST`; the regexes it used to run — saint/martyr words, "fast"+"day"
   tokens, a hardcoded Mijink list — guessed at a question `is_comm` answers upstream, and they
   contradicted `determine_feast_designation_task` on Mijink. With those gone, `designation` is
   the sole gate on context generation, which is what made the damage below permanent.

   That damage is the corollary of the rule above, and it has already
   cost us. `determine_feast_designation_task` used to stamp `FAST` on "<Ordinal> day of Great
   Lent"-shaped names without asking the classifier, carving out `Saint` but never `St.` — the
   abbreviation the engine actually uses. Production ended up with St. Theodore the Tyron, Lazarus
   Saturday and St. Gregory's Descent into the Pit all designated generic fasts and holding blank
   cards. `designation` is never overwritten once set and it is the only thing standing between a
   feast and its generated context, so nothing recovers on its own.

   The two halves ship together on purpose. Removing the regex stops new stamps; migration `0068`
   clears the ones already written, on any row whose observance the engine marks `is_comm` and not
   `is_fast` — `is_commemoration_and_not_a_fast` is the guard it asks. Deploying either alone is
   useless: the repair without the removal is overwritten within a day, and the removal without
   the repair leaves the damaged rows blank forever. Six ids carry both marks (the named Lenten
   Sundays, and Mijink) and are left alone, because there `FAST` may be considered rather than an
   artifact.

   **Context eligibility does not read the name.** `is_feast_context_generation_eligible` is now
   just `designation != FAST`. The regexes it used to run (saint/martyr words, "fast"+"day"
   tokens, a hardcoded Mijink list) guessed at a question `is_comm` answers upstream, and they
   contradicted `determine_feast_designation_task` on Mijink. That leaves `designation` as the
   only gate, which is why `0068` had to repair the rows a regex wrote into it.

   **Eligibility is a scheduling decision, not a payload field.** `context_eligible` used to be
   served so the app could hide a feast whose context would never arrive. The app stopped reading
   it in the same commit that adopted `feasts: []`, and once the rule collapsed to
   `designation != FAST` the field only restated `designation`, which is served beside it. Old
   builds reading the deprecated `feast` key are safe: their check was `context_eligible !==
   false`, so an absent field reads as eligible. The helper still gates the enqueue in the view
   and the worker.

   Because a feast has no date, two things go through the engine instead:
   - `dates_for_feast_name` / `representative_date_for_feast_name` (cached range sweep) supply a
     date where one is genuinely needed — the `feasts.json` reference matcher in `llm_service`.
     These are keyed on a single **observance** name, not the joined day name; keying them on the
     joined string would silently return no date for every multi-component day.
   - Feast API cache invalidation bumps a **per-church generation** folded into the cache key
     (`hub/cache.py`) rather than deleting per-date keys, which would mean enumerating thousands
     of days. O(1), and correct on every cache backend rather than only where `delete_pattern`
     exists.

   **There is no `sample_date` column.** The idea existed to answer "what observance is this row,
   really?" for a row whose only identity was display text. The id answers that directly, and
   `feast_rename._names_by_id` reads both languages' display text from the id with no date in the
   loop, so the column was never added. (`feast_name_map.json` has a `sample_date` *field* — a
   different thing, see the bridge below.)

   **After any `armenian-lectionary` bump**, run `remap_feast_names` (dry run, then `--apply`) and
   confirm with `audit_feast_duplicates`. Nothing does this automatically — the migrations applied
   the one-time repairs and, like every migration, run once. Under the id key this is now mostly
   a display-text refresh rather than a rescue. The rule is `hub/services/feast_rename.py`, shared
   verbatim by the command and by migration `0066` so they cannot drift, and it merges
   collisions through `feast_merge.survivor`. (`0065` adds the column and drops the name key,
   `0066` backfills, `0067` takes the new constraint — three migrations because the index has to
   settle between the schema change and the data work.)

   One one-time bridge exists for rows that predate all of this, and it is not part of the
   recurring path: `hub/data/feast_name_map.json`, from the retired sacredtradition.am scrape and
   engines 1.1.x/1.2.x. **Resolve through its `sample_date`, not its `new` name** — the names were
   a snapshot of 1.3.0's display text and 126 of the 229 stopped being emitted verbatim at 2.0.0,
   while the dates beside them still resolve. A legacy row for a day that commemorates nobody
   (14 of the 244 entries are position labels) correctly stays unresolved with a null id.

## Development Environment

This project runs in a **Docker development container**. All Python/Django commands must be executed inside the container using `docker exec`.

### Docker Container Names
- **App container**: `bahk_devcontainer-app-1`
- **Redis container**: `bahk_devcontainer-redis-1`

### Running Commands in Docker

All `python manage.py` commands and Python scripts must be prefixed with:
```bash
docker exec -e IS_PRODUCTION=false bahk_devcontainer-app-1 <command>
```

**Example:**
```bash
# Instead of: python manage.py migrate
# Use:
docker exec -e IS_PRODUCTION=false bahk_devcontainer-app-1 python manage.py migrate
```

## Development Commands

### Setup

```bash
# The Docker container handles dependencies automatically
# No need to create virtual environment or install packages manually

# Run migrations
docker exec bahk_devcontainer-app-1 python manage.py migrate

# Create superuser
docker exec bahk_devcontainer-app-1 python manage.py createsuperuser

# Seed test data
docker exec bahk_devcontainer-app-1 python manage.py seed
```

### Running the Application

```bash
# The development server runs automatically in the container
# Access at http://localhost:8000

# Collect static files
docker exec bahk_devcontainer-app-1 python manage.py collectstatic

# Run Celery worker (runs in container)
docker exec bahk_devcontainer-app-1 celery -A bahk worker -l info

# Run Celery beat scheduler (runs in container)
docker exec bahk_devcontainer-app-1 celery -A bahk beat -l info
```

### CSS Development

```bash
# Build CSS once
npm run build-css

# Watch for changes
npm run watch-css
```

### Testing

**Standard test command** (excludes performance tests):
```bash
docker exec bahk_devcontainer-app-1 python manage.py test --parallel --keepdb --exclude-tag=performance --settings=tests.test_settings
```

> **Note:** `--keepdb` skips recreating the test database on subsequent runs for faster local iteration. If you add or modify migrations, drop `--keepdb` once to rebuild the schema, then add it back.

**Other test commands**:
```bash
# Run all tests
docker exec bahk_devcontainer-app-1 python manage.py test --parallel --settings=tests.test_settings

# Run specific app tests
docker exec bahk_devcontainer-app-1 python manage.py test tests.unit.hub --settings=tests.test_settings
docker exec bahk_devcontainer-app-1 python manage.py test tests.integration --settings=tests.test_settings

# Run specific test class or method
docker exec bahk_devcontainer-app-1 python manage.py test tests.unit.hub.test_models.ModelCreationTests --settings=tests.test_settings

# Run only performance tests
docker exec bahk_devcontainer-app-1 python manage.py test --tag=performance --settings=tests.test_settings
```

See `tests/README.md` for comprehensive testing documentation.

### Database Seeding

```bash
# Seed the database with test data
docker exec bahk_devcontainer-app-1 python manage.py seed

# Creates test users:
# - user1a@email.com / user1b@email.com (Church1, participating in Fast1)
# - user2@email.com (Church2, participating in Fast2)
# - user3@email.com (Church3, no active fast)
# Password for all: default123
```

### Management Commands

```bash
# Event and activity feed management
docker exec bahk_devcontainer-app-1 python manage.py init_event_types
docker exec bahk_devcontainer-app-1 python manage.py populate_activity_feeds
docker exec bahk_devcontainer-app-1 python manage.py cleanup_activity_feeds
docker exec bahk_devcontainer-app-1 python manage.py engagement_report

# Fast and feast management
docker exec bahk_devcontainer-app-1 python manage.py import_readings
docker exec bahk_devcontainer-app-1 python manage.py regenerate_feast_contexts
docker exec bahk_devcontainer-app-1 python manage.py audit_feast_duplicates
docker exec bahk_devcontainer-app-1 python manage.py regenerate_map

# Notifications
docker exec bahk_devcontainer-app-1 python manage.py check_email_status
docker exec bahk_devcontainer-app-1 python manage.py retry_failed_emails

# Data maintenance
docker exec bahk_devcontainer-app-1 python manage.py update_thumbnail_cache
docker exec bahk_devcontainer-app-1 python manage.py geocode_locations
docker exec bahk_devcontainer-app-1 python manage.py cleanup_orphaned_bookmarks
```

## Code Architecture & Conventions

### Models

- Business logic lives in models and model managers
- Use `modeltrans.fields.TranslationField` for translatable content
- Use `FieldTracker` from django-model-utils to track field changes
- Core models: Church → Fast → Participation (links User to Fast)
- Profile extends User with church affiliation and settings

### Views & Serializers

- Use Django REST Framework for API endpoints
- Keep views light; delegate to models, managers, or service modules
- Use `select_related` and `prefetch_related` aggressively
- API endpoints are prefixed with `/api/`
- JWT authentication for mobile app, session auth for web

### Services

- Complex business logic is in `hub/services/` directory
- Examples: activity feed creation, analytics generation
- Keep services testable by accepting clear inputs/outputs

### Signals

- Use sparingly; defined in `signals.py` files
- Common uses: creating activity feed items, updating caches
- Register signals in app's `apps.py` ready() method

### Testing

- All tests use Django's unittest framework (not pytest)
- Inherit from base classes in `tests/base.py`
- Use `TestDataFactory` in `tests/fixtures/test_data.py`
- Tag performance tests with `@tag('performance')`
- Tag slow/load tests with `@tag('performance', 'slow')`

### Feature Development

- When adding models, always create migrations: `docker exec bahk_devcontainer-app-1 python manage.py makemigrations`
- Add translations to new user-facing fields using `TranslationField`
- Create activity feed events for significant user actions
- Run full test suite before submitting PRs: `docker exec bahk_devcontainer-app-1 python manage.py test --parallel --keepdb --exclude-tag=performance --settings=tests.test_settings`
- Follow PEP 8 style guidelines
- Avoid changing unrelated code when implementing new features

### Environment Configuration

Uses `python-decouple` for environment variables. Key variables in `.env`:
- `DEBUG`, `SECRET_KEY`, `DATABASE_URL`
- `REDIS_URL` (for cache and Celery)
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`
- `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (for AI-generated content)
- `BIBLE_API_KEY` (API.Bible text retrieval); see Key Features #6 for the spend-control
  vars: `READING_TEXT_MAX_AGE_DAYS`, `READING_REFRESH_LIMIT` (distinct passages, not
  readings), `READING_FETCH_DAILY_BUDGET`, `BIBLE_API_MONTHLY_BUDGET`
- `SENTRY_DSN` (error monitoring)

### Celery Scheduled Tasks

Defined in `bahk/celery.py`. Daily tasks include:
- Fast reminder notifications (midnight)
- Map updates (1 AM)
- Activity feed cleanup (2 AM)
- Milestone checks (various times)
- Reading text refresh (Monday 4 AM)

### Database

- Production uses PostgreSQL for JSONB support
- Tests and development can use SQLite
- Use Django ORM; avoid raw SQL unless performance critical
- Optimize queries with `select_related` and `prefetch_related`

### Static Files & Media

- Development: Local filesystem storage
- Production: S3 for media files, WhiteNoise for static files
- CSS built from Sass sources in `hub/static/scss/`

### Performance Optimization

- Redis caching is critical; cache keys prefixed with 'bahk'
- Thumbnail URLs cached to reduce S3 API calls
- Analytics data cached aggressively
- Use `USE_ASYNC_ACTIVITY_FEED` setting to toggle async activity feed creation

### Security

- CSRF protection enabled
- Use Django's built-in password validators
- Email authentication backend in `hub.auth.EmailBackend`
- JWT tokens for API authentication

## Common Patterns

### Creating Activity Feed Items

```python
from events.models import Event, EventType

Event.create_event(
    event_type_code=EventType.USER_JOINED_FAST,
    user=user,
    title='User joined fast',
    data={'fast_id': fast.id},
    request=request,
)
```

### Accessing Translated Fields

```python
# In views/serializers
fast.name_i18n  # Returns translated name based on active language
fast.description_i18n  # Falls back to default if translation missing
```

### Query Optimization

```python
# Bad
fasts = Fast.objects.all()
for fast in fasts:
    print(fast.church.name)  # N+1 queries

# Good
fasts = Fast.objects.select_related('church').all()
for fast in fasts:
    print(fast.church.name)  # 1 query
```

### Caching Pattern

```python
from django.core.cache import cache

cache_key = f'bahk:analytics:{user_id}'
data = cache.get(cache_key)
if data is None:
    data = expensive_computation()
    cache.set(cache_key, data, timeout=900)  # 15 minutes
```

### Push Notification Payloads

Push notifications use JSON payloads to navigate users to specific screens in the mobile app. The admin interface at `/admin/notifications/devicetoken/` provides a form builder that generates correct payload structures.

**Deep Link Payloads** (navigate to app screens):
```json
// Fast detail
{ "screen": "fast/48" }

// Devotional
{ "screen": "devotional/123" }

// Prayer
{ "screen": "prayer/77" }

// Prayer request detail
{ "screen": "prayer-request/123" }

// Prayer requests list
{ "screen": "prayer-requests" }

// Video (learning resources have learn/ prefix)
{ "screen": "learn/video/902" }

// Article
{ "screen": "learn/article/305" }

// Recipe
{ "screen": "learn/recipe/120" }

// Prayer set (uses hyphen not underscore)
{ "screen": "prayer-set/44" }

// Activity feed with params
{
  "screen": "activity",
  "params": {
    "activity_type": "announcement",
    "target_id": "987"
  }
}

// With query parameters
{
  "screen": "fast/48",
  "params": {
    "source": "push",
    "ref": "notification"
  }
}
```

**External URL Payloads** (open in browser):
```json
{ "url": "https://example.com/announcement" }
```

**Key Rules**:
- Learning resources (video, article, recipe) always include `learn/` prefix
- Prayer sets use hyphen: `prayer-set` not `prayer_set`
- Activity feed uses `screen: "activity"` with optional `params` object
- External URLs use `url` key (not `screen`)
- Announcements can include `announcement_url` in their data for external links

**Admin Form Features**:
- Route mapping automatically adds correct prefixes
- Visual preview shows app navigation path
- Quick presets with dynamic API loading:
  - **Latest Devotional**: Fetches from `/api/devotionals/by-date/`
  - **Next Major Fast**: Fetches upcoming non-weekly fast from `/api/fasts/`
  - **Current Fast**: Fetches active non-weekly fast from `/api/fasts/`
  - **Activity Feed**: Opens activity screen
- Input validation for IDs and URLs
- Loading spinners during API calls
- Error handling with fallback to manual entry
- **Confirmation modal** before sending:
  - Preview message and JSON payload
  - Warning about irreversible action
  - Keyboard accessible (Escape to cancel)
  - Safe default focus on Cancel button
- Manual override option for advanced payloads

See `notifications/tests/test_push_payload_generation.py` for comprehensive examples and validation tests.

## API Endpoints

Main API routes are in `bahk/urls.py` and individual app `urls.py` files:
- `/api/hub/` - Core fast and profile endpoints
- `/api/learning-resources/` - Devotional content
- `/api/events/` - Activity tracking and analytics
- `/api/icons/` - Icon management
- `/api/token/` - JWT authentication

## Project Structure

```
bahk/
├── bahk/              # Django project settings
├── hub/               # Main app (fasts, churches, profiles)
├── events/            # Activity tracking and analytics
├── notifications/     # Email and push notifications
├── learning_resources/# Devotional content
├── prayers/           # Prayer resources
├── icons/             # Icon management
├── app_management/    # App-level utilities
├── tests/             # Test suite
├── templates/         # Shared templates
├── staticfiles/       # Collected static files
├── manage.py          # Django management script
└── requirements.txt   # Python dependencies
```
