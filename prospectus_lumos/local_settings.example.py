"""Template for ``prospectus_lumos/local_settings.py``.

``local_settings.py`` is gitignored and is the single override point for every
environment: it is imported at the end of ``settings.py``, so anything defined
here wins over the defaults. Copy this file to ``local_settings.py`` and fill in
the values for the machine you are configuring.

Development needs nothing beyond a database entry — the defaults in
``settings.py`` cover the rest. Production must set every value in the
"Production" section below.
"""

# --- Required everywhere -----------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": "prospectus_lumos",
        "USER": "postgres",
        "PASSWORD": "password",
        "HOST": "127.0.0.1",
        "PORT": "5432",
    }
}

# --- Production --------------------------------------------------------------
# Uncomment and set all of these on a deployed host. Booting with DEBUG = False
# while SECRET_KEY is still the published development key raises
# ImproperlyConfigured.

# Generate with:
#   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# SECRET_KEY = 'replace-me-with-a-unique-50-character-random-string'

# DEBUG = False
# ALLOWED_HOSTS = ['prospectus-lumos.example.com']

# Served over HTTPS only.
# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True

# --- Integrations ------------------------------------------------------------

# REDIS_PASSWORD = ''
# CACHES = {
#     'default': {
#         'BACKEND': 'django_redis.cache.RedisCache',
#         'LOCATION': f'redis://:{REDIS_PASSWORD}@127.0.0.1:6379/1',
#         'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
#     }
# }

# Path to the Google service account JSON used for Drive and Sheets access.
# GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE = 'service-account.json'
# GOOGLE_SHEET_API_KEY = ''

# GEMINI_API_KEY = ''

# Distinguishes this deployment's session cookie from other local Django apps.
# SESSION_COOKIE_NAME = 'prospectuslumos'
