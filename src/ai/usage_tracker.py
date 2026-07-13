"""
Cortex Usage Tracker
Tracks token usage, request counts, tool calls, streaks, and insights.
All data stored locally in ~/.cortex/usage.json and ~/.cortex/profile.json.
"""

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

log = logging.getLogger("usage_tracker")

# Regex patterns for invalid/test model IDs that should not be tracked
_INVALID_MODEL_RE = re.compile(r'^(test|mock|fake|placeholder|unknown|default)', re.IGNORECASE)


class UsageTracker:
    """Tracks all AI usage metrics locally."""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            data_dir = str(Path.home() / ".cortex")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.usage_file = self.data_dir / "usage.json"
        self.profile_file = self.data_dir / "profile.json"
        self._usage = self._load_json(self.usage_file, self._default_usage())
        self._profile = self._load_json(self.profile_file, self._default_profile())
        # Clean up any stale test/placeholder model entries on startup
        try:
            self.cleanup_invalid_models()
        except Exception:
            pass

    # ── JSON Helpers ──────────────────────────────────────────────

    @staticmethod
    def _load_json(path: Path, default: dict) -> dict:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return default
        return default

    @staticmethod
    def _save_json(path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_usage(self):
        self._clean_invalid_models_in_place()
        self._save_json(self.usage_file, self._usage)
        self._sync_to_server()

    def _sync_to_server(self):
        """Sync usage data to Django server if logged in. Non-blocking."""
        try:
            from src.core.cortex_api import get_api_client
            api = get_api_client()
            if not api.is_logged_in():
                return
            # Run sync in background to avoid blocking
            import threading
            threading.Thread(
                target=self._do_sync,
                args=(api,),
                daemon=True,
            ).start()
        except Exception:
            pass  # Offline or not configured

    def _do_sync(self, api):
        """Sync subscription service usage to server (runs in background thread).
        
        Sends INCREMENTAL deltas, not cumulative totals. Avoids inflation bug
        where cumulative values were repeatedly added as new UsageLog records.
        """
        try:
            # Track last synced values to compute deltas
            if not hasattr(self, '_last_synced'):
                self._last_synced = {"ocr_pages": 0, "embedding_tokens": 0, "web_searches": 0}
            
            current = {
                "ocr_pages": self._usage.get("lifetime", {}).get("ocr_pages", 0),
                "embedding_tokens": self._usage.get("lifetime", {}).get("embedding_tokens", 0),
                "web_searches": self._usage.get("lifetime", {}).get("web_searches", 0),
            }
            
            # Compute delta since last sync
            delta = {
                k: max(0, current[k] - self._last_synced.get(k, 0))
                for k in current
            }
            
            if delta["ocr_pages"] > 0 or delta["embedding_tokens"] > 0 or delta["web_searches"] > 0:
                result = api.sync_usage(delta)
                if result:
                    # Only update last_synced on success
                    self._last_synced = current.copy()
                    log.debug(f"[UsageTracker] Synced delta to server: {delta}")
        except Exception as e:
            log.debug(f"[UsageTracker] Server sync failed: {e}")

    def _clean_invalid_models_in_place(self):
        """Remove invalid model entries from in-memory data (no save, no recursion)."""
        for model_id in list(self._usage.get("model_usage", {}).keys()):
            if not self._is_valid_model(model_id):
                del self._usage["model_usage"][model_id]
        for day_data in self._usage.get("daily_usage", {}).values():
            models = day_data.get("models", {})
            for model_id in list(models.keys()):
                if not self._is_valid_model(model_id):
                    del models[model_id]

    def _save_profile(self):
        self._save_json(self.profile_file, self._profile)

    # ── Defaults ──────────────────────────────────────────────────

    @staticmethod
    def _default_usage() -> dict:
        return {
            "version": 2,
            "lifetime": {
                "total_tokens": 0,
                "total_requests": 0,
                "total_tool_calls": 0,
                "total_sessions": 0,
                "longest_task_seconds": 0,
                "first_session": None,
                # Subscription service tracking
                "ocr_pages": 0,
                "embedding_tokens": 0,
                "web_searches": 0,
            },
            "current_period": {
                "start_date": None,
                "end_date": None,
                "tokens_used": 0,
                "tokens_limit": 200000,
                "requests_used": 0,
                "requests_limit": 100,
                "tool_calls_used": 0,
                "tool_calls_limit": 0,
                # Subscription service tracking
                "ocr_pages_used": 0,
                "embedding_tokens_used": 0,
                "web_searches_used": 0,
            },
            "streaks": {
                "current_streak_days": 0,
                "longest_streak_days": 0,
                "last_active_date": None,
                "streak_start_date": None,
            },
            "daily_usage": {},
            "model_usage": {},
            "insights": {
                "fast_mode_percent": 0,
                "most_reasoning_level": "medium",
                "reasoning_percent": 0,
                "skills_explored": [],
                "total_skills_used": 0,
                "plugins_used": [],
            },
            "peak": {
                "peak_tokens_single_session": 0,
                "peak_date": None,
            },
        }

    @staticmethod
    def _default_profile() -> dict:
        return {
            "version": 1,
            "profile": {
                "display_name": "User",
                "username": "@user",
                "email": "",
                "avatar_color": "#f97316",
                "avatar_initials": "U",
                "plan": "free",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "last_active": datetime.utcnow().isoformat() + "Z",
            },
            "auth": {
                "logged_in": False,
                "auth_method": None,
                "token_hash": None,
                "expires_at": None,
            },
        }

    # ── Profile Methods ───────────────────────────────────────────

    def get_profile(self) -> dict:
        """Return full profile.json contents."""
        return self._profile

    def set_profile(self, key: str, value: Any) -> bool:
        """Update a profile field. Returns True on success."""
        allowed = {"display_name", "username", "email", "avatar_color", "avatar_initials", "plan"}
        if key not in allowed:
            return False
        self._profile["profile"][key] = value
        self._profile["profile"]["last_active"] = datetime.utcnow().isoformat() + "Z"
        self._save_profile()
        return True

    def set_avatar(self, avatar_color: str, avatar_initials: str):
        """Update avatar color and initials."""
        self._profile["profile"]["avatar_color"] = avatar_color
        self._profile["profile"]["avatar_initials"] = avatar_initials
        self._save_profile()

    # ── Usage Tracking Methods ────────────────────────────────────

    @staticmethod
    def _is_valid_model(model: str) -> bool:
        """Return True if the model ID looks like a real model, not a test/placeholder."""
        if not model or not isinstance(model, str):
            return False
        if len(model) < 2 or len(model) > 80:
            return False
        return _INVALID_MODEL_RE.search(model) is None

    def record_token_usage(self, model: str, input_tokens: int, output_tokens: int):
        """Called after every AI response."""
        # Skip tracking for test/placeholder model IDs
        if not self._is_valid_model(model):
            return
        total = input_tokens + output_tokens
        today = date.today().isoformat()

        # Ensure today's entry exists
        if today not in self._usage["daily_usage"]:
            self._usage["daily_usage"][today] = {
                "tokens": 0,
                "requests": 0,
                "tool_calls": 0,
                "models": {},
            }

        daily = self._usage["daily_usage"][today]
        daily["tokens"] += total
        daily["requests"] += 1
        daily["models"][model] = daily["models"].get(model, 0) + total

        # Lifetime
        self._usage["lifetime"]["total_tokens"] += total
        self._usage["lifetime"]["total_requests"] += 1
        if self._usage["lifetime"]["first_session"] is None:
            self._usage["lifetime"]["first_session"] = today + "T00:00:00Z"

        # Model usage
        if model not in self._usage["model_usage"]:
            self._usage["model_usage"][model] = {"total_tokens": 0, "total_requests": 0}
        self._usage["model_usage"][model]["total_tokens"] += total
        self._usage["model_usage"][model]["total_requests"] += 1

        # Peak
        if total > self._usage["peak"]["peak_tokens_single_session"]:
            self._usage["peak"]["peak_tokens_single_session"] = total
            self._usage["peak"]["peak_date"] = today

        # Current period
        self._ensure_period()
        self._usage["current_period"]["tokens_used"] += total
        self._usage["current_period"]["requests_used"] += 1

        # Streaks
        self._update_streaks(today)

        self._save_usage()

    def record_tool_call(self, tool_name: str, duration_seconds: float = 0):
        """Called after every agent tool call."""
        today = date.today().isoformat()

        if today not in self._usage["daily_usage"]:
            self._usage["daily_usage"][today] = {
                "tokens": 0,
                "requests": 0,
                "tool_calls": 0,
                "models": {},
            }

        self._usage["daily_usage"][today]["tool_calls"] += 1
        self._usage["lifetime"]["total_tool_calls"] += 1

        self._ensure_period()
        self._usage["current_period"]["tool_calls_used"] += 1

        if duration_seconds > self._usage["lifetime"]["longest_task_seconds"]:
            self._usage["lifetime"]["longest_task_seconds"] = duration_seconds

        # Track skills/tools explored
        if tool_name and tool_name not in self._usage["insights"]["skills_explored"]:
            self._usage["insights"]["skills_explored"].append(tool_name)
            self._usage["insights"]["total_skills_used"] = len(self._usage["insights"]["skills_explored"])

        self._update_streaks(today)
        self._save_usage()

    def record_session_start(self):
        """Called when a new chat session begins."""
        today = date.today().isoformat()
        self._usage["lifetime"]["total_sessions"] += 1
        self._update_streaks(today)
        self._save_usage()

    def record_model_switch(self, from_model: str, to_model: str):
        """Called when user switches model."""
        pass  # Could track model switch frequency in future

    def record_fast_mode(self, enabled: bool):
        """Called when fast mode is toggled."""
        daily_insights = self._usage["insights"]
        # Update rolling average
        total_sessions = self._usage["lifetime"]["total_sessions"] or 1
        current = daily_insights["fast_mode_percent"]
        if enabled:
            daily_insights["fast_mode_percent"] = min(100, int(current + (100 - current) / total_sessions))
        else:
            daily_insights["fast_mode_percent"] = max(0, int(current - current / total_sessions))
        self._save_usage()

    def record_reasoning_level(self, level: str):
        """Called when reasoning level is used. level: low/medium/high."""
        self._usage["insights"]["most_reasoning_level"] = level
        # Calculate reasoning percent based on total requests that used reasoning
        total_requests = self._usage["lifetime"]["total_requests"] or 1
        reasoning_requests = self._usage["insights"].get("_reasoning_requests", 0) + 1
        self._usage["insights"]["_reasoning_requests"] = reasoning_requests
        self._usage["insights"]["reasoning_percent"] = min(100, int((reasoning_requests / total_requests) * 100))
        self._save_usage()

    def record_plugin_used(self, plugin_name: str):
        """Called when a plugin is used."""
        if plugin_name and plugin_name not in self._usage["insights"]["plugins_used"]:
            self._usage["insights"]["plugins_used"].append(plugin_name)
            self._save_usage()

    def record_ocr_pages(self, pages: int):
        """Called when Mistral OCR processes image(s). Subscription service."""
        if pages > 0:
            self._usage["lifetime"]["ocr_pages"] = self._usage["lifetime"].get("ocr_pages", 0) + pages
            self._ensure_period()
            self._usage["current_period"]["ocr_pages_used"] = self._usage["current_period"].get("ocr_pages_used", 0) + pages
            self._save_usage()
            # Sync to server
            self._sync_to_server()

    def record_web_searches(self, count: int = 1):
        """Called when a web search is performed. Subscription service."""
        if count > 0:
            self._usage["lifetime"]["web_searches"] = self._usage["lifetime"].get("web_searches", 0) + count
            self._ensure_period()
            self._usage["current_period"]["web_searches_used"] = self._usage["current_period"].get("web_searches_used", 0) + count
            self._save_usage()
            # Sync to server
            self._sync_to_server()

    def record_embedding_tokens(self, tokens: int):
        """Called when SiliconFlow generates embeddings. Subscription service."""
        if tokens > 0:
            self._usage["lifetime"]["embedding_tokens"] = self._usage["lifetime"].get("embedding_tokens", 0) + tokens
            self._ensure_period()
            self._usage["current_period"]["embedding_tokens_used"] = self._usage["current_period"].get("embedding_tokens_used", 0) + tokens
            self._save_usage()
            # Sync to server
            self._sync_to_server()

    # ── Query Methods ─────────────────────────────────────────────

    def get_usage_stats(self) -> dict:
        """Return full usage.json contents."""
        return self._usage

    def get_current_limits(self) -> dict:
        """Return current period limits and usage."""
        self._ensure_period()
        return self._usage["current_period"]

    def get_usage_for_range(self, range_type: str) -> dict:
        """
        Return usage data points for chart rendering.
        range_type: "daily" | "weekly" | "cumulative"
        Returns: {"points": [{"label": str, "value": int}, ...]}
        """
        daily = self._usage.get("daily_usage", {})

        if range_type == "daily":
            # Last 14 days
            points = []
            today = date.today()
            for i in range(13, -1, -1):
                d = today - timedelta(days=i)
                key = d.isoformat()
                tokens = daily.get(key, {}).get("tokens", 0)
                points.append({"label": d.strftime("%b %d"), "value": tokens})
            return {"points": points}

        elif range_type == "weekly":
            # Last 8 weeks
            points = []
            today = date.today()
            for i in range(7, -1, -1):
                week_start = today - timedelta(weeks=i)
                week_end = week_start + timedelta(days=6)
                total = 0
                for j in range(7):
                    d = (week_start + timedelta(days=j)).isoformat()
                    total += daily.get(d, {}).get("tokens", 0)
                label = week_start.strftime("%b %d")
                points.append({"label": label, "value": total})
            return {"points": points}

        elif range_type == "cumulative":
            # Last 12 months
            points = []
            today = date.today()
            for i in range(11, -1, -1):
                # Calculate month
                year = today.year
                month = today.month - i
                while month <= 0:
                    month += 12
                    year -= 1
                month_start = date(year, month, 1)
                if month == 12:
                    month_end = date(year + 1, 1, 1) - timedelta(days=1)
                else:
                    month_end = date(year, month + 1, 1) - timedelta(days=1)
                total = 0
                current = month_start
                while current <= month_end:
                    total += daily.get(current.isoformat(), {}).get("tokens", 0)
                    current += timedelta(days=1)
                points.append({"label": month_start.strftime("%b"), "value": total})
            return {"points": points}

        return {"points": []}

    def get_usage_for_range(self, start_date: str, end_date: str, granularity: str = "daily") -> dict:
        """Return usage data for a date range, grouped by granularity (daily/weekly/cumulative).

        Returns:
            {
                "range": {"start": "2026-06-01", "end": "2026-06-28"},
                "granularity": "daily",
                "points": [
                    {"date": "2026-06-01", "tokens": 1200, "requests": 3, "tool_calls": 5, "models": {}},
                    ...
                ],
                "totals": {"tokens": 45000, "requests": 120, "tool_calls": 200}
            }
        """
        daily = self._usage.get("daily_usage", {})
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except (ValueError, TypeError):
            start = date.today() - timedelta(days=30)
            end = date.today()

        points = []
        total_tokens = 0
        total_requests = 0
        total_tool_calls = 0

        if granularity == "cumulative":
            # Cumulative: one running-total line
            running = 0
            current = start
            while current <= end:
                day_data = daily.get(current.isoformat(), {})
                running += day_data.get("tokens", 0)
                points.append({
                    "date": current.isoformat(),
                    "tokens": running,
                    "requests": day_data.get("requests", 0),
                    "tool_calls": day_data.get("tool_calls", 0),
                    "models": day_data.get("models", {}),
                })
                total_tokens += day_data.get("tokens", 0)
                total_requests += day_data.get("requests", 0)
                total_tool_calls += day_data.get("tool_calls", 0)
                current += timedelta(days=1)

        elif granularity == "weekly":
            # Weekly: aggregate by ISO week
            week_buckets: Dict[str, dict] = {}
            current = start
            while current <= end:
                week_key = f"{current.isocalendar()[0]}-W{current.isocalendar()[1]:02d}"
                if week_key not in week_buckets:
                    week_buckets[week_key] = {"tokens": 0, "requests": 0, "tool_calls": 0, "models": {}}
                day_data = daily.get(current.isoformat(), {})
                week_buckets[week_key]["tokens"] += day_data.get("tokens", 0)
                week_buckets[week_key]["requests"] += day_data.get("requests", 0)
                week_buckets[week_key]["tool_calls"] += day_data.get("tool_calls", 0)
                for m, mc in day_data.get("models", {}).items():
                    week_buckets[week_key]["models"][m] = week_buckets[week_key]["models"].get(m, 0) + mc
                total_tokens += day_data.get("tokens", 0)
                total_requests += day_data.get("requests", 0)
                total_tool_calls += day_data.get("tool_calls", 0)
                current += timedelta(days=1)

            for wk, data in sorted(week_buckets.items()):
                points.append({"date": wk, **data})

        else:
            # Daily (default)
            current = start
            while current <= end:
                day_data = daily.get(current.isoformat(), {})
                points.append({
                    "date": current.isoformat(),
                    "tokens": day_data.get("tokens", 0),
                    "requests": day_data.get("requests", 0),
                    "tool_calls": day_data.get("tool_calls", 0),
                    "models": day_data.get("models", {}),
                })
                total_tokens += day_data.get("tokens", 0)
                total_requests += day_data.get("requests", 0)
                total_tool_calls += day_data.get("tool_calls", 0)
                current += timedelta(days=1)

        return {
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "granularity": granularity,
            "points": points,
            "totals": {
                "tokens": total_tokens,
                "requests": total_requests,
                "tool_calls": total_tool_calls,
            },
        }

    def get_model_breakdown(self) -> List[dict]:
        """Return per-model usage sorted by total tokens desc (filters invalid models)."""
        models = self._usage.get("model_usage", {})
        result = []
        for model_id, data in models.items():
            if not self._is_valid_model(model_id):
                continue
            result.append({
                "model": model_id,
                "tokens": data.get("total_tokens", 0),
                "requests": data.get("total_requests", 0),
            })
        result.sort(key=lambda x: x["tokens"], reverse=True)
        return result

    def cleanup_invalid_models(self):
        """Remove any test/placeholder model entries from usage data."""
        changed = False
        for model_id in list(self._usage.get("model_usage", {}).keys()):
            if not self._is_valid_model(model_id):
                del self._usage["model_usage"][model_id]
                changed = True
        for day_data in self._usage.get("daily_usage", {}).values():
            models = day_data.get("models", {})
            for model_id in list(models.keys()):
                if not self._is_valid_model(model_id):
                    del models[model_id]
                    changed = True
        if changed:
            self._save_usage()

    def get_insights(self) -> dict:
        """Return activity insights."""
        return self._usage.get("insights", {})

    def get_streaks(self) -> dict:
        """Return current and longest streak."""
        return self._usage.get("streaks", {})

    # ── Internal Helpers ──────────────────────────────────────────

    def _ensure_period(self):
        """Make sure current_period has valid dates."""
        period = self._usage["current_period"]
        today = date.today()
        if period["start_date"] is None or period["end_date"] is None:
            # Start from today, 30-day window
            period["start_date"] = today.isoformat()
            period["end_date"] = (today + timedelta(days=30)).isoformat()
        else:
            end = date.fromisoformat(period["end_date"])
            if today > end:
                # Period expired — reset
                period["start_date"] = today.isoformat()
                period["end_date"] = (today + timedelta(days=30)).isoformat()
                period["tokens_used"] = 0
                period["requests_used"] = 0
                period["tool_calls_used"] = 0

    def _update_streaks(self, today_str: str):
        """Update current and longest streak."""
        streaks = self._usage["streaks"]
        last = streaks.get("last_active_date")

        if last == today_str:
            return  # Already counted today

        if last is not None:
            try:
                last_date = date.fromisoformat(last)
                yesterday = date.today() - timedelta(days=1)
                if last_date == yesterday:
                    streaks["current_streak_days"] += 1
                else:
                    streaks["current_streak_days"] = 1
            except ValueError:
                streaks["current_streak_days"] = 1
        else:
            streaks["current_streak_days"] = 1

        if streaks["current_streak_days"] > streaks["longest_streak_days"]:
            streaks["longest_streak_days"] = streaks["current_streak_days"]

        streaks["last_active_date"] = today_str
        if streaks.get("streak_start_date") is None:
            streaks["streak_start_date"] = today_str

    # ── Export ────────────────────────────────────────────────────

    def export_csv(self, output_path: Optional[str] = None) -> str:
        """Export daily usage as CSV. Returns file path."""
        if output_path is None:
            output_path = str(self.data_dir / "usage_export.csv")

        daily = self._usage.get("daily_usage", {})
        lines = ["date,tokens,requests,tool_calls"]
        for day_key in sorted(daily.keys()):
            d = daily[day_key]
            lines.append(f"{day_key},{d.get('tokens',0)},{d.get('requests',0)},{d.get('tool_calls',0)}")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return output_path

    def export_json(self, output_path: Optional[str] = None) -> str:
        """Export full usage data as JSON. Returns file path."""
        if output_path is None:
            output_path = str(self.data_dir / "usage_export.json")
        self._save_json(Path(output_path), self._usage)
        return output_path

    def update_profile_bulk(self, data: Dict[str, Any]) -> bool:
        """Update multiple profile fields at once from a dict.

        Args:
            data: dict with keys like 'display_name', 'username', 'avatar_color', etc.

        Returns:
            True if at least one field was updated.
        """
        allowed = {"display_name", "username", "email", "avatar_color", "avatar_initials", "plan"}
        updated = False
        for key, value in data.items():
            if key in allowed:
                self._profile["profile"][key] = value
                updated = True
        if updated:
            self._profile["profile"]["last_active"] = datetime.utcnow().isoformat() + "Z"
            self._save_profile()
        return updated


# ════════════════════════════════════════════════════════════════
# Singleton — one instance shared across the application
# ════════════════════════════════════════════════════════════════

_USAGE_TRACKER_SINGLETON: Optional[UsageTracker] = None


def get_usage_tracker(data_dir: Optional[str] = None) -> UsageTracker:
    """Return the global UsageTracker instance (creates on first call)."""
    global _USAGE_TRACKER_SINGLETON
    if _USAGE_TRACKER_SINGLETON is None:
        _USAGE_TRACKER_SINGLETON = UsageTracker(data_dir=data_dir)
    return _USAGE_TRACKER_SINGLETON


def reset_usage_tracker():
    """Reset the singleton (for testing)."""
    global _USAGE_TRACKER_SINGLETON
    _USAGE_TRACKER_SINGLETON = None
