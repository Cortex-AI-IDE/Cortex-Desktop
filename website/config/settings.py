"""
Django settings for the Cortex marketing site + API server.

Production-ready configuration using python-decouple for env vars.
The server is BYOK — it never sees user API keys or LLM prompts.
"""
from pathlib import Path

from decouple import config, Csv

# =============================================================================
# Paths
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# Security — all from .env in production
# =============================================================================
SECRET_KEY = config(
    "DJANGO_SECRET_KEY",
    default="dev-only-secret-key-change-before-deploying-cortex-2026",
)

DEBUG = config("DJANGO_DEBUG", default="True", cast=bool)

ALLOWED_HOSTS = config(
    "DJANGO_ALLOWED_HOSTS",
    default="localhost,127.0.0.1,testserver",
    cast=Csv(),
)

# =============================================================================
# Application definition
# =============================================================================
INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "corsheaders",
    "social_django",

    # Cortex apps
    "cortex",
    "cortex.account",  # Custom user model + account panel
    "api",
    "adminpanel",
]

# =============================================================================
# Custom User Model — MUST be set before first migration
# =============================================================================
AUTH_USER_MODEL = "account.User"

# Auth redirect URLs
LOGIN_URL = "/account/login/"
LOGIN_REDIRECT_URL = "/account/profile/"
LOGOUT_REDIRECT_URL = "/"

# Email — console backend in dev, SMTP in production
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

# SMTP transport — all values from .env in production.
# Namecheap Private Email: mail.privateemail.com, port 465 (SSL) or 587 (TLS).
# Django rule: port 465 -> EMAIL_USE_SSL=True + EMAIL_USE_TLS=False,
#              port 587 -> EMAIL_USE_TLS=True + EMAIL_USE_SSL=False (never both).
EMAIL_HOST = config("EMAIL_HOST", default="mail.privateemail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default="True", cast=bool)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default="False", cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=15, cast=int)

# Brevo HTTP API — sends over HTTPS:443, so it works even where outbound
# SMTP ports (25/465/587) are blocked (e.g. DigitalOcean droplets).
# When BREVO_API_KEY is set, cortex.account.emails posts directly to
# https://api.brevo.com/v3/smtp/email (same approach as CodeVisualizer's
# otp_service.py). When empty, emails fall back to the SMTP backend above.
BREVO_API_KEY = config("BREVO_API_KEY", default="")

# Show/hide the Razorpay payment buttons on the pricing page.
# Set False while the Razorpay account verification is pending — the
# backend endpoints stay live; only the buttons are hidden.
RAZORPAY_ENABLED = config("RAZORPAY_ENABLED", default="True", cast=bool)

# Sender identity for ALL account emails (signup OTP, password-reset OTP).
# Google/GitHub OAuth users never receive these — their email arrives
# pre-verified from the provider.
NOTIFICATION_EMAIL = config("NOTIFICATION_EMAIL", default="notification@cortex-ide.app")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=f"Cortex <{NOTIFICATION_EMAIL}>")

# Email verification gate — requires working SMTP (SendGrid).
# False = signup auto-verifies and logs the user in (no OTP email).
# Flip to True via env once the SendGrid domain is verified.
EMAIL_VERIFICATION_ENABLED = config("EMAIL_VERIFICATION_ENABLED", default="False", cast=bool)

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Must be right after SecurityMiddleware
    "corsheaders.middleware.CorsMiddleware",        # Must be before CommonMiddleware
    "django.middleware.gzip.GZipMiddleware",        # Compress API responses
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# =============================================================================
# Database
# =============================================================================
# SQLite for development. Use DATABASE_URL env var for Postgres in production.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}

# If DATABASE_URL is set (production), use it
DATABASE_URL = config("DATABASE_URL", default=None)
if DATABASE_URL:
    import dj_database_url
    DATABASES["default"] = dj_database_url.parse(DATABASE_URL)
    # Connection pooling for Postgres
    DATABASES["default"]["CONN_MAX_AGE"] = 600  # Keep connections alive 10 min

# =============================================================================
# Cache — in-memory for model config & health checks
# =============================================================================
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cortex-cache",
        "TIMEOUT": 300,  # 5 min default TTL
        "OPTIONS": {
            "MAX_ENTRIES": 500,
        },
    }
}

# =============================================================================
# Auth — Industry-standard password hashing
# =============================================================================
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Password hashing — use Argon2id (industry standard) with bcrypt fallback
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",  # Primary: Argon2id
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",  # Fallback: bcrypt
    "django.contrib.auth.hashers.BCryptPasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",  # Django default
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

# Argon2id parameters (OWASP recommendations)
ARGON2_TIME_COST = 3  # Number of iterations
ARGON2_MEMORY_COST = 65536  # 64MB memory cost
ARGON2_PARALLELISM = 4  # Number of parallel threads

# Session security
SESSION_ENGINE = "django.contrib.sessions.backends.db"  # Database-backed sessions
SESSION_COOKIE_SECURE = not DEBUG  # HTTPS only in production
SESSION_COOKIE_HTTPONLY = True  # No JavaScript access
SESSION_COOKIE_SAMESITE = "Lax"  # CSRF protection
SESSION_COOKIE_AGE = 3600 * 24 * 7  # 1 week
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True  # Refresh session on every request

# CSRF protection
CSRF_COOKIE_SECURE = not DEBUG  # HTTPS only in production
CSRF_COOKIE_HTTPONLY = True  # No JavaScript access
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="http://localhost:8000,http://127.0.0.1:8000,https://cortex-ide.app",
    cast=Csv(),
)

# Secure headers
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"
X_FRAME_OPTIONS = "DENY"

# Rate limiting (using Django's built-in cache)
RATELIMIT_ENABLE = True
RATELIMIT_USE_CACHE = "default"

# =============================================================================
# Internationalization
# =============================================================================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# =============================================================================
# Static files — WhiteNoise in production
# =============================================================================
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# =============================================================================
# Media files — Release .exe uploads (admin panel)
# =============================================================================
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Allow large uploads — Cortex installer is ~280MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 350 * 1024 * 1024  # 350MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 350 * 1024 * 1024  # 350MB

# =============================================================================
# Payment Gateways — PayPal & Razorpay
# =============================================================================

# When DEBUG=True use sandbox/test keys, when DEBUG=False use live keys.
if DEBUG:
    # PayPal Sandbox
    PAYPAL_CLIENT_ID = config("PAYPAL_SANDBOX_CLIENT_ID", default="")
    PAYPAL_CLIENT_SECRET = config("PAYPAL_SANDBOX_CLIENT_SECRET", default="")
    PAYPAL_MODE = config("PAYPAL_MODE", default="sandbox")
    PAYPAL_WEBHOOK_ID = config("PAYPAL_SANDBOX_WEBHOOK_ID", default="")
    PAYPAL_BASE_URL = "https://api-m.sandbox.paypal.com"

    # Razorpay Test
    RAZORPAY_KEY_ID = config("RAZORPAY_TEST_KEY_ID", default="")
    RAZORPAY_KEY_SECRET = config("RAZORPAY_TEST_KEY_SECRET", default="")
    RAZORPAY_WEBHOOK_SECRET = config("RAZORPAY_TEST_WEBHOOK_SECRET", default="")
    RAZORPAY_MODE = "test"
else:
    # PayPal Live
    PAYPAL_CLIENT_ID = config("PAYPAL_LIVE_CLIENT_ID", default="")
    PAYPAL_CLIENT_SECRET = config("PAYPAL_LIVE_CLIENT_SECRET", default="")
    PAYPAL_MODE = config("PAYPAL_MODE", default="live")
    PAYPAL_WEBHOOK_ID = config("PAYPAL_LIVE_WEBHOOK_ID", default="")
    PAYPAL_BASE_URL = "https://api-m.paypal.com"

    # Razorpay Live
    RAZORPAY_KEY_ID = config("RAZORPAY_LIVE_KEY_ID", default="")
    RAZORPAY_KEY_SECRET = config("RAZORPAY_LIVE_KEY_SECRET", default="")
    RAZORPAY_WEBHOOK_SECRET = config("RAZORPAY_LIVE_WEBHOOK_SECRET", default="")
    RAZORPAY_MODE = "live"

# =============================================================================
# Default primary key field type
# =============================================================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# CORS — allow IDE to fetch model config from server
# =============================================================================
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:8000,http://127.0.0.1:8000,http://localhost:8753,http://127.0.0.1:8753,https://cortex-ide.app",
    cast=Csv(),
)

CORS_ALLOW_METHODS = ["GET", "POST", "PATCH", "OPTIONS"]  # GET/POST for API, PATCH for profile updates
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",       # Bearer token auth
    "content-type",
    "if-none-match",       # ETag caching for model config
    "x-requested-with",
]

# =============================================================================
# Cross-Origin-Opener-Policy — MUST be same-origin-allow-popups for PayPal
# Without this, PayPal Smart Buttons open a blank about:blank popup instead
# of the PayPal login/approval window.  (PayPal Dual Window bug fix)
# =============================================================================
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"

# =============================================================================
# Security — production settings (only active when DEBUG=False)
# =============================================================================
if not DEBUG:
    # HTTPS
    SECURE_SSL_REDIRECT = config("DJANGO_SECURE_SSL_REDIRECT", default="True", cast=bool)
    SECURE_HSTS_SECONDS = config("DJANGO_SECURE_HSTS_SECONDS", default=31536000, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = config("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default="True", cast=bool)
    SECURE_HSTS_PRELOAD = config("DJANGO_SECURE_HSTS_PRELOAD", default="True", cast=bool)

    # Cookies
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # Misc
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
else:
    # Dev — no HTTPS redirect, relaxed settings
    SECURE_SSL_REDIRECT = False

# =============================================================================
# Admin — custom URL (obfuscated, not /admin/)
# =============================================================================
ADMIN_URL = config("DJANGO_ADMIN_URL", default="ops/cortex-admin/")

# =============================================================================
# Cortex App Settings
# =============================================================================
TELEMETRY_RATE_LIMIT = config("TELEMETRY_RATE_LIMIT", default=10, cast=int)
TELEMETRY_RETENTION_DAYS = config("TELEMETRY_RETENTION_DAYS", default=90, cast=int)
CORTEX_SERVER_URL = config("CORTEX_SERVER_URL", default="https://cortex-ide.app")

# =============================================================================
# Payment Gateways — PayPal & Razorpay
# Auto-switch: DEBUG=True → sandbox, DEBUG=False → live/production
# =============================================================================

# --- PayPal (USD — global) ---
# PAYPAL_MODE from .env: "sandbox" or "live"
# When DEBUG=True, defaults to sandbox even if .env says live (safety guard)
PAYPAL_MODE = config("PAYPAL_MODE", default="sandbox")
if DEBUG and PAYPAL_MODE != "sandbox":
    PAYPAL_MODE = "sandbox"  # Force sandbox in dev — no accidental live charges

if PAYPAL_MODE == "sandbox":
    PAYPAL_CLIENT_ID = config("PAYPAL_SANDBOX_CLIENT_ID", default="")
    PAYPAL_CLIENT_SECRET = config("PAYPAL_SANDBOX_CLIENT_SECRET", default="")
    PAYPAL_WEBHOOK_ID = config("PAYPAL_SANDBOX_WEBHOOK_ID", default="")
    PAYPAL_BASE_URL = "https://api-m.sandbox.paypal.com"
else:
    PAYPAL_CLIENT_ID = config("PAYPAL_LIVE_CLIENT_ID", default="")
    PAYPAL_CLIENT_SECRET = config("PAYPAL_LIVE_CLIENT_SECRET", default="")
    PAYPAL_WEBHOOK_ID = config("PAYPAL_LIVE_WEBHOOK_ID", default="")
    PAYPAL_BASE_URL = "https://api-m.paypal.com"

# --- Razorpay (INR — India) ---
# Reads TEST keys when DEBUG=True, LIVE keys when DEBUG=False
if DEBUG:
    RAZORPAY_KEY_ID = config("RAZORPAY_TEST_KEY_ID", default="")
    RAZORPAY_KEY_SECRET = config("RAZORPAY_TEST_KEY_SECRET", default="")
    RAZORPAY_WEBHOOK_SECRET = config("RAZORPAY_TEST_WEBHOOK_SECRET", default="")
    RAZORPAY_MODE = "test"
else:
    RAZORPAY_KEY_ID = config("RAZORPAY_LIVE_KEY_ID", default="")
    RAZORPAY_KEY_SECRET = config("RAZORPAY_LIVE_KEY_SECRET", default="")
    RAZORPAY_WEBHOOK_SECRET = config("RAZORPAY_LIVE_WEBHOOK_SECRET", default="")
    RAZORPAY_MODE = "live"

# =============================================================================
# Logging
# =============================================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO" if not DEBUG else "DEBUG",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "api": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        # ── Noisy third-party libraries — WARNING only ──
        # xhtml2pdf logs the ENTIRE rendered invoice HTML (customer name,
        # email, order refs) at DEBUG on every PDF generation — a PII leak
        # into console/log files, plus dozens of tables/col-width lines.
        "xhtml2pdf": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "reportlab": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "PIL": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "urllib3": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "requests": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "social_core": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}


# =============================================================================
# Social Auth — Google + GitHub OAuth2 (social-auth-app-django)
# =============================================================================
AUTHENTICATION_BACKENDS = (
    "social_core.backends.google.GoogleOAuth2",
    "social_core.backends.github.GithubOAuth2",
    "django.contrib.auth.backends.ModelBackend",
)

SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = config("GOOGLE_OAUTH2_KEY", default="")
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = config("GOOGLE_OAUTH2_SECRET", default="")
SOCIAL_AUTH_GITHUB_KEY = config("GITHUB_OAUTH_KEY", default="")
SOCIAL_AUTH_GITHUB_SECRET = config("GITHUB_OAUTH_SECRET", default="")

SOCIAL_AUTH_PIPELINE = (
    "social_core.pipeline.social_auth.social_details",
    "social_core.pipeline.social_auth.social_uid",
    "social_core.pipeline.social_auth.auth_allowed",
    "social_core.pipeline.social_auth.social_user",
    "cortex.account.pipeline.mark_email_verified",
    "social_core.pipeline.user.get_username",
    "social_core.pipeline.user.create_user",
    "social_core.pipeline.social_auth.associate_user",
    "social_core.pipeline.social_auth.load_extra_data",
    "social_core.pipeline.user.user_details",
)

SOCIAL_AUTH_LOGIN_REDIRECT_URL = "/account/profile/"
SOCIAL_AUTH_NEW_USER_REDIRECT_URL = "/account/profile/"
SOCIAL_AUTH_URL_NAMESPACE = "social"

SOCIAL_AUTH_GOOGLE_OAUTH2_SCOPE = ["email", "profile"]
SOCIAL_AUTH_GITHUB_SCOPE = ["user:email"]

# social-auth-app-django JSON field fix
SOCIAL_AUTH_JSONFIELD_ENABLED = True
