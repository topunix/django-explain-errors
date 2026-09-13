"""Settings for the eval harness fixture app: a small, deliberately breakable
Django blog. Not a real project -- exists only so the harness has realistic
tracebacks to feed to the middleware.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = "eval-harness-fixture-app-not-for-production"
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

# The harness drives many fixtures in quick succession; the sliding-window
# throttle must never fire mid-run.
EXPLAIN_ERRORS_MAX_CALLS = 10_000
EXPLAIN_ERRORS_WINDOW_SECONDS = 60

# JSON 500 body carries the explanation text; see docs/tasks/eval-harness.md
# section 3. Overridden per-pass by run.py, kept here as the on-disk default
# so the fixture app is also usable interactively (`manage.py runserver`).
EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE = False

# RAG is off by default; run.py flips this on for the RAG-on pass and points
# EXPLAIN_ERRORS_RAG_INCLUDE at this directory before building the index.
EXPLAIN_ERRORS_RAG_ENABLED = False
