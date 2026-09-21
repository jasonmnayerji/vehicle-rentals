"""Django settings. All environment-specific values come from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.postgresql.psycopg_any import IsolationLevel

BASE_DIR = Path(__file__).resolve().parent.parent


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(f"environment variable {name} is required")
    return value


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _milliseconds(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 0:
        raise ImproperlyConfigured(f"environment variable {name} cannot be negative")
    return value


def _one_of(name: str, allowed: tuple[str, ...], default: str) -> str:
    value = os.environ.get(name, default).strip().lower()
    if value not in allowed:
        raise ImproperlyConfigured(
            f"environment variable {name} must be one of {', '.join(allowed)}; got {value!r}"
        )
    return value


# Which kind of deployment this is. Gates conveniences that must never exist outside a
# developer's machine (the test superuser). Production unless stated otherwise, so that
# forgetting to set it is the safe mistake; a typo stops start-up instead of guessing.
ENVIRONMENT = _one_of("DJANGO_ENV", allowed=("dev", "production"), default="production")

# No default: a missing secret must fail loudly at start-up, not fall back to a known value.
SECRET_KEY = _required("DJANGO_SECRET_KEY")
DEBUG = _flag("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = _csv("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _csv("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "rentals",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "config.request_id.RequestIdMiddleware",
    "config.access_log.AccessLogMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _required("POSTGRES_DB"),
        "USER": _required("POSTGRES_USER"),
        "PASSWORD": _required("POSTGRES_PASSWORD"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            # Pinned, not inherited. PostgresBookingLedger is only safe at READ COMMITTED: its
            # availability read must take a fresh snapshot *after* the row lock is granted.
            # At REPEATABLE READ the snapshot predates the lock, the read misses the previous
            # holder's insert, and the fleet overbooks with the lock still in place. A server
            # or role default must never be able to change this silently.
            "isolation_level": IsolationLevel.READ_COMMITTED,
            # Bounded waits: a stuck lock holder or a runaway query fails this request
            # instead of tying up a worker for ever. 0 disables a limit.
            "options": (
                f"-c lock_timeout={_milliseconds('POSTGRES_LOCK_TIMEOUT_MS', 5_000)} "
                f"-c statement_timeout={_milliseconds('POSTGRES_STATEMENT_TIMEOUT_MS', 15_000)}"
            ),
        },
        # Transactions are opened explicitly by PostgresBookingLedger, where the lock lives.
        # Wrapping every request in one would hold row locks for longer than necessary.
        "ATOMIC_REQUESTS": False,
    }
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
# The zone the web UI shows times in, and reads its zone-less datetime-local inputs in.
# Booking rules themselves work on absolute instants and never depend on this.
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -- security ------------------------------------------------------------------------------
# TLS is terminated by whatever sits in front of the container; these switch on with it.
_BEHIND_TLS = _flag("DJANGO_BEHIND_TLS", default=False)
SESSION_COOKIE_SECURE = _BEHIND_TLS
CSRF_COOKIE_SECURE = _BEHIND_TLS
SECURE_SSL_REDIRECT = _BEHIND_TLS
SECURE_HSTS_SECONDS = 31_536_000 if _BEHIND_TLS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = _BEHIND_TLS
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if _BEHIND_TLS else None
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

# -- logging -------------------------------------------------------------------------------
# One JSON object per line on stdout: the container runtime collects it, nothing is written
# to disk, and every line carries the request id so one request can be followed end to end.
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "config.request_id.RequestIdFilter"}},
    "formatters": {"json": {"()": "config.log_format.JsonFormatter"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    # Application loggers (rentals.*, config.*) carry no handlers of their own and propagate
    # here, so there is exactly one place that decides where log lines go.
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL},
    "loggers": {
        # Replaces Django's default handlers (console-only-in-DEBUG and mail_admins).
        # Unhandled exceptions in views arrive via django.request at ERROR with a traceback.
        "django": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}
