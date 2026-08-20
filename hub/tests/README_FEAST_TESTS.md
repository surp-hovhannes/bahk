# Feast Feature Tests

This document summarizes the comprehensive test coverage added for the Feast feature.

## Test Files Created

### 1. `test_audit_feast_duplicates.py` + `test_feast_merge.py`

Cover the audit command (which stored names the engine no longer emits) and the merge rule the
re-key migration applies. `test_import_feasts.py` and `test_scrape_feast.py` are gone: the
scrape was retired in favour of the offline engine, and feasts are no longer imported per date.

### 2. `test_scrape_feast.py` - Utility Function Tests
**11 test cases** covering the `scrape_feast` utility function:

- ✅ `test_scrape_feast_with_both_translations` - Scrapes feast with English and Armenian
- ✅ `test_scrape_feast_english_only` - Handles English-only feasts
- ✅ `test_scrape_feast_armenian_only` - Handles Armenian-only feasts
- ✅ `test_scrape_feast_no_feast_found` - Returns None when no feast exists
- ✅ `test_scrape_feast_with_nested_tags` - Strips HTML tags from feast names
- ✅ `test_scrape_feast_url_error` - Handles network errors gracefully
- ✅ `test_scrape_feast_http_error` - Handles HTTP error status codes
- ✅ `test_scrape_feast_unsupported_church` - Returns None for unsupported churches
- ✅ `test_scrape_feast_empty_dname` - Handles empty feast names
- ✅ `test_scrape_feast_date_format` - Verifies correct date formatting in URLs
- ✅ `test_scrape_feast_user_agent_header` - Ensures User-Agent header is included

### 3. `test_feast_model.py` - Model Tests
**13 test cases** covering the `Feast` model:

- ✅ `test_create_feast_basic` - Creates basic feast
- ✅ `test_feast_with_armenian_translation` - Saves Armenian translations using i18n field
- ✅ `test_feast_unique_constraint` - Enforces unique constraint on date + church
- ✅ `test_feast_different_churches_same_date` - Allows same date for different churches
- ✅ `test_feast_str_representation` - Tests string representation
- ✅ `test_feast_related_name` - Verifies reverse relationship through `church.feasts`
- ✅ `test_feast_translation_field_access` - Tests translation field access
- ✅ `test_feast_update_translation_only` - Updates only Armenian translation
- ✅ `test_feast_delete_cascade` - Tests feast deletion
- ✅ `test_feast_ordering_by_date` - Verifies date-based ordering
- ✅ `test_feast_filter_by_date_range` - Tests date range filtering
- ✅ `test_feast_default_church` - Uses default church when not specified
- ✅ `test_feast_translation_null_handling` - Handles None/null translations

## Total Test Coverage

- **32 total test cases**
- **3 test files**
- All tests passing ✅

## Running the Tests

Run all feast tests:
```bash
python manage.py test hub.tests.test_audit_feast_duplicates hub.tests.test_feast_merge hub.tests.test_feast_model --exclude-tag=performance --settings=tests.test_settings
```

Run individual test files:
```bash
# Import command tests
python manage.py test hub.tests.test_audit_feast_duplicates --exclude-tag=performance --settings=tests.test_settings

# Scrape utility tests
python manage.py test hub.tests.test_scrape_feast --exclude-tag=performance --settings=tests.test_settings

# Model tests
python manage.py test hub.tests.test_feast_model --exclude-tag=performance --settings=tests.test_settings
```

## Test Patterns

These tests follow Django best practices and the patterns established in the existing codebase:

1. **Mocking external dependencies** - Uses `unittest.mock.patch` to mock `urllib.request.urlopen` for web scraping tests
2. **i18n field testing** - Properly tests the `modeltrans` translation field pattern
3. **Database constraints** - Verifies unique constraints and relationships
4. **Edge case coverage** - Tests error conditions, empty data, and invalid inputs
5. **Management command testing** - Uses `StringIO` for command output and proper date handling

## Related Files

The tests cover functionality in:
- `hub/models.py` - Feast model
- `hub/utils.py` - scrape_feast function
- `hub/management/commands/audit_feast_duplicates.py` - audit command
- `hub/admin.py` - FeastAdmin (admin interface - not directly tested)

