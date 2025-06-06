# Test Suite Refactoring Plan

## Current State Analysis

### Test Locations
1. **Root tests**: `/workspace/tests/`
   - Mix of Django unittest and pytest formats
   - Contains integration, unit, tasks, and fixtures subdirectories
   
2. **App-specific tests**:
   - `/workspace/hub/tests/` - Hub app tests (Django unittest format)
   - `/workspace/notifications/tests/` - Notifications app tests (Django unittest format)

### Test Formats
- **Django unittest format** (majority): Uses `TestCase`, `APITestCase` from Django/DRF
- **pytest format** (minority): Uses `@pytest.mark` decorators and fixtures

### Issues Identified
1. Missing `__init__.py` files (now fixed)
2. Inconsistent test formats
3. Tests scattered across multiple locations
4. Outdated tests that need updating
5. Pytest configuration files that won't be needed

## Refactoring Strategy

### Phase 1: Standardize on Django unittest format
Since most tests already use Django's unittest format, we'll convert all pytest tests to this format.

### Phase 2: Reorganize test structure
Create a centralized test structure under `/workspace/tests/`:
```
tests/
├── __init__.py
├── unit/
│   ├── __init__.py
│   ├── hub/
│   │   ├── __init__.py
│   │   ├── test_models.py
│   │   ├── test_serializers.py
│   │   └── test_utils.py
│   └── notifications/
│       ├── __init__.py
│       └── test_models.py
├── integration/
│   ├── __init__.py
│   ├── test_auth.py (renamed from test_jwt.py)
│   ├── test_fast_endpoints.py
│   ├── test_notification_endpoints.py
│   └── test_aws_services.py
├── functional/
│   ├── __init__.py
│   ├── test_email_workflows.py
│   ├── test_fast_workflows.py
│   └── test_notification_workflows.py
└── fixtures/
    ├── __init__.py
    └── test_data.py
```

### Phase 3: Tests to convert from pytest to Django unittest
1. `/workspace/tests/integration/test_endpoints.py`
2. `/workspace/tests/integration/test_aws_location.py`
3. `/workspace/tests/tasks/test_add.py`
4. `/workspace/tests/unit/test_models.py`

### Phase 4: Tests to update or disable
1. `test_fast_endpoints.py` - Review and update to match current API
2. `test_jwt.py` - Rename to `test_auth.py` and ensure it covers current auth flow

### Phase 5: Remove pytest configuration
1. Remove `conftest.py`
2. Remove `fixtures/model_fixtures.py` (convert to Django fixtures)
3. Update `pytest.ini` or remove if not needed

## Execution Plan

### Step 1: Convert pytest tests to Django unittest
- Convert parametrized tests to multiple test methods or use `subTest()`
- Replace pytest fixtures with Django's `setUp()` method
- Replace `assert` statements with Django's assertion methods

### Step 2: Move and reorganize tests
- Move app-specific tests to centralized location
- Group tests by type (unit, integration, functional)
- Ensure consistent naming conventions

### Step 3: Update outdated tests
- Review API endpoints and update tests accordingly
- Ensure test coverage for current functionality
- Add missing tests for new features

### Step 4: Create test utilities
- Create base test classes for common functionality
- Create factory methods for test data creation
- Add test mixins for common test patterns

## Benefits
1. **Consistency**: All tests use the same format and structure
2. **Discoverability**: Tests are easier to find and run
3. **Maintainability**: Clearer organization makes updates easier
4. **Performance**: Django's test runner optimizations
5. **Integration**: Better integration with Django's testing tools

## Migration Commands
```bash
# Run all tests after refactoring
python manage.py test

# Run specific test modules
python manage.py test tests.unit.hub
python manage.py test tests.integration

# Run with coverage
coverage run --source='.' manage.py test
coverage report
```