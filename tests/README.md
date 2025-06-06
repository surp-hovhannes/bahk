# Test Suite Documentation

This directory contains all tests for the Fast & Pray application. All tests use Django's unittest framework for consistency.

## Directory Structure

```
tests/
├── unit/               # Unit tests for models, utilities, and isolated components
│   ├── hub/           # Hub app unit tests
│   └── notifications/ # Notifications app unit tests
├── integration/       # Integration tests for API endpoints and service interactions
├── functional/        # Functional tests for workflows and features
├── fixtures/          # Test data factories and utilities
├── tasks/            # Celery task tests
└── base.py           # Base test classes
```

## Test Organization

### Unit Tests (`unit/`)
- Test individual models, methods, and utilities in isolation
- Mock external dependencies
- Fast execution
- Example: Testing model validations, utility functions

### Integration Tests (`integration/`)
- Test API endpoints and view interactions
- Test integration between components
- May use database
- Example: Testing REST API endpoints, authentication

### Functional Tests (`functional/`)
- Test complete user workflows
- Test email sending, task execution
- Test admin interfaces
- Example: Testing complete fast creation workflow

## Running Tests

### Run All Tests
```bash
python manage.py test
```

### Run Specific Test Modules
```bash
# Run all unit tests
python manage.py test tests.unit

# Run hub unit tests
python manage.py test tests.unit.hub

# Run integration tests
python manage.py test tests.integration

# Run functional tests
python manage.py test tests.functional
```

### Run Specific Test Classes or Methods
```bash
# Run specific test class
python manage.py test tests.unit.hub.test_models.ModelCreationTests

# Run specific test method
python manage.py test tests.unit.hub.test_models.ModelCreationTests.test_create_church
```

### Run Tests with Options
```bash
# Verbose output
python manage.py test --verbosity=2

# Keep test database between runs (faster)
python manage.py test --keepdb

# Parallel execution (faster for large test suites)
python manage.py test --parallel

# Run tests matching a pattern
python manage.py test -k test_create
```

## Writing Tests

### Use Base Test Classes

All test classes should inherit from the appropriate base class in `tests/base.py`:

```python
from tests.base import BaseTestCase, BaseAPITestCase

class MyModelTests(BaseTestCase):
    def test_something(self):
        # Use convenience methods
        user = self.create_user(username="testuser")
        church = self.create_church(name="Test Church")
        # ... rest of test
```

### Use Test Data Factory

Use the `TestDataFactory` in `tests/fixtures/test_data.py` for creating test data:

```python
from tests.fixtures.test_data import TestDataFactory

class MyTests(TestCase):
    def setUp(self):
        self.user = TestDataFactory.create_user()
        self.fast = TestDataFactory.create_complete_fast()
```

### Test Naming Conventions

- Test files: `test_<feature>.py`
- Test classes: `<Feature>Tests` (e.g., `ModelCreationTests`)
- Test methods: `test_<what_is_being_tested>` (e.g., `test_create_user_with_profile`)

## Test Coverage

### Generate Coverage Report
```bash
# Run tests with coverage
coverage run --source='.' manage.py test

# View coverage report in terminal
coverage report

# Generate HTML coverage report
coverage html
# Open htmlcov/index.html in browser
```

### Coverage Goals
- Aim for >80% overall coverage
- Critical paths should have >90% coverage
- Focus on testing business logic, not Django internals

## Best Practices

1. **Keep Tests Fast**: Use `TestCase` instead of `TransactionTestCase` when possible
2. **Test One Thing**: Each test method should test one specific behavior
3. **Use Descriptive Names**: Test names should describe what they test
4. **Mock External Services**: Mock API calls, email sending in unit tests
5. **Use Fixtures Wisely**: Create reusable test data with the factory
6. **Clean Up**: Ensure tests don't leave data that affects other tests
7. **Test Edge Cases**: Include tests for error conditions and edge cases

## Debugging Tests

### Run Specific Test with Debugging
```bash
# Run with Python debugger
python -m pdb manage.py test tests.unit.hub.test_models.ModelCreationTests.test_create_church

# Use print statements (visible with verbosity)
python manage.py test --verbosity=2
```

### Keep Test Database for Inspection
```bash
# Preserve test database after test run
python manage.py test --keepdb --debug-mode
```

## Continuous Integration

Tests are automatically run on:
- Every pull request
- Every commit to main branch
- Can be run manually via GitHub Actions

## Common Issues

### Import Errors
- Ensure all test directories have `__init__.py` files
- Check that app is in `INSTALLED_APPS` in settings

### Database Errors
- Run migrations: `python manage.py migrate`
- Use `TransactionTestCase` for tests that need transactions

### Test Discovery Issues
- Test files must start with `test_`
- Test methods must start with `test_`
- Test classes should end with `Tests` or `TestCase`