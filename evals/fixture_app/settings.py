"""Settings for the fixture blog."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = "fixture-blog-insecure-dev-key"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "explain_errors",
    "blog",
]

MIDDLEWARE = [
    "explain_errors.middleware.ExplainErrorsMiddleware",
]

ROOT_URLCONF = "urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "eval_fixture_app.sqlite3"),
    }
}

USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EXPLAIN_ERRORS_MAX_CALLS = 10_000
EXPLAIN_ERRORS_WINDOW_SECONDS = 60

EXPLAIN_ERRORS_DEDUP = False

EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE = False

EXPLAIN_ERRORS_RAG_ENABLED = False
