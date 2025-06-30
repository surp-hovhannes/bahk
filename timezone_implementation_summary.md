# Timezone Implementation Summary

This document summarizes the timezone functionality that has been implemented for user profiles in the Django application.

## Overview

Users can now have their timezone automatically updated when API requests include timezone information that differs from their current timezone. New users default to UTC timezone, and there's a management command to update existing users' timezones based on their location.

## Implementation Details

### 1. Database Changes

**Model Updates:**
- Added `timezone` field to `Profile` model in `hub/models.py`
- Field type: `CharField(max_length=100, default='UTC')`
- Stores timezone in IANA format (e.g., 'America/New_York')
- Added to field tracker for change detection

**Migration:**
- Created migration `0029_add_timezone_to_profile.py`
- Successfully applied to add timezone field with UTC default

### 2. API Changes

**Serializer Updates:**
- Updated `ProfileSerializer` in `hub/serializers.py` to include `timezone` field
- Users can now view and update their timezone via API endpoints
- Timezone field appears in profile API responses

**Profile API Endpoint:**
- `/api/profile/` now includes timezone in GET responses
- PUT/PATCH requests to `/api/profile/` can update timezone

### 3. Automatic Timezone Updates

**Middleware Implementation:**
- Created `TimezoneUpdateMiddleware` in `hub/middleware.py`
- Automatically updates user timezone when API requests include timezone info
- Checks for timezone in:
  - Query parameter: `?tz=America/New_York`
  - HTTP header: `X-Timezone: America/New_York`
- Only updates if timezone differs from current setting
- Added to Django middleware stack in `settings.py`

**Features:**
- Validates timezone strings using `pytz`
- Gracefully handles invalid timezones (logs warning, doesn't break request)
- Only processes authenticated users with profiles
- Optimized to avoid unnecessary database writes

### 4. Web Interface Updates

**Form Updates:**
- Added `timezone` field to `ProfileForm` in `hub/forms.py`
- Users can update timezone via web profile page
- Field includes appropriate CSS classes for styling

### 5. Management Command for Initial Rollout

**Command: `update_user_timezones`**
Location: `hub/management/commands/update_user_timezones.py`

**Purpose:**
Updates existing users' timezones based on their geographical location using coordinates.

**Features:**
- Uses `timezonefinder` library to map coordinates to timezones
- Can install `timezonefinder` automatically with `--install-timezonefinder` flag
- Processes users in batches for performance
- Supports dry-run mode to preview changes
- Updates only users with UTC/empty timezone by default
- Can force update all users with `--force-all` flag

**Usage Examples:**
```bash
# Dry run to see what would be updated
python manage.py update_user_timezones --dry-run

# Update users with UTC timezone based on their location
python manage.py update_user_timezones

# Update all users regardless of current timezone
python manage.py update_user_timezones --force-all

# Install timezonefinder and run update
python manage.py update_user_timezones --install-timezonefinder
```

### 6. Testing

**Test Coverage:**
Created `hub/tests/test_timezone_functionality.py` with comprehensive tests:
- Model tests for timezone field functionality
- Serializer tests for API integration
- Middleware tests for automatic updates
- Integration tests for end-to-end functionality

## Usage Examples

### 1. API Usage

**Get user profile with timezone:**
```bash
GET /api/profile/
Authorization: Bearer <token>

Response:
{
  "user_id": 123,
  "email": "user@example.com",
  "name": "John Doe",
  "location": "New York, NY",
  "timezone": "America/New_York",
  ...
}
```

**Update user timezone:**
```bash
PATCH /api/profile/
Authorization: Bearer <token>
Content-Type: application/json

{
  "timezone": "Europe/London"
}
```

### 2. Automatic Updates via API Requests

**Query Parameter:**
```bash
GET /api/fasts/?tz=America/Los_Angeles
Authorization: Bearer <token>
```
This will update the user's timezone to "America/Los_Angeles" if it's different from their current setting.

**HTTP Header:**
```bash
GET /api/profile/
Authorization: Bearer <token>
X-Timezone: Asia/Tokyo
```
This will update the user's timezone to "Asia/Tokyo" if it's different.

### 3. Management Command Usage

**Basic usage for initial rollout:**
```bash
# Preview changes without making them
python manage.py update_user_timezones --dry-run

# Update users with location data but UTC timezone
python manage.py update_user_timezones

# Force update all users with coordinates
python manage.py update_user_timezones --force-all
```

## Configuration

### Dependencies

For the management command, you may need to install:
```bash
pip install timezonefinder
```

Or use the automatic installation:
```bash
python manage.py update_user_timezones --install-timezonefinder
```

### Middleware Order

The timezone middleware is placed after authentication middleware in `settings.py`:
```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'hub.middleware.TimezoneUpdateMiddleware',  # After auth
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

## Benefits

1. **Automatic Updates**: User timezones stay current as they travel or use different devices
2. **Default UTC**: New users start with a standard timezone
3. **Location-Based Inference**: Existing users get appropriate timezones based on their location
4. **API Integration**: Timezone information flows naturally through existing API calls
5. **Web Interface**: Users can manually adjust timezones via the profile page
6. **Performance Optimized**: Only updates when timezone actually changes
7. **Error Handling**: Gracefully handles invalid timezone data

## Future Enhancements

1. **Frontend Integration**: Update mobile/web apps to send timezone info in API calls
2. **Timezone Validation**: Add more robust timezone validation in forms
3. **User Preferences**: Allow users to override automatic timezone detection
4. **Analytics**: Track timezone distribution for insights
5. **Enhanced Location Mapping**: Use more sophisticated location-to-timezone mapping

## Support

For issues or questions about timezone functionality:
1. Check the logs for timezone middleware warnings
2. Verify timezone strings are valid IANA format
3. Ensure middleware is properly configured
4. Test with the management command in dry-run mode first

The implementation is backward compatible and should not affect existing functionality.