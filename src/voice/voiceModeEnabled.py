"""Voice mode enablement checks."""
import os


# Defensive imports
try:
    from ..services.analytics.growthbook import get_feature_value_cached_may_be_stale
except ImportError:
    def get_feature_value_cached_may_be_stale(flag_name: str, default: bool) -> bool:
        """Stub GrowthBook feature flag checker."""
        return default


try:
    from ..utils.auth import get_cloud_ai_oauth_tokens, is_anthropic_auth_enabled
except ImportError:
    def is_anthropic_auth_enabled() -> bool:
        """Stub auth checker."""
        return False
    
    def get_cloud_ai_oauth_tokens():
        """Stub token getter."""
        return None


def is_voice_growth_book_enabled() -> bool:
    """
    Kill-switch check for voice mode. Returns True unless the
    'tengu_amber_quartz_disabled' GrowthBook flag is flipped on (emergency
    off). Default False means a missing/stale disk cache reads as "not
    killed" — so fresh installs get voice working immediately without
    waiting for GrowthBook init. Use this for deciding whether voice mode
    should be *visible* (e.g., command registration, config UI).
    
    Returns:
        True if voice mode is not disabled by feature flag
    """
    # Check if VOICE_MODE feature is enabled in build
    voice_mode_enabled = os.environ.get('VOICE_MODE', '').lower() in ('1', 'true', 'yes')
    
    if voice_mode_enabled:
        # Positive ternary pattern — see docs/feature-gating.md
        return not get_feature_value_cached_may_be_stale(
            'tengu_amber_quartz_disabled',
            False
        )
    
    return False


def has_voice_auth() -> bool:
    """
    Auth-only check for voice mode. Returns True when the user has a valid
    Anthropic OAuth token. Backed by memoized get_cloud_ai_oauth_tokens —
    first call spawns `security` on macOS (~20-50ms), subsequent calls are
    cache hits. The memoize clears on token refresh (~once/hour), so one
    cold spawn per refresh is expected. Cheap enough for usage-time checks.
    
    Returns:
        True if user has valid Anthropic OAuth access token
    """
    # Voice mode requires Anthropic OAuth — it uses the voice_stream
    # endpoint on claude.ai which is not available with API keys,
    # Bedrock, Vertex, or Foundry.
    if not is_anthropic_auth_enabled():
        return False
    
    # is_anthropic_auth_enabled only checks the auth *provider*, not whether
    # a token exists. Without this check, the voice UI renders but
    # connect_voice_stream fails silently when the user isn't logged in.
    tokens = get_cloud_ai_oauth_tokens()
    return bool(tokens and tokens.get('accessToken'))


def is_voice_mode_enabled() -> bool:
    """
    Full runtime check: auth + GrowthBook kill-switch. Callers: `/voice`
    (voice.ts, voice/index.ts), ConfigTool, VoiceModeNotice — command-time
    paths where a fresh keychain read is acceptable. For React render
    paths use use_voice_enabled() instead (memoizes the auth half).
    
    Returns:
        True if voice mode is fully enabled (auth + feature flag)
    """
    return has_voice_auth() and is_voice_growth_book_enabled()
