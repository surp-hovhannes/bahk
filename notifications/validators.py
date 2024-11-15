import re

def is_valid_expo_push_token(token):
    """Validate the format of an Expo push token."""
    pattern = r'^ExponentPushToken\[[a-zA-Z0-9_-]+\]$'
    return bool(re.match(pattern, token)) 