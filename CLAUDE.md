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

## Development Environment

This project runs in a **Docker development container**. All Python/Django commands must be executed inside the container using `docker exec`.

### Docker Container Names
- **App container**: `bahk_devcontainer-app-1`
- **Redis container**: `bahk_devcontainer-redis-1`

### Running Commands in Docker

All `python manage.py` commands and Python scripts must be prefixed with:
```bash
docker exec bahk_devcontainer-app-1 <command>
```

**Example:**
```bash
# Instead of: python manage.py migrate
# Use:
docker exec bahk_devcontainer-app-1 python manage.py migrate
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
docker exec bahk_devcontainer-app-1 python manage.py test --exclude-tag=performance --settings=tests.test_settings
```

**Other test commands**:
```bash
# Run all tests
docker exec bahk_devcontainer-app-1 python manage.py test --settings=tests.test_settings

# Run specific app tests
docker exec bahk_devcontainer-app-1 python manage.py test tests.unit.hub --settings=tests.test_settings
docker exec bahk_devcontainer-app-1 python manage.py test tests.integration --settings=tests.test_settings

# Run with keepdb for faster iterations
docker exec bahk_devcontainer-app-1 python manage.py test --keepdb --exclude-tag=performance --settings=tests.test_settings

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
docker exec bahk_devcontainer-app-1 python manage.py import_feasts
docker exec bahk_devcontainer-app-1 python manage.py import_readings
docker exec bahk_devcontainer-app-1 python manage.py regenerate_feast_contexts
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
- Run full test suite before submitting PRs: `docker exec bahk_devcontainer-app-1 python manage.py test --exclude-tag=performance --settings=tests.test_settings`
- Follow PEP 8 style guidelines
- Avoid changing unrelated code when implementing new features

### Environment Configuration

Uses `python-decouple` for environment variables. Key variables in `.env`:
- `DEBUG`, `SECRET_KEY`, `DATABASE_URL`
- `REDIS_URL` (for cache and Celery)
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`
- `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (for AI-generated content)
- `SENTRY_DSN` (error monitoring)

### Celery Scheduled Tasks

Defined in `bahk/celery.py`. Daily tasks include:
- Fast reminder notifications (midnight)
- Feast date creation (12:05 AM)
- Map updates (1 AM)
- Activity feed cleanup (2 AM)
- Milestone checks (various times)

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
