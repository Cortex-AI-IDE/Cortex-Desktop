"""
Cortex API Client, HTTP client for Django backend.

Handles all communication between Desktop IDE and Django server:
- Authentication (OAuth2 token management)
- Profile CRUD
- Usage sync
- Billing/subscription queries

Falls back to local data when offline.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# get_logger(), not logging.getLogger(): the bare call attaches no
# handler, so every line this module logged went nowhere. A browser
# sign-in that failed its code exchange left "Login Successful" in the
# browser, a spinning dialog, and NOT ONE line in cortex.log to say why.
try:
    from src.utils.logger import get_logger
    log = get_logger("cortex_api")
except Exception:
    log = logging.getLogger("cortex_api")

# App version, sent as X-Cortex-Version on every API request so the server
# can track which IDE version each user runs (admin panel "App Versions in
# Use"). Read from src/version.py: this used to be a hand-maintained literal
# and sat at "2.8.5" for four releases, so the admin panel believed every
# user was on 2.8.5 no matter what they had installed.
from src.version import VERSION as APP_VERSION

# Sent as X-Cortex-Platform so the server can answer for this OS, e.g.
# /ops/health/ reports the latest version for windows or linux.
import platform as _platform
_system = _platform.system().lower()
APP_PLATFORM = {"darwin": "macos"}.get(_system, _system)

# Try to import httpx, fall back to requests
try:
    import httpx
    _HTTP_CLIENT = "httpx"
except ImportError:
    try:
        import requests
        _HTTP_CLIENT = "requests"
    except ImportError:
        _HTTP_CLIENT = None
        log.warning("[CortexAPI] No HTTP client available (install httpx or requests)")


# ── Secret-file permissions ────────────────────────────────────────────────
# ~/.cortex holds live 30-day bearer tokens (auth.json), the encrypted
# provider-key vault (keys.enc) and cortex.db. auth.json used to be written
# with Path.write_text(), which creates it 0664 under the common umask 0002,
# so every other local user on a shared machine could read the token and
# impersonate the account for its full lifetime. keys.enc already got this
# right (key_manager._restrict_perms); auth.json was the one secret left out.
#
# os.open(mode=0o600) creates the file already owner-only -- chmod *after*
# write_text() would leave a window where the tokens sit world-readable --
# and the follow-up chmod heals files an older build left at 0664, because
# O_CREAT's mode is ignored when the file already exists.
#
# On Windows os.chmod only toggles the read-only bit and grants no real ACL
# protection, but it is harmless: each user's profile directory is already
# isolated by NTFS ACLs, so the exposure closed here is POSIX-specific.
_CORTEX_DIR_MODE = 0o700
_SECRET_FILE_MODE = 0o600


def _restrict_existing(path: Path) -> None:
    """chmod an existing secret file to owner-only, ignoring failures.

    Mirrors key_manager._restrict_perms. A chmod that fails must not abort a
    sign-in whose tokens were already written, and must not spam the log on
    every launch, so this stays quiet at debug level.
    """
    try:
        os.chmod(path, _SECRET_FILE_MODE)
    except OSError as e:
        log.debug(f"[CortexAPI] Could not restrict {path}: {e}")


def _restrict_dir(path: Path) -> None:
    """chmod an existing directory to owner-only, ignoring failures.

    Best-effort on purpose: the token file itself is still created 0600, so a
    directory we cannot tighten is a lost defence-in-depth layer, not a leak
    of the tokens -- and it must never mask the real reason a save failed.
    """
    try:
        os.chmod(path, _CORTEX_DIR_MODE)
    except OSError as e:
        log.debug(f"[CortexAPI] Could not restrict {path}: {e}")


def _harden_secret_dir(path: Path) -> None:
    """Best-effort create-and-tighten, for READ paths only.

    Never raises: _load_tokens() runs on every client construction and a
    permission hiccup there must not log the user out. Write paths must not
    use this -- they need mkdir failures to propagate so the user is told the
    true cause (a full disk, not a missing path).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.debug(f"[CortexAPI] Could not create {path}: {e}")
        return
    _restrict_dir(path)


def _write_secret_text(path: Path, text: str) -> None:
    """Write `text` to `path`, creating it owner-only with no world-readable
    window, then heal a pre-existing loose file.

    Raises OSError when the directory cannot be created or the write fails, so
    _save_tokens can keep reporting the real cause to the user. Only the chmod
    calls are best-effort.
    """
    # Deliberately NOT wrapped: when this fails the session genuinely cannot
    # be persisted, and swallowing it would replace "No space left on device"
    # with a misleading "No such file or directory" from the os.open below.
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SECRET_FILE_MODE)
    try:
        fh = os.fdopen(fd, "w", encoding="utf-8")
    except Exception:
        # fdopen failed, so it never took ownership of the descriptor.
        os.close(fd)
        raise
    with fh:
        fh.write(text)
    _restrict_existing(path)


class CortexAPIClient:
    """HTTP client for Cortex Django backend.

    Features:
    - Connection pooling via httpx persistent client
    - Automatic token refresh on 401
    - ETag caching for model config
    - Graceful offline fallback
    """

    def __init__(self, base_url: str = "https://cortex-ide.app"):
        self.base_url = base_url.rstrip("/")
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: Optional[str] = None
        self.user_info: Optional[Dict] = None
        self._lock = threading.Lock()
        # Serialises every request made on the single shared httpx.Client
        # created below. Three threads use that client at once: the
        # semantic-search background indexer (`semantic-bg-ind`), the
        # `cortex-account-refresh` worker and the agent/main thread. httpx is
        # nominally thread-safe, but under Python 3.14.4 + OpenSSL 3.5.5 the
        # concurrent TLS handshakes segfault in native code -- 9 identical
        # crashes in ~/.cortex/crash.log, every one faulting in
        # ssl.do_handshake on the `semantic-bg-ind` thread at
        # siliconflow_embeddings.generate_embedding. A Python-level exception
        # handler cannot catch this; the only fix is to never let two threads
        # enter the native handshake at the same time.
        # RLock, not Lock: _request re-enters the client via _try_refresh on a
        # 401, and a plain Lock would deadlock the caller there.
        self._http_lock = threading.RLock()
        self.last_error: Optional[str] = None
        self._token_file = Path.home() / ".cortex" / "auth.json"
        self._load_tokens()

        # Persistent HTTP client for connection pooling
        self._http_client = None
        if _HTTP_CLIENT == "httpx":
            self._http_client = httpx.Client(
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"X-Cortex-Platform": APP_PLATFORM},
            )

        # Model config cache
        self._cached_config: Optional[Dict] = None
        self._cached_config_version: str = ""

        # Subscription cache (see get_subscription / _SUBSCRIPTION_TTL)
        self._subscription_cache: Optional[Dict] = None
        self._subscription_cache_at: float = 0.0

    # ── Token persistence ──────────────────────────────────────────────

    def _load_tokens(self):
        """Load tokens from disk."""
        try:
            # ~/.cortex also holds .env (provider API keys) and cortex.db, and
            # main.py creates it under the default umask (0775), so tighten it
            # here instead of only on the next sign-in. Guarded by exists() so
            # a read never creates directories as a side effect.
            if self._token_file.parent.exists():
                _harden_secret_dir(self._token_file.parent)
            if self._token_file.exists():
                # Heal a token file an older build left world-readable. A user
                # who is already signed in may not re-save for days, and the
                # tokens sitting in that file stay valid for 30.
                _restrict_existing(self._token_file)
                data = json.loads(self._token_file.read_text(encoding="utf-8"))
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.expires_at = data.get("expires_at")
                self.user_info = data.get("user")
                log.info(f"[CortexAPI] Loaded tokens for {self.user_info.get('email', 'unknown') if self.user_info else 'unknown'}")
        except Exception as e:
            log.warning(f"[CortexAPI] Failed to load tokens: {e}")

    def _save_tokens(self) -> bool:
        """Save tokens to disk. False when the session will not survive a restart."""
        try:
            data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "user": self.user_info,
            }
            # _write_secret_text() creates ~/.cortex (0700) and auth.json
            # (0600) in one step. This file holds live bearer tokens, and the
            # old write_text() left it 0664 -- readable by every other local
            # user for the token's whole 30-day life.
            _write_secret_text(self._token_file, json.dumps(data, indent=2))
            return True
        except Exception as e:
            # A sign-in that cannot be written to disk works until the app is
            # closed and is then gone, so the login screen comes back on every
            # launch with nothing explaining why. This used to be a warning on
            # a logger that had no handler: invisible twice over. A full disk
            # is the realistic cause.
            log.error(f"[CortexAPI] Could not save the session to "
                      f"{self._token_file}: {e}")
            self.last_error = (
                f"Signed in, but the session could not be saved to disk "
                f"({e}). You will be asked to sign in again next launch.")
            return False

    def _clear_tokens(self):
        """Clear tokens from memory and disk."""
        self.access_token = None
        self.refresh_token = None
        self.expires_at = None
        self.user_info = None
        try:
            if self._token_file.exists():
                self._token_file.unlink()
        except Exception:
            pass

    # ── HTTP helpers ───────────────────────────────────────────────────

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Cortex-Version": APP_VERSION,
            "X-Cortex-Platform": APP_PLATFORM,
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _send(self, method: str, url: str, headers: Dict[str, str],
              json_data: dict = None, params: dict = None,
              timeout: float = 15.0):
        """Issue one HTTP request while holding the shared-client lock.

        Every path that touches `self._http_client` goes through here so that
        only one thread is ever inside the native OpenSSL handshake. See the
        `_http_lock` comment in __init__ for the crash this prevents.

        Returns the response, or None when no HTTP backend is available.
        """
        with self._http_lock:
            if self._http_client:
                return self._http_client.request(
                    method, url, headers=headers, json=json_data, params=params)
            if _HTTP_CLIENT == "requests":
                return requests.request(
                    method, url, headers=headers, json=json_data,
                    params=params, timeout=timeout)
        log.error("[CortexAPI] No HTTP client available")
        return None

    def _request(self, method: str, path: str, json_data: dict = None,
                 params: dict = None, timeout: float = 15.0) -> Optional[Dict]:
        """Make an HTTP request with connection pooling and auto-retry on 401.

        Returns response JSON or None on error.
        """
        url = f"{self.base_url}{path}"
        headers = self._get_headers()

        try:
            resp = self._send(method, url, headers, json_data, params, timeout)
            if resp is None:
                return None

            if resp.status_code == 401:
                # Try to refresh token
                if self._try_refresh():
                    headers = self._get_headers()
                    resp = self._send(method, url, headers, json_data, params, timeout)
                    if resp is None:
                        return None
                else:
                    self._clear_tokens()
                    return None

            if resp.status_code >= 400:
                log.warning(f"[CortexAPI] {method} {path} → {resp.status_code}: {resp.text[:200]}")
                # Keep the refusal. Returning a bare None here is what made a
                # failed sign-in unexplainable: the caller knew only "falsy".
                self.last_error = self._describe_error(resp)
                return None

            return resp.json()

        except Exception as e:
            log.warning(f"[CortexAPI] {method} {path} failed: {e}")
            self.last_error = f"Could not reach {self.base_url}: {e}"
            return None

    @staticmethod
    def _describe_error(resp) -> str:
        """Turn a JSON error body into one line a human can act on."""
        _HUMAN = {
            "email_not_verified": "Verify your email address, then sign in again.",
            "invalid_code":       "That sign-in link expired or was already used. Try again.",
            "missing_code":       "The browser sent no authorization code. Try again.",
            "invalid_body":       "The server rejected the sign-in request.",
            "server_error":       "The server hit an error completing sign-in.",
        }
        try:
            body = resp.json()
            code = str(body.get("error", "") or "")
            if code in _HUMAN:
                return _HUMAN[code]
            detail = body.get("detail") or code
            if detail:
                return f"{detail} (HTTP {resp.status_code})"
        except Exception:
            pass
        return f"The server refused the sign-in (HTTP {resp.status_code})."

    def _try_refresh(self) -> bool:
        """Try to refresh the access token."""
        if not self.refresh_token:
            return False

        try:
            url = f"{self.base_url}/api/v1/auth/refresh/"
            data = {"refresh_token": self.refresh_token}

            # Via _send so the refresh never races another thread's handshake.
            # Reached from inside _request's 401 branch, which already holds
            # _http_lock -- that is why the lock is an RLock.
            resp = self._send("POST", url, {"Content-Type": "application/json"},
                              json_data=data, timeout=10)
            if resp is None:
                return False

            if resp.status_code == 200:
                result = resp.json()
                self.access_token = result["access_token"]
                self.expires_at = result.get("expires_at")
                self._save_tokens()
                log.info("[CortexAPI] Token refreshed successfully")
                return True

        except Exception as e:
            log.warning(f"[CortexAPI] Token refresh failed: {e}")

        return False

    # ── Auth endpoints ─────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Check if user is logged in."""
        return self.access_token is not None and self.user_info is not None

    def is_server_reachable(self) -> bool:
        """Check if the Django server is reachable."""
        try:
            url = f"{self.base_url}/ops/health/"
            resp = self._send("GET", url, {"X-Cortex-Platform": APP_PLATFORM},
                              timeout=3)
            if resp is not None:
                return resp.status_code == 200
        except Exception:
            pass
        return False

    def get_login_url(self, state: str = "", redirect_uri: str = "http://127.0.0.1:18923/callback") -> Optional[str]:
        """Get the OAuth2 authorization URL with device info."""
        import platform
        result = self._request("GET", "/api/v1/auth/login/", params={
            "state": state,
            "redirect_uri": redirect_uri,
            "os": platform.system(),
            "version": APP_VERSION,
        })
        return result.get("auth_url") if result else None

    def login_with_code(self, code: str, state: str = "", device_info: dict = None) -> bool:
        """Exchange auth code for tokens."""
        self.last_error = None
        result = self._request("POST", "/api/v1/auth/callback/", json_data={
            "code": code,
            "state": state,
            "device_info": device_info or self._get_device_info(),
        })
        if result:
            self.access_token = result["access_token"]
            self.refresh_token = result["refresh_token"]
            self.expires_at = result.get("expires_at")
            self.user_info = result.get("user")
            self._save_tokens()
            log.info(f"[CortexAPI] Logged in as {self.user_info.get('email')}")
            return True
        log.error(f"[CortexAPI] Auth code exchange failed: {self.last_error}")
        return False

    def login_with_credentials(self, email: str, password: str) -> bool:
        """Direct login with email + password."""
        result = self._request("POST", "/api/v1/auth/login/credentials/", json_data={
            "email": email,
            "password": password,
            "device_info": self._get_device_info(),
        })
        if result:
            self.access_token = result["access_token"]
            self.refresh_token = result["refresh_token"]
            self.expires_at = result.get("expires_at")
            self.user_info = result.get("user")
            self._save_tokens()
            log.info(f"[CortexAPI] Logged in as {self.user_info.get('email')}")
            return True
        return False

    def logout(self):
        """Logout and revoke tokens."""
        if self.access_token:
            self._request("POST", "/api/v1/auth/logout/")
        self._clear_tokens()
        log.info("[CortexAPI] Logged out")

    def get_me(self) -> Optional[Dict]:
        """Get current user info from server."""
        return self._request("GET", "/api/v1/auth/me/")

    # ── Profile endpoints ──────────────────────────────────────────────

    def get_profile(self) -> Optional[Dict]:
        """Get user profile from server."""
        return self._request("GET", "/api/v1/profile/")

    def update_profile(self, data: Dict) -> Optional[Dict]:
        """Update user profile on server."""
        return self._request("PATCH", "/api/v1/profile/", json_data=data)

    # ── Usage endpoints ────────────────────────────────────────────────

    def get_usage_summary(self) -> Optional[Dict]:
        """Get usage summary from server."""
        return self._request("GET", "/api/v1/usage/summary/")

    def sync_usage(self, service_usage: Dict) -> Optional[Dict]:
        """Sync subscription service usage to server.

        Sends Mistral (OCR), SiliconFlow (embeddings), and web search usage.
        LLM usage stays local.

        Args:
            service_usage: {"ocr_pages": int, "embedding_tokens": int, "web_searches": int}
        """
        return self._request("POST", "/api/v1/usage/sync/", json_data={
            "service_usage": service_usage,
        })

    # ── Model config endpoints ────────────────────────────────────────

    def get_model_config(
        self, cached_version: str = "", ide_version: str = ""
    ) -> Optional[Dict]:
        """Fetch model configuration from server with ETag caching.

        Uses persistent client for connection pooling. Caches result locally
        to avoid re-fetching when version hasn't changed.

        Args:
            cached_version: Previously fetched config version string.
                           If it matches, server returns 304.
            ide_version:   This build's version. The server hides models whose
                           min_ide_version is newer, so a desktop that lacks
                           the client code for a model never lists it.

        Returns:
            Config dict with providers, models, defaults, or None if unchanged/error.
        """
        version_to_use = cached_version or self._cached_config_version
        headers = {"Content-Type": "application/json"}
        if version_to_use:
            headers["If-None-Match"] = f'"{version_to_use}"'

        url = f"{self.base_url}/api/v1/models/config/"
        if ide_version:
            url = f"{url}?ide_version={ide_version}"
        try:
            resp = self._send("GET", url, headers, timeout=10)
            if resp is None:
                return self._cached_config

            if resp.status_code == 304:
                return self._cached_config  # Return cached version
            if resp.status_code == 200:
                data = resp.json()
                self._cached_config = data
                self._cached_config_version = data.get("version", "")
                return data
            log.warning(f"[CortexAPI] Model config returned {resp.status_code}")
            return self._cached_config
        except Exception as e:
            log.debug(f"[CortexAPI] Model config fetch failed: {e}")
            return self._cached_config

    # ── Billing endpoints ──────────────────────────────────────────────

    # proxy_service() calls get_subscription() once per embedding purely to
    # read the license_key, so indexing a 1659-file project fired ~1659 extra
    # TLS requests from the background indexer thread. That request volume is
    # what made the shared-client race guarded by _http_lock so likely to be
    # hit. A license key only changes when a plan is bought or renewed, so a
    # short TTL is safe; `force` bypasses it for the "I just purchased" path.
    _SUBSCRIPTION_TTL = 60.0

    def get_subscription(self, force: bool = False) -> Optional[Dict]:
        """Get current subscription from server, cached for _SUBSCRIPTION_TTL.

        Only successful responses are cached, so an offline moment never pins a
        stale "no subscription" verdict for the whole TTL window.
        """
        now = time.time()
        if not force:
            cached = getattr(self, "_subscription_cache", None)
            if cached is not None and \
                    now - getattr(self, "_subscription_cache_at", 0.0) < self._SUBSCRIPTION_TTL:
                return cached
        result = self._request("GET", "/api/v1/billing/subscription/")
        if result is not None:
            self._subscription_cache = result
            self._subscription_cache_at = now
        return result

    def invalidate_subscription_cache(self) -> None:
        """Drop the cached subscription so the next read hits the server.

        Called after a purchase or plan change, so someone who just bought Pro
        is never told "no license key" for up to a minute afterwards.
        """
        self._subscription_cache = None
        self._subscription_cache_at = 0.0

    def get_credits(self) -> Optional[Dict]:
        """Get credit balance from server."""
        return self._request("GET", "/api/v1/billing/credits/")

    def get_payment_history(self) -> Optional[Dict]:
        """Get payment history from server."""
        return self._request("GET", "/api/v1/billing/history/")

    # ── Proxy chat endpoint ───────────────────────────────────────────

    def proxy_chat(self, model: str, messages: list, license_key: str,
                   temperature: float = 0.7, max_tokens: int = None,
                   tools: list = None, ocr_pages: int = 0) -> Optional[Dict]:
        """Send a chat request through the server proxy (included models only).

        Used for DeepSeek, MiMo, Mistral, models that use Cortex subscription credits.
        BYOK models (OpenAI, Qwen, etc.) should be called directly from the IDE.

        Args:
            model: Model ID (e.g. "deepseek-v4-pro-promo")
            messages: Chat messages array
            license_key: User's subscription license key
            temperature: Sampling temperature
            max_tokens: Max output tokens
            tools: Tool definitions for function calling
            ocr_pages: Number of OCR pages to process

        Returns:
            Response dict with choices, usage, and billing info, or None on error.
        """
        payload = {
            "license_key": license_key,
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "ocr_pages": ocr_pages,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        return self._request("POST", "/api/v1/proxy/chat/", json_data=payload, timeout=120)

    def proxy_service(self, service: str, **kwargs) -> Optional[Dict]:
        """Proxy subscription service request through server.

        Used for services that require server-side API keys:
        - mistral_ocr: Image text extraction
        - siliconflow_embeddings: Semantic search embeddings
        - web_search: Web search (SerpAPI)

        Args:
            service: Service name ("mistral_ocr", "siliconflow_embeddings", "web_search")
            **kwargs: Service-specific parameters

        Returns:
            Response dict or None on error.
        """
        # Get license key from subscription
        sub_info = self.get_subscription()
        license_key = sub_info.get("license_key", "") if sub_info else ""

        if not license_key:
            log.warning("[CortexAPI] No license key for proxy service")
            return None

        payload = {
            "license_key": license_key,
            "service": service,
            **kwargs,
        }
        return self._request("POST", "/api/v1/proxy/chat/", json_data=payload, timeout=60)

    # Re-read account info from /auth/me at most this often. user_info is the
    # copy saved at sign-in (auth.json) and was never refreshed, so a plan that
    # expired AFTER sign-in kept looking active until the next sign-in.
    _USER_INFO_TTL = 30 * 60

    @staticmethod
    def _iso_in_past(value) -> bool:
        """True when an ISO-8601 timestamp is in the past (unparseable: False)."""
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)

    def refresh_user_info_async(self, force: bool = False) -> None:
        """Re-fetch account info from /auth/me on a worker thread (never blocks)."""
        if not self.is_logged_in():
            return
        now = time.time()
        if not force and now - getattr(self, "_user_info_at", 0) < self._USER_INFO_TTL:
            return
        if getattr(self, "_user_info_refreshing", False):
            return
        self._user_info_refreshing = True
        self._user_info_at = now

        def _run():
            try:
                me = self.get_me()
                if isinstance(me, dict) and me.get("email"):
                    with self._lock:
                        self.user_info = {**(self.user_info or {}), **me}
                    self._save_tokens()
            except Exception as e:
                log.debug(f"[CortexAPI] account refresh failed: {e}")
            finally:
                self._user_info_refreshing = False

        threading.Thread(target=_run, daemon=True, name="cortex-account-refresh").start()

    def has_subscription(self) -> bool:
        """True only while the subscription is CURRENTLY entitled.

        This gates OCR, embeddings and web search, so "a plan name is set" is
        not good enough. The old version returned True whenever `plan` read
        "pro"/"pro_yearly"/"starter", and `plan` keeps its value after the
        billing period ends, so an expired account went on unlocking paid
        services while the website showed "Expired".
        """
        try:
            if not self.is_logged_in() or not self.user_info:
                return False
            self.refresh_user_info_async()  # keeps the saved copy current
            info = self.user_info
            # Newer server: an explicit, already-evaluated verdict.
            if info.get("subscription_expired"):
                return False
            status = (info.get("subscription_status") or "").lower().strip()
            if status and status not in ("active", "trialing"):
                return False
            # The end date is enough on its own, even offline or when the saved
            # copy predates the expiry.
            end = info.get("subscription_period_end")
            if end and self._iso_in_past(end):
                return False
            if "has_subscription" in info:
                return bool(info.get("has_subscription"))
            # Older server that never sent has_subscription: fall back to the
            # plan name, but only alongside an active status (checked above).
            plan = (info.get("plan") or "").lower().strip()
            return bool(status) and plan in ("pro", "pro_yearly", "starter")
        except Exception:
            return False

    # ── Lifecycle ─────────────────────────────────────────────────────

    def close(self):
        """Close the HTTP client and release resources.

        Holds _http_lock so the client is never torn down underneath a thread
        that is still mid-request: destroying an OpenSSL context another thread
        is handshaking on is the same class of native fault the lock exists to
        prevent, and shutdown is exactly when the indexer is most likely to
        still be running.
        """
        with self._http_lock:
            if self._http_client:
                try:
                    self._http_client.close()
                except Exception:
                    pass
                self._http_client = None

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_device_info(self) -> Dict:
        """Get device info for auth."""
        import platform
        return {
            "os": platform.system(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "app": "cortex_desktop",
        }


# ── Singleton ──────────────────────────────────────────────────────────

_api_client: Optional[CortexAPIClient] = None


def get_api_client() -> CortexAPIClient:
    """Get the singleton API client."""
    global _api_client
    if _api_client is None:
        # Load server URL from settings
        try:
            from src.config.settings import load_settings
            settings = load_settings()
            server_url = settings.get("server", {}).get("url", "https://cortex-ide.app")
        except Exception:
            server_url = "https://cortex-ide.app"
        _api_client = CortexAPIClient(base_url=server_url)
    return _api_client
