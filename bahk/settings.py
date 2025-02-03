"""
Django settings for bahk project.

Generated by 'django-admin startproject' using Django 4.2.9.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""
import datetime
import os
import sys
import dj_database_url
import django_heroku

from pathlib import Path
from decouple import config, Csv
from ssl import CERT_NONE


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Default church name (used if user is not logged in)
DEFAULT_CHURCH_NAME = "Armenian Apostolic Church"

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-340y5$yaevl3%&50ob@)r@6htxve-6b0161m03j$)4r_%g8djq')
# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

# Determine if we are running in production
IS_PRODUCTION = config('IS_PRODUCTION', default=False, cast=bool)


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'hub.apps.HubConfig',
    'rest_framework',
    'storages',
    'imagekit',
    'django_celery_beat',
    'anymail',
    'app_management',
    'markdownx',
    'corsheaders',
    'notifications',
    'learning_resources',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'bahk.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'bahk.context_processors.current_date'
            ],
        },
    },
]

WSGI_APPLICATION = 'bahk.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
import dj_database_url

if os.getenv('DATABASE_URL'):
    # We are running on Heroku, use the DATABASE_URL environment variable
    DATABASES = {
        'default': dj_database_url.config(default=os.getenv('DATABASE_URL'))
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Cache Configuration
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': config('REDIS_URL', default='redis://redis:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_CLASS': 'redis.BlockingConnectionPool',
            'CONNECTION_POOL_CLASS_KWARGS': {
                'max_connections': 50,
                'timeout': 20,
            },
            'MAX_CONNECTIONS': 1000,
            'PICKLE_VERSION': -1,
            'SSL': {
                'ssl_cert_reqs': None,  # Disable certificate verification for Heroku's self-signed certs
                'ssl_ca_certs': None,
                'ssl_certfile': None,
                'ssl_keyfile': None,
            }
        },
        'KEY_PREFIX': 'bahk',
        'TIMEOUT': 60 * 15,  # 15 minutes default timeout
    }
}

# Cache middleware settings
CACHE_MIDDLEWARE_SECONDS = 60 * 15  # 15 minutes
CACHE_MIDDLEWARE_KEY_PREFIX = 'bahk'

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'America/Los_Angeles'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'PAGE_SIZE': 10
}

# extend lifetime of JWT refresh tokens
SIMPLE_JWT = {
    'REFRESH_TOKEN_LIFETIME': datetime.timedelta(days=100*365)  # set to expire in 100 years (~forever)
}

# path to backend for authenticating users by email
AUTHENTICATION_BACKENDS = ["hub.auth.EmailBackend"]

# Redirect to home URL after login (Default redirects to /accounts/profile/)
LOGIN_REDIRECT_URL = '/hub/web/'

# Activate Django-Heroku.
import django_heroku
django_heroku.settings(locals())

# AWS S3 settings for production
if IS_PRODUCTION:
    AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', 'us-east-1')  # Default region
    AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com'
    MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/media/'
    DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
else:
    MEDIA_URL = '/media/'
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'


# Helper function to ensure Redis URL has proper scheme
def get_redis_url(url):
    """Ensure Redis URL has proper scheme"""
    if not url:
        return 'redis://redis:6379/1'  # Use 'redis' service name instead of localhost
    if not any(url.startswith(scheme) for scheme in ['redis://', 'rediss://', 'unix://']):
        return f'redis://{url}'
    return url

# Celery Configuration
CELERY_BROKER_URL = get_redis_url(config('REDIS_URL', default='redis://redis:6379/0'))
CELERY_RESULT_BACKEND = get_redis_url(config('REDIS_URL', default='redis://redis:6379/0'))

# Ensure consistent SSL settings for Celery Redis connections
CELERY_BROKER_USE_SSL = {
    'ssl_cert_reqs': None,  # Disable certificate verification for Heroku's self-signed certs
    'ssl_ca_certs': None,
    'ssl_certfile': None,
    'ssl_keyfile': None,
}

CELERY_REDIS_BACKEND_USE_SSL = {
    'ssl_cert_reqs': None,  # Disable certificate verification for Heroku's self-signed certs
    'ssl_ca_certs': None,
    'ssl_certfile': None,
    'ssl_keyfile': None,
}

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'

# Use Redis for session cache
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'

# Cache middleware settings
CACHE_MIDDLEWARE_ALIAS = 'default'

# Mailgun Configuration
EMAIL_HOST = 'smtp.mailgun.org'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'postmaster@' + config('MAILGUN_DOMAIN')
EMAIL_HOST_PASSWORD = config('MAILGUN_API_KEY')
EMAIL_TEST_ADDRESS = config('EMAIL_TEST_ADDRESS', default='test@test.com')
ANYMAIL = {
    "MAILGUN_API_KEY": config('MAILGUN_API_KEY'),
    "MAILGUN_SENDER_DOMAIN": config('MAILGUN_DOMAIN')  
}
EMAIL_BACKEND = "anymail.backends.mailgun.EmailBackend" 
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='fastandprayhelp@gmail.com')

# Test settings
if 'test' in sys.argv:
    MEDIA_ROOT = os.path.join(BASE_DIR, 'test_media')
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
    EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    
    # Use the same database for tests with a test_ prefix
    if 'DATABASE_URL' in os.environ:
        db_config = dj_database_url.config(default=os.environ['DATABASE_URL'])
        DATABASES = {
            'default': {
                **db_config,
                'TEST': {
                    'NAME': f"test_{db_config['NAME']}",
                }
            }
        }

# test if is_production and print something to console
if IS_PRODUCTION:
    print("\033[91m" + "="*50)  # Red color
    print("\033[91m🚀 RUNNING IN PRODUCTION ENVIRONMENT")
    print("\033[91m" + "="*50 + "\033[0m")  # Reset color
else:
    print("\033[93m" + "="*50)  # Yellow color
    print("\033[93m🔧 RUNNING IN DEVELOPMENT ENVIRONMENT")
    print("\033[93m" + "="*50 + "\033[0m")  # Reset color

# handling CORS headers
CORS_ORIGIN_ALLOW_ALL = config('CORS_ORIGIN_ALLOW_ALL', default=False, cast=bool)
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())

# Frontend URL for password reset, if local development or production
FRONTEND_URL = config('FRONTEND_URL', default='https://web.fastandpray.app')

# AWS S3 settings
AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID', default=None)
AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default=None)
AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME', default=None)
AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='us-east-1')

# Define AWS_S3_CUSTOM_DOMAIN if we have the necessary AWS settings
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and AWS_STORAGE_BUCKET_NAME:
    AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com'
else:
    AWS_S3_CUSTOM_DOMAIN = None

# Handle certificates
def download_certificate(filename):
    """Download certificate from S3 or get from local path"""
    try:
        # If we have AWS credentials, try S3 first
        if AWS_S3_CUSTOM_DOMAIN:
            import boto3
            s3 = boto3.client('s3',
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                region_name=AWS_S3_REGION_NAME
            )
            
            # Create certificates directory if it doesn't exist
            cert_dir = os.path.join(BASE_DIR, 'certificates')
            os.makedirs(cert_dir, exist_ok=True)
            
            # Download the file
            cert_path = os.path.join(cert_dir, filename)
            try:
                s3.download_file(
                    AWS_STORAGE_BUCKET_NAME,
                    f'certificates/{filename}',
                    cert_path
                )
                print(f"Successfully downloaded certificate from S3: {filename}")
                return cert_path
            except Exception as e:
                print(f"Failed to download from S3: {str(e)}")
                
        # Try local file as fallback
        local_path = os.path.join(BASE_DIR, 'certificates', filename)
        if os.path.exists(local_path):
            print(f"Using local certificate: {filename}")
            return local_path
        
        print(f"Certificate not found: {filename}")
        return None
                
    except Exception as e:
        print(f"Error handling certificate {filename}: {str(e)}")
        if not DEBUG:
            raise
        return None

# Push Notifications Settings
EXPO_PUSH_SETTINGS = {
    'EXPO_PUSH_URL': 'https://exp.host/--/api/v2/push/send',
    'DEFAULT_SOUND': 'default',
    'DEFAULT_PRIORITY': 'high',
}

""" # APNS Certificate
apns_cert_filename = config('APNS_CERTIFICATE_FILENAME', default='apns_certificate.pem')
if apns_cert_filename:
    apns_cert_path = download_certificate(apns_cert_filename)
    if apns_cert_path:
        EXPO_PUSH_SETTINGS['APNS_CERTIFICATE'] = apns_cert_path
    else:
        print(f"Warning: Failed to load APNS certificate: {apns_cert_filename}")

# FCM Certificate
fcm_cert_filename = config('FCM_CERTIFICATE_FILENAME', default='fcm_certificate.json')
if fcm_cert_filename:
    fcm_cert_path = download_certificate(fcm_cert_filename)
    if fcm_cert_path:
        EXPO_PUSH_SETTINGS['FCM_CREDENTIALS'] = fcm_cert_path
    else:
        print(f"Warning: Failed to load FCM certificate: {fcm_cert_filename}")
 """
# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'level': 'DEBUG' if DEBUG else 'WARNING',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}
