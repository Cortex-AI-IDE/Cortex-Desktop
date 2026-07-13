"""
Token and Cost Usage Tracker for Cortex IDE
Extracted from claude-code-main/src/services/api/claude.ts

Ports the logic of:
  - updateUsage()       → UsageTracker.update_streaming()
  - accumulateUsage()   → UsageTracker.record() / session totals
  - addToTotalSessionCost() → UsageTracker.session_cost_usd

All data is in-memory per session.  Persisted snapshots can be
written via UsageTracker.save() / load() if needed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from threading import Lock
from typing import Dict, List, Optional

from src.utils.logger import get_logger

log = get_logger("usage_tracker")


# ═════════════════════════════════════════════════════════════════════════════
# Data structures (mirror NonNullableUsage in claude.ts / logging.ts)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class TokenUsage:
    """
    Cumulative token counts for a single API call.
    Mirrors NonNullableUsage in services/api/logging.ts.

    Streaming rule (from updateUsage comment in claude.ts:2914):
      - input_tokens / cache_* are set in message_start and stay constant.
      - message_delta may send 0 for these — do NOT overwrite with 0.
      - output_tokens always comes from the latest delta.
    """
    input_tokens:                int   = 0
    output_tokens:               int   = 0
    cache_creation_input_tokens: int   = 0
    cache_read_input_tokens:     int   = 0
    # Cost mirrors calculateUSDCost(model, usage) in utils/modelCost.ts
    cost_usd:                    float = 0.0


@dataclass
class TurnRecord:
    """Record for a single completed LLM turn."""
    timestamp:  float   = field(default_factory=time.time)
    provider:   str     = ""
    model:      str     = ""
    input_tok:  int     = 0
    output_tok: int     = 0
    cost_usd:   float   = 0.0
    duration_ms: float  = 0.0


@dataclass
class SessionStats:
    """Aggregated stats for the current IDE session."""
    total_input_tokens:  int   = 0
    total_output_tokens: int   = 0
    total_cost_usd:      float = 0.0
    turn_count:          int   = 0
    turns:               List[TurnRecord] = field(default_factory=list)


# ═════════════════════════════════════════════════════════════════════════════
# UsageTracker
# ═════════════════════════════════════════════════════════════════════════════

class UsageTracker:
    """
    Thread-safe tracker for token usage and cost across all LLM providers.

    Design mirrors the addToTotalSessionCost / updateUsage pattern in
    claude.ts + cost-tracker.ts — but generalised for multi-LLM Cortex.
    """

    def __init__(self):
        self._lock   = Lock()
        self._stats  = SessionStats()
        self._current: TokenUsage = TokenUsage()

    # ── Streaming helpers ─────────────────────────────────────────────────────

    def update_streaming(
        self,
        input_tokens:                Optional[int] = None,
        output_tokens:               Optional[int] = None,
        cache_creation_input_tokens: Optional[int] = None,
        cache_read_input_tokens:     Optional[int] = None,
    ) -> TokenUsage:
        """
        Apply a partial streaming delta to the current in-flight usage.
        Mirrors updateUsage() in claude.ts:2924.

        Rule: only overwrite input/cache counts if the new value is > 0
              (Anthropic streaming sends 0 in message_delta for these fields).
        """
        with self._lock:
            if input_tokens and input_tokens > 0:
                self._current.input_tokens = input_tokens
            if cache_creation_input_tokens and cache_creation_input_tokens > 0:
                self._current.cache_creation_input_tokens = cache_creation_input_tokens
            if cache_read_input_tokens and cache_read_input_tokens > 0:
                self._current.cache_read_input_tokens = cache_read_input_tokens
            if output_tokens is not None:
                self._current.output_tokens = output_tokens
            return TokenUsage(**self._current.__dict__)

    def flush_current(self) -> TokenUsage:
        """Return and reset the current in-flight usage after a turn completes."""
        with self._lock:
            result = TokenUsage(**self._current.__dict__)
            self._current = TokenUsage()
            return result

    # ── Turn recording ────────────────────────────────────────────────────────

    def record(
        self,
        provider:    str,
        model:       str,
        input_tok:   int,
        output_tok:  int,
        cost_usd:    float   = 0.0,
        duration_ms: float   = 0.0,
    ) -> None:
        """
        Record a completed turn.
        Mirrors accumulateUsage() in claude.ts:2993 +
                addToTotalSessionCost() in cost-tracker.ts.
        """
        with self._lock:
            turn = TurnRecord(
                provider   = provider,
                model      = model,
                input_tok  = input_tok,
                output_tok = output_tok,
                cost_usd   = cost_usd,
                duration_ms = duration_ms,
            )
            self._stats.turns.append(turn)
            self._stats.total_input_tokens  += input_tok
            self._stats.total_output_tokens += output_tok
            self._stats.total_cost_usd      += cost_usd
            self._stats.turn_count          += 1

        log.debug(
            "Turn recorded: provider=%s model=%s in=%d out=%d cost=$%.6f",
            provider, model, input_tok, output_tok, cost_usd,
        )

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def session_stats(self) -> SessionStats:
        with self._lock:
            return SessionStats(
                total_input_tokens  = self._stats.total_input_tokens,
                total_output_tokens = self._stats.total_output_tokens,
                total_cost_usd      = self._stats.total_cost_usd,
                turn_count          = self._stats.turn_count,
                turns               = list(self._stats.turns),
            )

    @property
    def session_cost_usd(self) -> float:
        with self._lock:
            return self._stats.total_cost_usd

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return self._stats.total_input_tokens + self._stats.total_output_tokens

    def reset_session(self) -> None:
        """Clear all session stats (e.g. on new project open)."""
        with self._lock:
            self._stats  = SessionStats()
            self._current = TokenUsage()
        log.info("UsageTracker session reset")

    # ── Per-model breakdown ───────────────────────────────────────────────────

    def breakdown_by_model(self) -> Dict[str, Dict[str, float]]:
        """Return per-model usage summary (for settings / billing panel)."""
        result: Dict[str, Dict[str, float]] = {}
        with self._lock:
            for turn in self._stats.turns:
                key = f"{turn.provider}/{turn.model}"
                if key not in result:
                    result[key] = {"input": 0, "output": 0, "cost": 0.0, "turns": 0}
                result[key]["input"]  += turn.input_tok
                result[key]["output"] += turn.output_tok
                result[key]["cost"]   += turn.cost_usd
                result[key]["turns"]  += 1
        return result

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Save session stats to JSON (optional, for cross-session analytics)."""
        try:
            with self._lock:
                data = asdict(self._stats)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            log.info("UsageTracker saved to %s", path)
        except Exception as exc:
            log.error("UsageTracker.save failed: %s", exc)

    def load(self, path: str) -> None:
        """Restore session stats from JSON."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                turns = [TurnRecord(**t) for t in data.get("turns", [])]
                self._stats = SessionStats(
                    total_input_tokens  = data.get("total_input_tokens", 0),
                    total_output_tokens = data.get("total_output_tokens", 0),
                    total_cost_usd      = data.get("total_cost_usd", 0.0),
                    turn_count          = data.get("turn_count", 0),
                    turns               = turns,
                )
            log.info("UsageTracker loaded from %s", path)
        except FileNotFoundError:
            log.info("No usage file at %s — starting fresh", path)
        except Exception as exc:
            log.error("UsageTracker.load failed: %s", exc)


# ─── Module-level singleton ───────────────────────────────────────────────────
_tracker: Optional[UsageTracker] = None


def get_usage_tracker() -> UsageTracker:
    """Return the shared UsageTracker singleton."""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker
