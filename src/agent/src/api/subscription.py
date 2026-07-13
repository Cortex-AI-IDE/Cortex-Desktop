# api/subscription.py
# Cortex IDE Subscription Management Module
# Handles subscription status, plan limits, and access control

from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from enum import Enum


# ============================================================================
# ENUMS
# ============================================================================

class PlanType(str, Enum):
    """Subscription plan types."""
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    TRIAL = "trial"


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class PlanLimits:
    """Limits for a subscription plan."""
    max_requests_per_day: int = 100
    max_tokens_per_month: int = 100000
    max_concurrent_sessions: int = 1
    features: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.features is None:
            self.features = []
    
    def allows_feature(self, feature: str) -> bool:
        """Check if a feature is allowed."""
        if self.features is None:
            return False
        return feature in self.features
    
    @classmethod
    def free_limits(cls) -> 'PlanLimits':
        """Get limits for free plan."""
        return cls(
            max_requests_per_day=100,
            max_tokens_per_month=100000,
            max_concurrent_sessions=1,
            features=["basic_llm"]
        )
    
    @classmethod
    def pro_limits(cls) -> 'PlanLimits':
        """Get limits for pro plan."""
        return cls(
            max_requests_per_day=1000,
            max_tokens_per_month=1000000,
            max_concurrent_sessions=5,
            features=["basic_llm", "advanced_llm", "priority_support"]
        )
    
    @classmethod
    def enterprise_limits(cls) -> 'PlanLimits':
        """Get limits for enterprise plan."""
        return cls(
            max_requests_per_day=-1,  # unlimited
            max_tokens_per_month=-1,  # unlimited
            max_concurrent_sessions=-1,  # unlimited
            features=["basic_llm", "advanced_llm", "priority_support", "custom_models", "sso"]
        )


# ============================================================================
# SUBSCRIPTION MANAGER
# ============================================================================

class SubscriptionManager:
    """
    Manages subscription status and access control.
    """
    
    _instance: Optional['SubscriptionManager'] = None
    
    def __init__(self):
        self._plan_type: PlanType = PlanType.FREE
        self._limits: PlanLimits = PlanLimits.free_limits()
        self._expires_at: Optional[str] = None
    
    @classmethod
    def get_instance(cls) -> 'SubscriptionManager':
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance."""
        cls._instance = None
    
    @property
    def plan_type(self) -> PlanType:
        """Get current plan type."""
        return self._plan_type
    
    @property
    def limits(self) -> PlanLimits:
        """Get current plan limits."""
        return self._limits
    
    def set_plan(self, plan_type: PlanType) -> None:
        """Set the subscription plan."""
        self._plan_type = plan_type
        if plan_type == PlanType.FREE:
            self._limits = PlanLimits.free_limits()
        elif plan_type == PlanType.PRO:
            self._limits = PlanLimits.pro_limits()
        elif plan_type == PlanType.ENTERPRISE:
            self._limits = PlanLimits.enterprise_limits()
        else:
            self._limits = PlanLimits.free_limits()
    
    def is_feature_allowed(self, feature: str) -> bool:
        """Check if a feature is allowed by current plan."""
        return self._limits.allows_feature(feature)
    
    def can_make_request(self, daily_count: int) -> bool:
        """Check if more requests can be made today."""
        if self._limits.max_requests_per_day < 0:
            return True
        return daily_count < self._limits.max_requests_per_day
    
    async def refresh_status(self) -> None:
        """Refresh subscription status from server."""
        # Stub implementation
        pass


# ============================================================================
# MODULE-LEVEL FUNCTIONS
# ============================================================================

def get_subscription_manager() -> SubscriptionManager:
    """Get the global subscription manager instance."""
    return SubscriptionManager.get_instance()


def get_subscription_info() -> Dict[str, Any]:
    """Get current subscription information."""
    manager = get_subscription_manager()
    return {
        "plan": manager.plan_type.value,
        "limits": {
            "max_requests_per_day": manager.limits.max_requests_per_day,
            "max_tokens_per_month": manager.limits.max_tokens_per_month,
            "features": manager.limits.features,
        },
    }


__all__ = [
    # Classes
    "SubscriptionManager",
    "PlanType",
    "PlanLimits",
    # Functions
    "get_subscription_manager",
    "get_subscription_info",
]
