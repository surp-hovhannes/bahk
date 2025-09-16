# Engagement Report Management Command

## Overview

The `engagement_report` Django management command generates comprehensive user engagement analytics for a specified date range. This command analyzes user behavior, fast participation, and system events to provide detailed insights into platform usage and engagement patterns.

## Command Syntax

```bash
python manage.py engagement_report [OPTIONS]
```

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--start` | String | 30 days ago | Start date in YYYY-MM-DD format |
| `--end` | String | Today | End date in YYYY-MM-DD format (inclusive) |
| `--format` | Choice | `json` | Output format: `json` or `csv` |
| `--stdout` | Flag | False | Print consolidated JSON to stdout instead of files |
| `--output-dir` | String | Temp directory | Directory to write output files |
| `--zip` | Flag | False | Zip generated files into an archive |
| `--upload-s3` | Flag | False | Upload files/archive to S3 |
| `--s3-prefix` | String | `engagement-reports/` | S3 key prefix for uploads |

## Generated Metrics

The command produces four main categories of engagement metrics:

### 1. New Users Over Time
- **File**: `new_users_over_time.json/csv`
- **Description**: Daily count of new user registrations
- **Data Structure**:
  ```json
  [
    {
      "date": "2024-01-15",
      "count": 25
    }
  ]
  ```

### 2. Fast Engagement
- **File**: `fasts.json/csv`
- **Description**: Participation metrics for each fast
- **Data Structure**:
  ```json
  [
    {
      "fast_id": 123,
      "fast_name": "Community Fast 2024",
      "church_name": "St. Mary Church",
      "participants": 150,
      "joins_in_period": 45,
      "leaves_in_period": 12
    }
  ]
  ```

### 3. User Activity
- **File**: `user_activity.json/csv`
- **Description**: Individual user activity breakdown by type
- **Data Structure**:
  ```json
  [
    {
      "user_id": 456,
      "username": "john_doe",
      "email": "john@example.com",
      "total_items": 23,
      "by_type": {
        "prayer_request": 8,
        "fast_join": 2,
        "comment": 13
      }
    }
  ]
  ```

### 4. Other Metrics
- **File**: `other_metrics.json`
- **Description**: Additional system-wide analytics
- **Data Structure**:
  ```json
  {
    "events_by_type": {
      "USER_JOINED_FAST": 156,
      "USER_LEFT_FAST": 23,
      "PRAYER_REQUEST_CREATED": 89
    },
    "active_users": 342,
    "top_fasts_by_joins": [
      {
        "fast_id": 123,
        "fast": "Community Fast 2024",
        "joins": 45
      }
    ]
  }
  ```

## Usage Examples

### Basic Usage

Generate a report for the last 30 days (default):
```bash
python manage.py engagement_report
```

### Custom Date Range

Generate a report for January 2024:
```bash
python manage.py engagement_report --start 2024-01-01 --end 2024-01-31
```

### CSV Format

Generate CSV files instead of JSON:
```bash
python manage.py engagement_report --format csv --start 2024-01-01 --end 2024-01-31
```

### Quick Console Output

Print consolidated data to console:
```bash
python manage.py engagement_report --stdout --start 2025-01-01 --end 2025-09-01
```

### Custom Output Directory

Save files to a specific directory:
```bash
python manage.py engagement_report --output-dir /path/to/reports --format csv
```

### Archive and Upload

Create a zip archive and upload to S3:
```bash
python manage.py engagement_report --zip --upload-s3 --s3-prefix monthly-reports/
```

## Output Formats

### JSON Format
- **Structure**: Nested objects with full data hierarchy
- **Use Case**: API consumption, detailed analysis, programmatic processing
- **Files**: Separate JSON file for each metric category

### CSV Format
- **Structure**: Flattened tabular data
- **Use Case**: Spreadsheet analysis, reporting tools, data visualization
- **Special Handling**: User activity types are flattened into `type_*` columns
- **Files**: Separate CSV file for each metric category (except other_metrics.json)

### Consolidated Format (`--stdout`)
- **Structure**: Single JSON object containing all metrics
- **Use Case**: Quick analysis, piping to other tools, debugging
- **Output**: Printed to standard output

## S3 Integration

When using `--upload-s3`, the command requires the following Django settings:

```python
AWS_STORAGE_BUCKET_NAME = "your-bucket-name"
AWS_S3_REGION_NAME = "us-east-1"
AWS_ACCESS_KEY_ID = "your-access-key"
AWS_SECRET_ACCESS_KEY = "your-secret-key"
```

### Upload Behavior
- **With --zip**: Uploads the zip archive
- **Without --zip**: Creates an in-memory zip of all files and uploads it
- **URL**: Returns a public S3 URL for the uploaded file
- **Content-Type**: Set to `application/zip` for proper browser handling

## Data Sources

The command analyzes data from several Django models:

- **User Model**: New user registrations (`date_joined`)
- **Fast Model**: Fast participation and metadata
- **Event Model**: System events and user actions
- **UserActivityFeed Model**: Detailed user activity tracking

## Performance Considerations

- **Database Queries**: Optimized with `select_related` and `prefetch_related`
- **Memory Usage**: Processes data in chunks, suitable for large datasets
- **Execution Time**: Varies with date range and data volume
- **Indexing**: Ensure proper database indexes on timestamp fields

## Error Handling

- **Invalid Dates**: Raises `ValueError` with clear message
- **Missing S3 Settings**: Gracefully fails with informative error
- **Database Errors**: Standard Django ORM exception handling
- **File System Errors**: Uses temporary directories with proper cleanup

## Common Use Cases

### Monthly Reports
```bash
# Generate monthly report for January 2024
python manage.py engagement_report \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --format csv \
  --zip \
  --upload-s3 \
  --s3-prefix monthly-reports/2024-01/
```

### Weekly Analytics
```bash
# Quick weekly summary to console
python manage.py engagement_report \
  --start 2024-01-15 \
  --end 2024-01-21 \
  --stdout
```

### Data Export for Analysis
```bash
# Export detailed data for external analysis
python manage.py engagement_report \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --format csv \
  --output-dir /data/exports/engagement/
```

## Troubleshooting

### Common Issues

1. **"Invalid date format" Error**
   - Ensure dates are in YYYY-MM-DD format
   - Check for typos in date strings

2. **S3 Upload Failures**
   - Verify AWS credentials in Django settings
   - Check S3 bucket permissions
   - Ensure bucket exists and is accessible

3. **Large Dataset Performance**
   - Consider smaller date ranges for very large datasets
   - Monitor database query performance
   - Use `--stdout` for quick testing without file I/O

4. **Memory Issues**
   - Reduce date range for memory-intensive operations
   - Consider running during off-peak hours

## Related Documentation

- [User Activity Feed API Guide](/app/docs/USER_ACTIVITY_FEED_API_GUIDE.md)
- [Events Model Documentation](/app/events/models.py)
- [Django Management Commands](https://docs.djangoproject.com/en/stable/howto/custom-management-commands/)
