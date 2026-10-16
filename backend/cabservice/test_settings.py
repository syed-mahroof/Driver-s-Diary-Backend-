"""Isolated SQLite settings for local verification; never use the live database."""
import os
from .settings import *  # noqa: F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ.get('DIARY_TEST_DATABASE', ':memory:')}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CORS_ALLOWED_ORIGINS = ['http://localhost:5174', 'http://127.0.0.1:5174']
