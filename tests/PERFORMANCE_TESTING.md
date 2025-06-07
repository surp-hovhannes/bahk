# Performance Testing

This project includes performance tests that are tagged to run separately from regular unit tests.

## Test Tags

### `performance`
Tests that measure response times and check basic performance criteria.

### `slow` 
Tests that take longer to run (e.g., load testing with multiple requests).

## Running Tests

### Run ALL tests (default):
```bash
python manage.py test --settings=tests.test_settings
```

### Run ONLY performance tests:
```bash
python manage.py test --tag=performance --settings=tests.test_settings
```

### Run ONLY slow/load tests:
```bash
python manage.py test --tag=slow --settings=tests.test_settings
```

### Run regular tests WITHOUT performance tests:
```bash
python manage.py test --exclude-tag=performance --settings=tests.test_settings
```

### Run regular tests WITHOUT slow tests:
```bash
python manage.py test --exclude-tag=slow --settings=tests.test_settings
```

### Run regular tests WITHOUT performance AND slow tests:
```bash
python manage.py test --exclude-tag=performance --exclude-tag=slow --settings=tests.test_settings
```

### Combine tags (tests tagged with BOTH performance AND slow):
```bash
python manage.py test --tag=performance --tag=slow --settings=tests.test_settings
```

## Test Counts

- **All tests**: 121 tests
- **Performance tests**: 8 tests (includes 2 slow tests)
- **Slow tests**: 2 tests  
- **Regular tests only** (excluding all performance): 113 tests

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

## Adding New Performance Tests

To tag a new test as a performance test:

```python
from django.test.utils import tag

class MyTestClass(TestCase):
    
    @tag('performance')
    def test_my_performance_test(self):
        """Basic performance test."""
        pass
    
    @tag('performance', 'slow')  
    def test_my_load_test(self):
        """Load/stress test."""
        pass
```

## CI/CD Integration

For continuous integration, you may want to:

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