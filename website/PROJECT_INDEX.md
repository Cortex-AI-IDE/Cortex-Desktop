# Cortex Django — Project Index

_Last regenerated: 2026-07-06_

## Overview

**Cortex Django** is the backend server for the **Cortex AI IDE** — an agentic AI coding IDE for Windows. It serves a marketing site, user account panel, REST API for the IDE desktop client, and dual payment gateways (PayPal + Razorpay).

**BYOK architecture** — all LLM inference uses the user's own API keys (never touch the server). The subscription covers platform services only: Mistral OCR, SiliconFlow embeddings, and SerpAPI web search.

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Python source files | 20 (excluding migrations/`__init__.py`) |
| Django apps | 3 (`api`, `cortex`, `cortex.account`) |
| Core models | 10 across 2 apps |
| API endpoints | 20+ |
| Payment endpoints | 11 |
| HTML templates | 20+ |
| CSS/JS files | 2 CSS + 2 JS |
| Migrations | 8 (`api`) + 1 (`cortex/account`) |
| Key files (lines) | models.py (589), payment_views.py (960), views.py (663), auth_views.py (394), settings.py (378) |
| Dependencies | 10+ (Django, Gunicorn, WhiteNoise, CORS, Argon2, Pillow, Razorpay, Requests…) |

---

## Architecture

```
Cortex_djnago/
├── api/                        # REST API app
│   ├── models.py               # ModelConfig, Subscription, UsageLog, CrashReport, Payment, AuthToken
│   ├── views.py                # Config, license, crash, proxy (OCR/embeddings/search), health, billing
│   ├── auth_views.py           # OAuth2 device auth (login, authorize, callback, refresh, me)
│   ├── payment_views.py        # PayPal + Razorpay integration (create, capture/verify, webhook, activate)
│   ├── payment_urls.py         # Payment URL routing (11 endpoints)
│   ├── paypal_utils.py         # PayPal API wrapper (OAuth, orders, webhook verification)
│   ├── billing_engine.py       # CoreBillingEngine — plan info
│   ├── decorators.py           # token_required + optional_token
│   ├── urls.py                 # API URL routing
│   ├── admin.py                # Django admin for Subscription, CrashReport
│   └── management/commands/    # cleanup_crash_reports.py
│
├── cortex/                     # Public website app
│   ├── views.py                # 6 TemplateViews (Index, Pricing, Download, Privacy, Terms, EULA)
│   ├── urls.py                 # Marketing site URL routing
│   ├── account/                # Account management sub-app
│   │   ├── models.py           # User (AUTH_USER_MODEL), ActiveSession, ApiKey, Order
│   │   ├── views.py            # Auth (login, signup, logout, password reset) + account panel (profile, usage, plan, integrations, security, pricing)
│   │   ├── forms.py            # LoginForm, SignupForm, ProfileForm
│   │   ├── decorators.py       # Custom login_required redirecting to /account/login/
│   │   └── urls.py             # 15 URL routes
│   ├── templates/cortex/       # 20+ HTML templates (base, index, pricing, account/*, payment/*)
│   └── static/cortex/          # CSS (styles.css, account.css), JS (main.js, account.js), img
│
└── config/                     # Django project config
    ├── settings.py             # 378 lines (DB, auth, sessions, CORS, payments, logging)
    ├── urls.py                 # Root URL routing — 6 route groups
    ├── wsgi.py                 # WSGI entry (Gunicorn)
    └── asgi.py                 # ASGI entry
```

_Note: The `codevisualizer/` directory exists alongside but is a separate reference project (CodeVisualizer learning platform). Not part of Cortex._

---

## Data Models

### api app (`api/models.py` — 589 lines)

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **ModelConfig** | Remote model config served to IDE on startup | `version`, `config_json`, `min_ide_version`, `is_active` |
| **Subscription** | User subscription — Pro $10/mo or Pro Yearly $80/yr | `license_key`, `email`, `plan`, `status`, `current_period_end`, `paypal_order_id`, `razorpay_order_id` |
| **UsageLog** | Per-request usage tracking (OCR, embeddings, web search) | `subscription`, `model_id`, `input_cache_hit`, `input_cache_miss`, `output_tokens`, `ocr_pages`, `raw_cost_usd`, `gross_cost_usd`, `credits_consumed` |
| **CrashReport** | Opt-in crash telemetry from IDE | `device_hash` (SHA-256), `ide_version`, `os_version`, `error_type`, `error_message`, `stack_trace` |
| **Payment** | Unified payment records (PayPal + Razorpay) | `user`, `plan`, `amount`, `currency`, `gateway`, `status`, `paypal_order_id`, `razorpay_order_id`, `razorpay_signature` |
| **AuthToken** | OAuth2 tokens for Desktop IDE auth | `user`, `access_token`, `refresh_token`, `expires_at`, `device_info`, `is_active` |

### cortex.account app (`cortex/account/models.py` — 265 lines)

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **User** | Custom AUTH_USER_MODEL (extends AbstractUser) | `avatar`, `display_name`, `timezone`, `marketing_opt_in` |
| **ActiveSession** | Track sessions (Web, IDE, CLI, JetBrains) | `user`, `client_type`, `ip_address`, `location`, `user_agent`, `is_current` |
| **ApiKey** | User API keys (SHA-256 hashed, raw key shown once) | `user`, `name`, `key_hash`, `key_prefix`, `scopes`, `expires_at` |
| **Order** | Billing order history | `user`, `stripe_payment_intent_id`, `amount_usd`, `currency`, `status`, `item`, `order_type` |

---

## Payment Gateways

| Gateway | Currency | Target | Order Creation | Verification |
|---------|----------|--------|---------------|--------------|
| **PayPal** | USD | Global | Client-side JS SDK → server records | Capture status + server-side amount validation |
| **Razorpay** | INR | India | Server-side `razorpay.Client.order.create()` | HMAC-SHA256 signature verification |

### Pricing (Server-Side Source of Truth)

| Plan | USD | INR | Billing |
|------|-----|-----|---------|
| Pro | $10.00/mo | ₹899.00/mo | Monthly |
| Pro Yearly | $80.00/yr | ₹6,999.00/yr | Annual (33% savings) |

### Dual-Layer Verification
```
Layer 1 (Primary):  JS callback → /verify endpoint → activate subscription
Layer 2 (Backup):   Gateway webhook → /webhook endpoint → activate (idempotent)
```

### Security
- **Server-side amount validation** — amounts NEVER trusted from frontend; validated against backend `PLAN_PRICES_USD`/`PLAN_PRICES_INR`
- **HMAC-SHA256** for Razorpay verification
- **Webhook signature verification** for PayPal (production)
- **Pending dedup** — blocks rapid retries within 10 minutes
- **Idempotent activation** — safe to call `_activate_subscription()` multiple times
- **Amount tamper detection** — logs `logger.critical()` alerts

---

## Authentication

Two systems coexist:

| System | Use Case | Token Type |
|--------|----------|------------|
| **Django Session** | Web browser (marketing, account panel) | Session cookie |
| **OAuth2 Bearer** | Desktop IDE (API) | `Authorization: Bearer cortex_at_...` |

### Desktop Auth Flow (Device Auth)
```
Desktop → GET /api/v1/auth/login/ → auth_url → browser opens
User logs in → /api/v1/auth/authorize/ → "Authorize Device?" page
User clicks Authorize → auth_code → redirects to localhost callback
Desktop → POST /api/v1/auth/callback/ { code } → receives access_token + refresh_token
```

---

## Complete URL Map

### Marketing Site
| Path | Name | Auth |
|------|------|:----:|
| `/` | `cortex:index` | No |
| `/pricing/` | `cortex:pricing` | No |
| `/download/` | `cortex:download` | No |
| `/privacy/` | `cortex:privacy` | No |
| `/terms/` | `cortex:terms` | No |
| `/license/` | `cortex:eula` | No |

### Account Panel
| Path | Name | Auth |
|------|------|:----:|
| `/account/` | redirect→profile | Yes |
| `/account/login/` | `account:login` | No |
| `/account/signup/` | `account:signup` | No |
| `/account/logout/` | `account:logout` | No |
| `/account/password-reset/` | `account:password-reset` | No |
| `/account/password-reset-confirm/` | `account:password-reset-confirm` | No |
| `/account/password-reset-complete/` | `account:password-reset-complete` | No |
| `/account/profile/` | `account:profile` | Yes |
| `/account/usage/` | `account:usage` | Yes |
| `/account/plan/` | `account:plan` | Yes |
| `/account/integrations/` | `account:integrations` | Yes |
| `/account/pricing/` | `account:pricing` | Yes |
| `/account/security/` | `account:security` | Yes |

### API — IDE
| Path | Method | Auth | Purpose |
|------|--------|:----:|---------|
| `/api/v1/models/config/` | GET | No | IDE fetches model config |
| `/api/v1/subscription/validate/` | POST | No | Validate license key |
| `/api/v1/proxy/chat/` | POST | License | Proxy OCR/embeddings/search |
| `/api/v1/telemetry/crash/` | POST | No | Crash report ingestion |
| `/api/v1/auth/login/` | GET | No | Get OAuth auth URL |
| `/api/v1/auth/authorize/` | GET/POST | Session | Device authorization page |
| `/api/v1/auth/callback/` | POST | No | Exchange code for tokens |
| `/api/v1/auth/login/credentials/` | POST | No | Direct email+password login |
| `/api/v1/auth/refresh/` | POST | No | Refresh access token |
| `/api/v1/auth/logout/` | POST | Token | Revoke tokens |
| `/api/v1/auth/me/` | GET | Token | Current user info |
| `/api/v1/profile/` | GET/PATCH | Token | User profile |
| `/api/v1/usage/summary/` | GET | Token | Usage summary |
| `/api/v1/usage/sync/` | POST | Token | Sync usage from desktop |
| `/api/v1/billing/subscription/` | GET | Token | Current subscription |
| `/api/v1/billing/history/` | GET | Token | Payment history |
| `/ops/health/` | GET | No | Health check |

### Payment
| Path | Method | Auth | Purpose |
|------|--------|:----:|---------|
| `/payment/paypal/create-order/` | POST | Session | Record PayPal order |
| `/payment/paypal/capture-order/` | POST | Session | Verify capture + activate |
| `/payment/paypal/webhook/` | POST | No | Backup notification |
| `/payment/razorpay/create-order/` | POST | Session | Create Razorpay order |
| `/payment/razorpay/verify/` | POST | Session | Verify signature + activate |
| `/payment/razorpay/webhook/` | POST | No | Backup notification |
| `/payment/success/` | GET | Session | Success page |
| `/payment/cancel/` | GET | Session | Cancel page |
| `/payment/failed/` | GET | Session | Failed page |
| `/payment/cancel-pending/` | POST | Session | Cancel stuck payment |
| `/payment/subscription/status/` | GET | Session | JSON status |

---

## Settings Highlights (`config/settings.py` — 378 lines)

| Section | Key Settings |
|---------|-------------|
| **Auth** | `AUTH_USER_MODEL = "account.User"`, Argon2id primary hasher, 12-char min password |
| **Sessions** | Database-backed, 1 week age, HTTP-only, SameSite=Lax |
| **CORS** | `corsheaders`, Authorization + ETag headers allowed |
| **Static** | WhiteNoise `CompressedManifestStaticFilesStorage` |
| **Database** | SQLite (dev), PostgreSQL via `DATABASE_URL` (prod), WAL mode |
| **Payments** | Auto-switch sandbox/live by `DEBUG` flag |
| **Security** | HSTS (prod), X-Frame-Options DENY, COOP for PayPal popups |
| **Logging** | Console handler, WARNING for Django, DEBUG for api app |
| **Admin** | Custom URL via `DJANGO_ADMIN_URL` (default: `ops/cortex-admin/`) |

## Dependencies (`requirements.txt`)

| Package | Version | Purpose |
|---------|---------|---------|
| Django | >=5.1,<5.2 | Web framework |
| gunicorn | >=22.0 | Production WSGI |
| whitenoise | >=6.7 | Static files |
| python-decouple | >=3.8 | .env config |
| django-cors-headers | >=4.4 | CORS |
| argon2-cffi | >=25.1 | Password hashing |
| Pillow | >=12.3 | Avatar images |
| python-dateutil | >=2.9 | Date utilities |
| requests | >=2.31 | HTTP client |
| razorpay | >=1.0 | Razorpay gateway |

---

## Key Design Decisions

1. **BYOK** — LLM inference uses user's own API keys (never touch server). Subscription covers only platform services (OCR, embeddings, web search).
2. **No credit system** — Pro plan is a flat fee. `CreditBalance` model was removed. `billing_credits_view()` returns zero for backward compat.
3. **Two plans only** — Pro ($10/mo) and Pro Yearly ($80/yr). No free tier. Signup doesn't auto-provision a subscription.
4. **Dual payment gateways** — PayPal (USD global) + Razorpay (INR India). Dual-layer verification (JS callback + webhook backup).
5. **OAuth2 device auth** — Desktop IDE uses OAuth2 auth code flow. Tokens expire after 30 days, refreshable.
6. **Public vs internal pricing** — `/pricing/` shows prices only (no payment buttons). `/account/pricing/` has actual PayPal/Razorpay integration behind auth.
7. **Admin URL obfuscation** — Django admin at configurable path via `DJANGO_ADMIN_URL`, not `/admin/`.

## Deployment

- **Platform**: Heroku (Procfile + runtime.txt)
- **Server**: Gunicorn (WSGI)
- **Static**: WhiteNoise (no Nginx/CDN needed)
- **Database**: SQLite (dev) / PostgreSQL (prod via `DATABASE_URL`)

## What's Not Yet Implemented

- Email verification on signup
- Google/GitHub OAuth login
- 2FA (TOTP/WebAuthn)
- Rate limiting on auth endpoints
- Avatar upload processing
- Stripe integration
- Auto-renewal billing
- BYOK provider configuration UI
- Credit pack purchases
