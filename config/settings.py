"""
Django settings for the petty-cash expense & claims tracker.

Kept intentionally simple for the demo:
  - SQLite (zero setup); swap DATABASES for PostgreSQL in production.
  - Synchronous AI receipt processing; swap in Celery for production.
Secrets and tunables are read from a .env file (see .env.example).
"""
from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from the project root if present.
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Core -------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-dev-only-key")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Local apps
    "accounts",
    "claims",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "claims.context_processors.policy",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---------------------------------------------------------------
# SQLite for the demo. For production, replace with:
#   DATABASES = {"default": dj_database_url.config(...)}  # PostgreSQL
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# --- Auth -------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "home"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- I18N -------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dubai"
USE_I18N = True
USE_TZ = True

# --- Static & media ---------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Branding ---------------------------------------------------------------
SITE_NAME = os.getenv("SITE_NAME", "ClaimFlow")
SITE_TAGLINE = os.getenv("SITE_TAGLINE", "Automated expense & claims management")
# Absolute base URL used to build links inside notification emails.
SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:8010")

# --- Email / notifications --------------------------------------------------
# Default: print emails to the console so notifications work with zero setup.
# To send real email, set EMAIL_HOST/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD in .env
# (e.g. Gmail SMTP) and the SMTP backend switches on automatically.
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", f"{SITE_NAME} <no-reply@claimflow.app>"
)
# Master switch for outbound notifications.
NOTIFICATIONS_ENABLED = env_bool("NOTIFICATIONS_ENABLED", True)

# --- AI receipt extraction --------------------------------------------------
AI_PROVIDER = os.getenv("AI_PROVIDER", "mock").lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")

# --- Expense policy ---------------------------------------------------------
CURRENCY = os.getenv("CURRENCY", "AED")
AUTO_APPROVE_THRESHOLD = float(os.getenv("AUTO_APPROVE_THRESHOLD", "50"))
HIGH_VALUE_THRESHOLD = float(os.getenv("HIGH_VALUE_THRESHOLD", "500"))
# Claims at or above this amount need Finance Manager sign-off after the branch
# manager; claims below it skip Finance Manager and go straight to Accounts.
FINANCE_REVIEW_THRESHOLD = float(os.getenv("FINANCE_REVIEW_THRESHOLD", "50"))
MAX_RECEIPT_AGE_DAYS = int(os.getenv("MAX_RECEIPT_AGE_DAYS", "30"))
# Per-category single-claim ceilings (used by the AI validation layer).
CATEGORY_LIMITS = {
    "TAXI": 100,
    "FOOD": 150,
    "SUPPLIES": 300,
    "FUEL": 250,
    "OTHER": 200,
}
