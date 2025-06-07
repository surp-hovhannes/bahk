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

### Performance Tests
Tests that measure response times and check performance criteria. These are tagged for selective execution:

#### `@tag('performance')`
- Basic performance tests that measure response times
- Check that endpoints respond within acceptable time limits
- Examples: Fast list/detail endpoint timing, join/leave operations

#### `@tag('performance', 'slow')`
- Load tests with multiple requests
- Stress testing with various parameters
- Examples: Multiple sequential requests, pagination performance testing

## Test Counts

- **All tests**: 121 tests
- **Performance tests**: 8 tests (includes 2 slow tests)
- **Slow tests**: 2 tests  
- **Regular tests only** (excluding all performance): 113 tests

## Running Tests

### Run All Tests
```bash
python manage.py test --settings=tests.test_settings
```

### Run Tests by Type
```bash
# Run all unit tests
python manage.py test tests.unit --settings=tests.test_settings

# Run hub unit tests
python manage.py test tests.unit.hub --settings=tests.test_settings

# Run integration tests
python manage.py test tests.integration --settings=tests.test_settings

# Run functional tests
python manage.py test tests.functional --settings=tests.test_settings
```

### Run Performance Tests
```bash
# Run ONLY performance tests
python manage.py test --tag=performance --settings=tests.test_settings

# Run ONLY slow/load tests
python manage.py test --tag=slow --settings=tests.test_settings

# Run regular tests WITHOUT performance tests
python manage.py test --exclude-tag=performance --settings=tests.test_settings

# Run regular tests WITHOUT slow tests
python manage.py test --exclude-tag=slow --settings=tests.test_settings

# Run regular tests WITHOUT performance AND slow tests
python manage.py test --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings

# Combine tags (tests tagged with BOTH performance AND slow)
python manage.py test --tag=performance --tag=slow --settings=tests.test_settings
```

### Run Specific Test Classes or Methods
```bash
# Run specific test class
python manage.py test tests.unit.hub.test_models.ModelCreationTests --settings=tests.test_settings

# Run specific test method
python manage.py test tests.unit.hub.test_models.ModelCreationTests.test_create_church --settings=tests.test_settings
```

### Run Tests with Options
```bash
# Verbose output
python manage.py test --verbosity=2 --settings=tests.test_settings

# Keep test database between runs (faster)
python manage.py test --keepdb --settings=tests.test_settings

# Parallel execution (faster for large test suites)
python manage.py test --parallel --settings=tests.test_settings

# Run tests matching a pattern
python manage.py test -k test_create --settings=tests.test_settings
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

### Writing Performance Tests

To tag a test as a performance test:

```python
from django.test.utils import tag
from django.test import TestCase
import time

class MyPerformanceTests(TestCase):
    
    @tag('performance')
    def test_my_performance_test(self):
        """Basic performance test."""
        start_time = time.time()
        # ... test logic
        end_time = time.time()
        self.assertLess(end_time - start_time, 1.0)  # Should complete in < 1 second
    
    @tag('performance', 'slow')  
    def test_my_load_test(self):
        """Load/stress test."""
        # Multiple requests or heavy operations
        for i in range(10):
            # ... test logic
        pass
```

### Test Naming Conventions

- Test files: `test_<feature>.py`
- Test classes: `<Feature>Tests` (e.g., `ModelCreationTests`)
- Test methods: `test_<what_is_being_tested>` (e.g., `test_create_user_with_profile`)

## Performance Test Categories

### Basic Performance Tests (`@tag('performance')`)
- `test_fast_list_returns_200` - Fast list endpoint timing
- `test_fast_detail_returns_correct_data` - Fast detail endpoint timing  
- `test_fast_by_date_returns_correct_data` - Fast by date endpoint timing
- `test_join_and_leave_fast` - Join/leave operations timing
- `test_fast_participants_endpoint` - Participants endpoint timing
- `test_fast_stats_endpoint` - Stats endpoint timing

### Load Tests (`@tag('performance', 'slow')`)
- `test_endpoints_under_load` - Multiple sequential requests to endpoints
- `test_performance_with_different_page_sizes` - Pagination performance with various page sizes

## Test Coverage

### Generate Coverage Report
```bash
# Run tests with coverage
coverage run --source='.' manage.py test --settings=tests.test_settings

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
8. **Tag Performance Tests**: Use `@tag('performance')` for performance tests, `@tag('performance', 'slow')` for load tests

## Debugging Tests

### Run Specific Test with Debugging
```bash
# Run with Python debugger
python -m pdb manage.py test tests.unit.hub.test_models.ModelCreationTests.test_create_church --settings=tests.test_settings

# Use print statements (visible with verbosity)
python manage.py test --verbosity=2 --settings=tests.test_settings
```

### Keep Test Database for Inspection
```bash
# Preserve test database after test run
python manage.py test --keepdb --debug-mode --settings=tests.test_settings
```

## Continuous Integration

Tests are automatically run on:
- Every pull request
- Every commit to main branch
- Can be run manually via GitHub Actions

### CI/CD Performance Test Strategy

For continuous integration, consider different test execution strategies:

1. **Regular CI runs**: Exclude all performance tests (fastest)
   ```bash
   python manage.py test --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings
   ```

2. **Quick CI runs**: Exclude only slow tests
   ```bash
   python manage.py test --exclude-tag=slow --settings=tests.test_settings
   ```

3. **Nightly performance runs**: Include all performance tests
   ```bash
   python manage.py test --tag=performance --settings=tests.test_settings
   ```

4. **Pre-deployment**: Run basic performance tests only
   ```bash
   python manage.py test --tag=performance --exclude-tag=slow --settings=tests.test_settings
   ```

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

### Performance Test Issues
- Performance tests may be sensitive to system load
- Consider running performance tests in isolation
- Use consistent test environments for reliable results