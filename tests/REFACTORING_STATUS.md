# Test Refactoring Status

## ✅ Completed Tasks

1. **Added missing `__init__.py` files**:
   - `/workspace/tests/integration/__init__.py`
   - `/workspace/tests/tasks/__init__.py`
   - `/workspace/tests/unit/__init__.py`
   - `/workspace/tests/fixtures/__init__.py`
   - `/workspace/hub/tests/__init__.py`
   - `/workspace/tests/unit/hub/__init__.py`
   - `/workspace/tests/unit/notifications/__init__.py`
   - `/workspace/tests/functional/__init__.py`

2. **Converted pytest tests to Django unittest**:
   - `/workspace/tests/tasks/test_add.py` ✅
   - `/workspace/tests/unit/test_models.py` ✅
   - `/workspace/tests/integration/test_endpoints.py` ✅
   - `/workspace/tests/integration/test_aws_location.py` ✅

3. **Renamed files**:
   - `test_jwt.py` → `test_auth.py` ✅

4. **Removed pytest configuration**:
   - Deleted `pytest.ini` ✅
   - Deleted `tests/conftest.py` ✅
   - Deleted `tests/fixtures/model_fixtures.py` ✅

5. **Created new test utilities**:
   - `/workspace/tests/fixtures/test_data.py` - Django test data factory ✅
   - `/workspace/tests/base.py` - Base test classes ✅
   - `/workspace/tests/TEST_REFACTORING_PLAN.md` - Refactoring plan ✅

## 📋 Remaining Tasks

### 1. Move Tests to Organized Structure
Currently tests are scattered. They need to be moved to:

**Unit Tests** (move to `/workspace/tests/unit/`):
- `/workspace/hub/tests/test_llm_prompt.py` → `/workspace/tests/unit/hub/test_models_llm.py`
- `/workspace/notifications/tests/test_settings.py` → `/workspace/tests/unit/notifications/test_settings.py`

**Integration Tests** (keep in `/workspace/tests/integration/`):
- `/workspace/tests/test_fast_endpoints.py` (already here, needs review)
- `/workspace/tests/test_participant_lists.py` → `/workspace/tests/integration/test_participant_endpoints.py`
- `/workspace/tests/test_password_reset.py` → `/workspace/tests/integration/test_password_reset.py`

**Functional Tests** (move to `/workspace/tests/functional/`):
- `/workspace/hub/tests/test_email_tasks.py` → `/workspace/tests/functional/test_email_workflows.py`
- `/workspace/hub/tests/test_email_templates.py` → `/workspace/tests/functional/test_email_templates.py`
- `/workspace/hub/tests/test_fast_views.py` → `/workspace/tests/functional/test_fast_workflows.py`
- `/workspace/hub/tests/test_reading_context.py` → `/workspace/tests/functional/test_reading_context.py`
- `/workspace/notifications/tests/test_device_tokens.py` → `/workspace/tests/functional/test_notification_workflows.py`
- `/workspace/notifications/tests/test_email_templates.py` → `/workspace/tests/functional/test_notification_email_templates.py`
- `/workspace/notifications/tests/test_promo_email_admin.py` → `/workspace/tests/functional/test_promo_email_admin.py`
- `/workspace/notifications/tests/test_promo_emails.py` → `/workspace/tests/functional/test_promo_email_workflows.py`
- `/workspace/notifications/tests/test_unsubscribe.py` → `/workspace/tests/functional/test_unsubscribe_workflow.py`

### 2. Update Imports in Moved Tests
After moving tests, update their imports to use the new base classes and test factory.

### 3. Review and Update Outdated Tests
- Review `/workspace/tests/test_fast_endpoints.py` - ensure it matches current API
- Review `/workspace/tests/integration/test_auth.py` - ensure JWT auth is still relevant

### 4. Create Test Running Documentation
Create a `README.md` in the tests directory explaining:
- How to run all tests
- How to run specific test modules
- Test organization structure
- How to add new tests

## 🏃 Running Tests

After refactoring, tests can be run using Django's test runner:

```bash
# Run all tests
python manage.py test

# Run specific app tests
python manage.py test tests.unit.hub
python manage.py test tests.integration
python manage.py test tests.functional

# Run with verbosity
python manage.py test --verbosity=2

# Run specific test class
python manage.py test tests.unit.hub.test_models.ModelCreationTests

# Run specific test method
python manage.py test tests.unit.hub.test_models.ModelCreationTests.test_create_church
```

## 📊 Test Coverage

To check test coverage after refactoring:

```bash
coverage run --source='.' manage.py test
coverage report
coverage html  # Creates htmlcov/index.html with detailed report
```