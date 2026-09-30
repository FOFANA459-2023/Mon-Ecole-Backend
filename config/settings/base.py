from datetime import timedelta
from pathlib import Path
from typing import Any

import environ
from corsheaders.defaults import default_headers
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-local-development-key-never-use-in-production")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "apps.core",
    "apps.schools",
    "apps.accounts",
    "apps.audit",
    "apps.people",
    "apps.academics",
    "apps.enrollments",
    "apps.documents",
    "apps.search",
    "apps.dashboard",
    "apps.imports",
    "apps.finance",
    "apps.cashregister",
]

MIDDLEWARE = [
    "apps.core.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
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

if env("DATABASE_URL", default=""):
    DATABASES = {"default": env.db("DATABASE_URL")}
    # Supabase's Supavisor pooler runs in transaction mode: no persistent connections, no server-side cursors.
    DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrUsernameBackend"]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", _("French")), ("en", _("English"))]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES: dict[str, dict[str, Any]] = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "login": env("THROTTLE_LOGIN", default="10/min"),
        "password_reset": env("THROTTLE_PASSWORD_RESET", default="5/hour"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default=SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# The refresh token never reaches JavaScript: it lives in an HttpOnly cookie limited to the auth endpoints.
REFRESH_COOKIE_NAME = "me_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth/"
REFRESH_COOKIE_SECURE = env.bool("REFRESH_COOKIE_SECURE", default=not DEBUG)
REFRESH_COOKIE_SAMESITE = "Lax"
REFRESH_COOKIE_DOMAIN = env("REFRESH_COOKIE_DOMAIN", default=None)

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:5173"])
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = (*default_headers, "x-school-id", "x-request-id")
CORS_EXPOSE_HEADERS = ["x-request-id"]
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# Header carrying the real client IP (e.g. "HTTP_CF_CONNECTING_IP" behind Cloudflare). Empty = REMOTE_ADDR.
CLIENT_IP_META_KEY = env("CLIENT_IP_META_KEY", default="")

REDIS_URL = env("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL or "memory://")
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=not REDIS_URL)
CELERY_TIMEZONE = "UTC"
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Mon École <no-reply@monecole.local>")
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=1025)

# EmailJS (production email). When the keys are empty, Django's EMAIL_BACKEND is used instead.
EMAILJS_SERVICE_ID = env("EMAILJS_SERVICE_ID", default="")
EMAILJS_TEMPLATE_ID = env("EMAILJS_TEMPLATE_ID", default="")
EMAILJS_PUBLIC_KEY = env("EMAILJS_PUBLIC_KEY", default="")
EMAILJS_PRIVATE_KEY = env("EMAILJS_PRIVATE_KEY", default="")

ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=DEBUG)

SPECTACULAR_SETTINGS = {
    "TITLE": "Mon École API",
    "DESCRIPTION": "School management system API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Fixed prefix so operation names and tags are identical whether the docs endpoints are enabled or not.
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "ENUM_NAME_OVERRIDES": {
        "LanguageEnum": "apps.accounts.models.User.Language",
        "GenderEnum": "apps.people.models.Gender",
        "SchoolStatusEnum": "apps.schools.models.School.Status",
        "ArchiveStatusEnum": "apps.people.models.Student.Status",
        # Academic years and cash sessions share these choices (open / closed).
        "OpenClosedStatusEnum": "apps.academics.models.AcademicYear.Status",
        "EnrollmentStatusEnum": "apps.enrollments.models.Enrollment.Status",
        "KindEnum": "apps.enrollments.models.Enrollment.Kind",
        "InvoiceStatusEnum": "apps.finance.models.Invoice.Status",
        "InvoiceSourceEnum": "apps.finance.models.Invoice.Source",
        "DocumentCategoryEnum": "apps.documents.models.Document.Category",
        "FeeCategoryKindEnum": "apps.finance.models.FeeCategory.Kind",
        "DiscountKindEnum": "apps.finance.models.StudentDiscount.Kind",
        "DiscountReasonEnum": "apps.finance.models.StudentDiscount.Reason",
        "FeeAppliesToEnum": "apps.finance.models.FeeSchedule.AppliesTo",
        "PaymentMethodEnum": "apps.finance.models.Payment.Method",
        "PaymentStateEnum": "apps.finance.models.Payment.Status",
        "ExpenseCategoryEnum": "apps.finance.models.Expense.Category",
        "ExpenseStatusEnum": "apps.finance.models.Expense.Status",
        "RefundStatusEnum": "apps.finance.models.Refund.Status",
        "CashDirectionEnum": "apps.cashregister.models.CashMovement.Direction",
        "CashSourceEnum": "apps.cashregister.models.CashMovement.Source",
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_context": {"()": "apps.core.logging.RequestContextFilter"}},
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "filters": ["request_context"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05),
        send_default_pii=False,
    )
