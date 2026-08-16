"""
Cortex IDE API Communication Layer

Provides clean, simplified communication with logic-practice.com backend.

Modules:
- api_client: Main API client for authentication & LLM routing
- auth: JWT token management & API key handling
- subscription: Subscription status & access control
- usage: Usage tracking & reporting
"""

from .api_client import (
    CortexAPIClient,
    APIConfig,
    UserProfile,
    SubscriptionStatus,
    LLMResponse,
    get_api_client,
    cleanup_api_client,
    AuthenticationError,
    SubscriptionError,
    LLMError,
    UsageError,
    NetworkError,
)

from .auth import (
    AuthManager,
    AuthToken,
    APIKeyManager,
    get_auth_manager,
    get_api_key_manager,
)

from .subscription import (
    SubscriptionManager,
    PlanType,
    PlanLimits,
    get_subscription_manager,
)

from .usage import (
    UsageTracker,
    UsageRecord,
    DailyUsage,
    get_usage_tracker,
)

__all__ = [
    # API Client
    'CortexAPIClient',
    'APIConfig',
    'UserProfile',
    'SubscriptionStatus',
    'LLMResponse',
    'get_api_client',
    'cleanup_api_client',
    
    # Exceptions
    'AuthenticationError',
    'SubscriptionError',
    'LLMError',
    'UsageError',
    'NetworkError',
    
    # Auth
    'AuthManager',
    'AuthToken',
    'APIKeyManager',
    'get_auth_manager',
    'get_api_key_manager',
    
    # Subscription
    'SubscriptionManager',
    'PlanType',
    'PlanLimits',
    'get_subscription_manager',
    
    # Usage
    'UsageTracker',
    'UsageRecord',
    'DailyUsage',
    'get_usage_tracker',
]
