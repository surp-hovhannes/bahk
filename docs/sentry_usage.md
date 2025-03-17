# Sentry Integration Guide

This document provides information on how Sentry is integrated in our Django application and how to effectively use it for error tracking and performance monitoring.

## Setup

Sentry is configured in `bahk/settings.py`. The integration includes:

1. Error tracking for Django views and middleware
2. Performance monitoring (traces)
3. Integration with Celery for background task monitoring
4. Integration with Redis for cache operations
5. User context association for better debugging
6. Heroku-specific dyno tracking

## Environment Variables

The following environment variables should be set:

- `SENTRY_DSN`: Your Sentry project DSN (required for Sentry to work)
- `SENTRY_ENVIRONMENT`: The environment name (development, staging, production)
- `SENTRY_RELEASE`: Optional version/release identifier

## Heroku-Specific Configuration

When running on Heroku, the following additional information is automatically captured:

- `dyno`: The specific dyno instance (e.g., `web.1`, `worker.2`)
- `dyno_type`: The type of dyno (e.g., `web`, `worker`)
- `heroku_release`: The Heroku release version if available
- `heroku_app`: The Heroku application name if available

These tags allow you to filter Sentry issues by dyno and determine if errors are specific to certain dyno types or instances.

## How to Use Sentry in Your Code

### Basic Error Capturing

```python
import sentry_sdk

try:
    # Your code that might fail
    do_something_risky()
except Exception as e:
    # Capture the exception
    sentry_sdk.capture_exception(e)
    
    # Re-raise if needed or handle gracefully
    raise
```

### Adding Context

```python
# Add context data to help with debugging
sentry_sdk.set_context("user_operation", {
    "operation_type": "payment_process",
    "amount": 100.00,
    "currency": "USD"
})

# Add breadcrumbs to track the sequence of events
sentry_sdk.add_breadcrumb(
    category="payment",
    message="Payment initiated",
    level="info"
)
```

### Performance Monitoring

```python
# Track a custom transaction
with sentry_sdk.start_transaction(op="task", name="Process Payment"):
    # Do some work
    
    # Track a specific operation within the transaction
    with sentry_sdk.start_span(op="db.query", description="Verify account balance"):
        # Database operation here
        check_balance()
    
    # Another operation
    with sentry_sdk.start_span(op="http.request", description="Payment gateway call"):
        # API call to payment processor
        process_payment()
```

## Real World Example

The `FastParticipantsMapView` in `hub/views/fast.py` includes an example of Sentry integration with:

1. Error tracking with context
2. Performance monitoring with transactions and spans
3. Breadcrumbs for tracking request flow

## Debugging Tips

1. **Check Configuration**: Ensure the `SENTRY_DSN` is correctly set in your environment
2. **Check Dashboard**: Sentry issues can be viewed in the Sentry dashboard
3. **Filter by Environment**: Use environment filters in Sentry to focus on relevant issues
4. **Tag Your Events**: Add tags to make searching and filtering easier

## Performance Considerations

- Set an appropriate `traces_sample_rate` for each environment (lower in production)
- Add custom transactions only for critical paths
- Be mindful of sensitive data in context

## Best Practices

1. **Contextual Information**: Always add relevant context to errors
2. **User Information**: Include user context when appropriate
3. **Error Grouping**: Structure your errors to group related issues
4. **Release Tracking**: Set up release tracking to identify which releases introduced issues
5. **Environment Separation**: Keep development, staging, and production errors separate 