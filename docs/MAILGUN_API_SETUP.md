# Mailgun API Integration

This document explains how the application has been configured to use Mailgun API for sending emails instead of SMTP.

## Overview

The application now uses **Mailgun API** via the `django-anymail` library for all email sending, including:
- User notifications
- Fast reminders  
- Promotional emails
- Password reset emails
- All other system emails

## Configuration

### Required Environment Variables

Make sure you have these environment variables set:

```bash
# Mailgun API configuration
MAILGUN_API_KEY=your-mailgun-api-key-here
MAILGUN_DOMAIN=your-mailgun-domain.com

# Email settings
DEFAULT_FROM_EMAIL=your-from-email@your-domain.com
EMAIL_TEST_ADDRESS=test@example.com  # For testing
```

### Django Settings

The following settings are configured in `bahk/settings.py`:

```python
# Mailgun API Configuration (via Django Anymail)
ANYMAIL = {
    "MAILGUN_API_KEY": config('MAILGUN_API_KEY'),
    "MAILGUN_SENDER_DOMAIN": config('MAILGUN_DOMAIN'),
    "MAILGUN_API_URL": "https://api.mailgun.net/v3",  # US servers
}

# Use Mailgun API backend (not SMTP)
EMAIL_BACKEND = "anymail.backends.mailgun.EmailBackend"

# Email settings
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL')
EMAIL_HOST_USER = DEFAULT_FROM_EMAIL  # For backward compatibility
```

## Testing the Integration

### Method 1: Management Command (Recommended)

Use the custom management command to test the Mailgun API integration:

```bash
# Basic test
python manage.py test_mailgun_api

# Test with custom email address
python manage.py test_mailgun_api --email your-email@example.com

# Verbose output with configuration details
python manage.py test_mailgun_api --verbose
```

### Method 2: Python Shell

You can also test directly in the Django shell:

```python
python manage.py shell

# In the shell:
from hub.utils import test_mailgun_api
result = test_mailgun_api()
print(result)
```

### Method 3: Celery Task

Test via Celery task:

```python
from hub.tasks.email_tasks import test_mailgun_api_task
result = test_mailgun_api_task.delay()
print(result.get())
```

## Key Benefits of API vs SMTP

1. **Better Reliability**: API calls are more reliable than SMTP connections
2. **Enhanced Features**: Access to advanced Mailgun features like:
   - Email tracking and analytics
   - Bounce and complaint handling
   - Email validation
   - Scheduled sending
3. **Better Error Handling**: More detailed error messages and status codes
4. **No Connection Issues**: No need to worry about SMTP connection timeouts or authentication issues

## Troubleshooting

### Common Issues

1. **Invalid API Key**: Make sure your `MAILGUN_API_KEY` environment variable is set correctly
2. **Domain Not Verified**: Ensure your domain is verified in your Mailgun dashboard
3. **Wrong Region**: If using EU servers, update the API URL in settings:
   ```python
   "MAILGUN_API_URL": "https://api.eu.mailgun.net/v3"
   ```

### Checking Configuration

Run the test command with verbose output to see your current configuration:

```bash
python manage.py test_mailgun_api --verbose
```

### Logs

Check the application logs for detailed error messages if emails fail to send. The enhanced test function provides comprehensive logging.

## Migration Notes

- **SMTP Settings Removed**: The old SMTP settings (`EMAIL_HOST`, `EMAIL_PORT`, etc.) have been removed from the configuration
- **Backward Compatibility**: `EMAIL_HOST_USER` is still available for backward compatibility but now points to `DEFAULT_FROM_EMAIL`
- **All Email Types**: Both promotional emails and system notifications now use the API
- **Testing**: All existing tests continue to work as they use the locmem backend during testing

## Next Steps

1. Test the integration using the management command
2. Monitor email delivery in your Mailgun dashboard
3. Consider setting up webhooks for bounce/complaint handling
4. Review and update any custom email templates if needed

For more information about Mailgun API features, see the [Mailgun documentation](https://documentation.mailgun.com/) and [Django Anymail documentation](https://anymail.readthedocs.io/).