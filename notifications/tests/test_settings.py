from django.test import TestCase
from decouple import config

# Test settings
SITE_URL = 'http://testserver'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
EMAIL_HOST_USER = config('EMAIL_TEST_ADDRESS', default='test@test.com')