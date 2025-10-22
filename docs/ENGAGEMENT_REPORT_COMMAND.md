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

The command produces seven main categories of engagement metrics:

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
- **Description**: Individual user activity breakdown by type with account creation date
- **Data Structure**:
  ```json
  [
    {
      "user_id": 456,
      "username": "john_doe",
      "email": "john@example.com",
      "date_joined": "2023-06-15T10:30:00Z",
      "total_items": 23,
      "by_type": {
        "prayer_request": 8,
        "fast_join": 2,
        "comment": 13
      }
    }
  ]
  ```

### 4. User Activity Timeline (NEW)
- **File**: `user_activity_timeline.json/csv`
- **Description**: Detailed timeline of individual user activities with timestamps
- **Use Case**: LLM analysis, detailed engagement tracking, timeliness assessment
- **Data Structure**:
  ```json
  [
    {
      "user_id": 456,
      "username": "john_doe",
      "activity_type": "devotional_viewed",
      "timestamp": "2024-01-15T14:23:45Z",
      "title": "Devotional viewed",
      "description": "User viewed devotional for Day 3",
      "target_type": "devotional",
      "target_id": 789
    }
  ]
  ```

### 5. User Fast Participation (NEW)
- **File**: `user_fast_participation.json/csv`
- **Description**: Complete history of user fast memberships with join/leave timestamps
- **Use Case**: Tracking fast engagement trends, identifying churned users
- **Data Structure**:
  ```json
  [
    {
      "user_id": 456,
      "username": "john_doe",
      "email": "john@example.com",
      "fast_id": 123,
      "fast_name": "Community Fast 2024",
      "church_name": "St. Mary Church",
      "joined_at": "2024-01-05T08:15:00Z",
      "left_at": null,
      "status": "active"
    }
  ]
  ```

### 6. Retention Cohorts (NEW)
- **File**: `retention_cohorts.json/csv`
- **Description**: Weekly user cohorts showing retention rates and activity levels
- **Use Case**: Understanding user retention, cohort analysis, growth tracking
- **Data Structure**:
  ```json
  [
    {
      "cohort_week": "2024-W03",
      "cohort_start_date": "2024-01-15",
      "total_users": 45,
      "active_users": 23,
      "retention_rate": 51.11,
      "avg_activities_per_user": 12.5,
      "cohort_age_weeks": 4
    }
  ]
  ```

### 7. Other Metrics (ENHANCED)
- **File**: `other_metrics.json`
- **Description**: Comprehensive system-wide analytics including screen views and engagement patterns
- **Data Structure**:
  ```json
  {
    "events_by_type": {
      "user_joined_fast": 156,
      "user_left_fast": 23,
      "devotional_viewed": 342,
      "checklist_used": 189,
      "screen_view": 1256
    },
    "active_users": 342,
    "top_fasts_by_joins": [
      {
        "fast_id": 123,
        "fast": "Community Fast 2024",
        "joins": 45
      }
    ],
    "screen_views": {
      "total": 1256,
      "unique_users": 234,
      "avg_per_user": 5.37,
      "top_screens": [
        {"screen": "fast_detail", "views": 456},
        {"screen": "devotional_view", "views": 342}
      ],
      "daily_timeline": {
        "2024-01-15": 89,
        "2024-01-16": 92
      }
    },
    "engagement_patterns": {
      "devotionals": {
        "total_views": 342,
        "unique_users": 156,
        "avg_per_user": 2.19,
        "top_users": [
          {"user_id": 456, "username": "john_doe", "views": 45}
        ]
      },
      "checklists": {
        "total_uses": 189,
        "unique_users": 89,
        "avg_per_user": 2.12,
        "avg_days_between_uses": 3.5
      },
      "cross_engagement": {
        "users_both_devotional_and_checklist": 67,
        "users_in_fasts_with_activity": 145,
        "users_without_fasts_with_activity": 34,
        "avg_activities_users_with_fast": 8.5,
        "avg_activities_users_without_fast": 3.2
      }
    }
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

## New Features for Timeliness Analysis

The enhanced engagement report now includes three powerful new data sets specifically designed for analyzing user engagement timeliness and trends:

### User Activity Timeline

This detailed timeline provides individual activity records with precise timestamps, enabling you to:

- **Analyze engagement patterns**: See exactly when users are most active
- **Track timeliness**: Identify gaps in engagement or periods of high activity
- **User journey mapping**: Understand the sequence of user interactions
- **LLM-friendly format**: Clean, structured data perfect for AI analysis

### User Fast Participation History

Complete fast membership history with join/leave timestamps allows you to:

- **Track fast lifecycle**: See how long users stay in fasts
- **Identify churn patterns**: Users who have left fasts
- **Measure commitment**: Time between joining and leaving
- **Cross-reference with activity**: Correlate fast membership with engagement

### Retention Cohorts

Weekly cohort analysis provides insights into:

- **User retention trends**: How well are you retaining users over time?
- **Cohort comparison**: Which cohorts are most engaged?
- **Age-based patterns**: How does engagement change as cohorts age?
- **Activity benchmarks**: Average activity levels by cohort

### Enhanced Engagement Metrics

The enhanced `other_metrics` now includes:

- **Screen View Analytics**: Most popular screens, viewing patterns
- **Devotional Engagement**: Who's viewing devotionals and how often
- **Checklist Usage**: Frequency and consistency of checklist usage
- **Cross-Engagement Analysis**: Users engaging with multiple features
- **Fast vs Non-Fast Users**: Activity comparison between groups

## Using Reports for LLM Analysis

The enhanced engagement report is optimized for Large Language Model analysis. Here's how to get the best results:

### Recommended Format for LLM Analysis

**For Overview Analysis:**
```bash
python manage.py engagement_report --format json --start 2024-01-01 --end 2024-01-31
```

Load these files into your LLM context:
- `user_activity.json` - For understanding who is active
- `retention_cohorts.json` - For cohort and retention analysis
- `other_metrics.json` - For system-wide patterns

**For Detailed Timeline Analysis:**
```bash
python manage.py engagement_report --format csv --start 2024-01-01 --end 2024-01-31
```

CSV format is more compact for timeline data:
- `user_activity_timeline.csv` - Individual activities with timestamps
- `user_fast_participation.csv` - Fast membership history

### Sample LLM Prompts

**Engagement Timeliness Analysis:**
```
Analyze the user_activity_timeline.csv and identify:
1. Peak engagement times (hour of day, day of week)
2. Users with declining engagement (increasing gaps between activities)
3. Most engaged users (high frequency, recent activity)
4. Users at risk of churn (no activity in past 7 days)
```

**Cohort Performance:**
```
Using retention_cohorts.json, compare the performance of:
1. Recent cohorts (last 4 weeks) vs older cohorts
2. Identify which cohort week had the best retention
3. Calculate retention rate trends over cohort age
4. Recommend optimal onboarding period based on retention data
```

**Feature Adoption:**
```
Using other_metrics.json engagement_patterns, analyze:
1. What percentage of users engage with both devotionals and checklists?
2. How does fast membership correlate with activity levels?
3. Which screens are most viewed and by how many users?
4. Identify power users based on devotional and checklist engagement
```

**Fast Participation Trends:**
```
Analyze user_fast_participation.csv to find:
1. Average time users stay in a fast before leaving
2. Which fasts have the highest retention?
3. Users who have joined multiple fasts
4. Identify users who left fasts recently (potential re-engagement targets)
```

### Best Practices

1. **Date Range Selection**: 
   - Use 30-90 days for monthly analysis
   - Use 7 days for weekly check-ins
   - Use 6-12 months for long-term trend analysis

2. **File Selection**:
   - Start with `other_metrics.json` for high-level overview
   - Use `retention_cohorts.json` for strategic planning
   - Dive into `user_activity_timeline` for user-specific investigations

3. **CSV vs JSON**:
   - JSON: Better for nested data, full context
   - CSV: More compact, easier to filter/sort in spreadsheets or LLMs

4. **Combining Data Sets**:
   - Cross-reference user_id across files
   - Join user_activity with user_fast_participation to correlate fast membership with activity
   - Combine timeline data with cohort data for time-based segmentation

## Related Documentation

- [User Activity Feed API Guide](/app/docs/USER_ACTIVITY_FEED_API_GUIDE.md)
- [Events Model Documentation](/app/events/models.py)
- [Django Management Commands](https://docs.djangoproject.com/en/stable/howto/custom-management-commands/)
