# api/usage.py
# Cortex IDE Usage Tracking Module
# Tracks API usage, token consumption, and reporting

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from collections import defaultdict


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class UsageRecord:
    """A single usage record."""
    timestamp: datetime
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    request_count: int = 1
    metadata: Dict[str, str] = field(default_factory=dict)
    
    @property
    def total_tokens(self) -> int:
        """Get total tokens used."""
        return self.input_tokens + self.output_tokens


@dataclass
class DailyUsage:
    """Usage statistics for a single day."""
    date: date
    records: List[UsageRecord] = field(default_factory=list)
    
    def add_record(self, record: UsageRecord) -> None:
        """Add a usage record."""
        self.records.append(record)
    
    @property
    def total_input_tokens(self) -> int:
        """Get total input tokens for the day."""
        return sum(r.input_tokens for r in self.records)
    
    @property
    def total_output_tokens(self) -> int:
        """Get total output tokens for the day."""
        return sum(r.output_tokens for r in self.records)
    
    @property
    def total_tokens(self) -> int:
        """Get total tokens for the day."""
        return sum(r.total_tokens for r in self.records)
    
    @property
    def total_requests(self) -> int:
        """Get total requests for the day."""
        return sum(r.request_count for r in self.records)
    
    @property
    def by_provider(self) -> Dict[str, int]:
        """Get usage by provider."""
        result: Dict[str, int] = defaultdict(int)
        for record in self.records:
            result[record.provider] += record.total_tokens
        return dict(result)
    
    @property
    def by_model(self) -> Dict[str, int]:
        """Get usage by model."""
        result: Dict[str, int] = defaultdict(int)
        for record in self.records:
            result[record.model] += record.total_tokens
        return dict(result)


# ============================================================================
# USAGE TRACKER
# ============================================================================

class UsageTracker:
    """
    Tracks and reports API usage.
    """
    
    _instance: Optional['UsageTracker'] = None
    
    def __init__(self):
        self._daily_usage: Dict[date, DailyUsage] = {}
        self._current_provider: str = "anthropic"
        self._current_model: str = "claude-3-opus"
    
    @classmethod
    def get_instance(cls) -> 'UsageTracker':
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance."""
        cls._instance = None
    
    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> UsageRecord:
        """Record a usage event."""
        today = date.today()
        if today not in self._daily_usage:
            self._daily_usage[today] = DailyUsage(date=today)
        
        record = UsageRecord(
            timestamp=datetime.now(),
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata=metadata or {}
        )
        
        self._daily_usage[today].add_record(record)
        return record
    
    def get_daily_usage(self, day: Optional[date] = None) -> DailyUsage:
        """Get usage for a specific day."""
        target_day = day or date.today()
        return self._daily_usage.get(target_day, DailyUsage(date=target_day))
    
    def get_monthly_usage(self, year: int, month: int) -> Dict[str, int]:
        """Get aggregated usage for a month."""
        total_input = 0
        total_output = 0
        total_requests = 0
        
        for day, usage in self._daily_usage.items():
            if day.year == year and day.month == month:
                total_input += usage.total_input_tokens
                total_output += usage.total_output_tokens
                total_requests += usage.total_requests
        
        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "requests": total_requests,
        }
    
    def get_usage_summary(self) -> Dict[str, Any]:
        """Get overall usage summary."""
        today = self.get_daily_usage()
        return {
            "today": {
                "tokens": today.total_tokens,
                "requests": today.total_requests,
            },
            "by_provider": today.by_provider,
            "by_model": today.by_model,
        }
    
    def clear_old_usage(self, days_to_keep: int = 30) -> None:
        """Clear usage records older than specified days."""
        cutoff = date.today() - timedelta(days=days_to_keep)
        self._daily_usage = {
            d: u for d, u in self._daily_usage.items() if d >= cutoff
        }


# ============================================================================
# MODULE-LEVEL FUNCTIONS
# ============================================================================

def get_usage_tracker() -> UsageTracker:
    """Get the global usage tracker instance."""
    return UsageTracker.get_instance()


def get_usage_info() -> Dict[str, Any]:
    """Get current API usage information."""
    tracker = get_usage_tracker()
    return tracker.get_usage_summary()


__all__ = [
    # Classes
    "UsageTracker",
    "UsageRecord",
    "DailyUsage",
    # Functions
    "get_usage_tracker",
    "get_usage_info",
]
