# Standard Library
from importlib.util import find_spec

# Minimal Django settings for running management commands / tests locally.
# Not used in production — the Alliance Auth install has its own settings.
#
# The values below exist so `manage.py test` can boot Alliance Auth 5.6's
# system checks. They are placeholders. Nothing here is a real credential,
# and every URL is example.com.

import os

from celery import current_app

SECRET_KEY = "dev-only-not-for-production"
DEBUG = True

INSTALLED_APPS = [
    "allianceauth",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_celery_beat",
    "solo",
    "django_bootstrap5",
    "sortedm2m",
    "esi",
    "allianceauth.framework",
    "allianceauth.admin_status",
    "allianceauth.authentication",
    "allianceauth.services",
    "allianceauth.eveonline",
    "allianceauth.groupmanagement",
    "allianceauth.notifications",
    "allianceauth.thirdparty.navhelper",
    "allianceauth.analytics",
    "allianceauth.menu",
    "allianceauth.theme",
    "allianceauth.theme.darkly",
    "allianceauth.theme.flatly",
    "allianceauth.theme.materia",
    "allianceauth.custom_css",
    "allianceauth.crontab",
    "sri",
    "aa_pilotstats",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Real Alliance Auth sites run MariaDB or MySQL. Set PILOTSTATS_TEST_DB=mysql
# (CI does, with a MariaDB service) to run the tests there instead of SQLite.
# The defaults match a throwaway local server; nothing here is a real login.
if os.environ.get("PILOTSTATS_TEST_DB") == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("PILOTSTATS_DB_NAME", "pilotstats"),
            "USER": os.environ.get("PILOTSTATS_DB_USER", "root"),
            "PASSWORD": os.environ.get("PILOTSTATS_DB_PASSWORD", "example-test-password"),
            "HOST": os.environ.get("PILOTSTATS_DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("PILOTSTATS_DB_PORT", "3306"),
            # What Alliance Auth's install guide sets up.
            "OPTIONS": {"charset": "utf8mb4"},
            "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# allianceauth.authentication's task_statistics hook up a redis-backed event
# counter at app-ready() time regardless of whether Celery is in use — point
# it at a redis:// URL so django_redis can build a (lazy) client without
# needing a real Redis server for `manage.py check` / `test` to work.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://localhost:6379/0",
    }
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "allianceauth.context_processors.auth_settings",
            ],
        },
    }
]

STATIC_URL = "/static/"
# Auth's base template hashes static files with django-sri, which asks the
# storage for a filesystem path before it falls back to the app finders.
STATIC_ROOT = "/tmp/pilotstats-static"
ROOT_URLCONF = "urls_dev"

SITE_NAME = "example"
SITE_URL = "https://example.com"
CSRF_TRUSTED_ORIGINS = ["https://example.com"]
ALLOWED_HOSTS = ["*"]

DEFAULT_THEME = "allianceauth.theme.flatly.auth_hooks.FlatlyThemeHook"
LOGIN_TOKEN_SCOPES = ["publicData"]

# Tests share one Redis, so a cached leaderboard could leak from one test
# into the next. Caching is off here; test_leaderboard_cache turns it on
# with a private in-memory cache.
PILOTSTATS_LEADERBOARD_CACHE_SECONDS = 0

# Placeholder SSO settings so the esi system check can pass. Not real credentials.
ESI_SSO_CLIENT_ID = "example-client-id"
ESI_SSO_CLIENT_SECRET = "example-client-secret"
ESI_SSO_CALLBACK_URL = "https://example.com/sso/callback"
ESI_USER_CONTACT_EMAIL = "maintainer@example.com"

# Alliance Auth's celery check reads the default Celery app, which is only
# configured when a project's celery module is imported. Set the same values
# the Auth project template sets, so `manage.py test` does not fail the check.
current_app.conf.broker_connection_retry_on_startup = True
current_app.conf.broker_transport_options = {
    "priority_steps": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    "queue_order_strategy": "priority",
}

# AA Desktop's bridge is optional and not on PyPI yet. When it is installed
# (as in aa-desktop's CI), turn it on so aa_pilotstats/tests/test_desktop.py
# runs; otherwise those tests skip and nothing here changes.
if find_spec("aa_desktop_bridge") is not None:
    INSTALLED_APPS += ["oauth2_provider", "aa_desktop_bridge"]
    OAUTH2_PROVIDER = {
        "PKCE_REQUIRED": True,
        "SCOPES": {
            "openid": "OpenID Connect",
            "desktop.core": "AA Desktop",
            "desktop.pilotstats.upload": "Upload combat logs from AA Desktop",
        },
    }
