# bun/bundle.py
# Python stub for bun.bundle module
# Provides feature flag functionality (normally from Bun runtime)

def feature(feature_name: str) -> bool:
    """
    Check if a feature flag is enabled.
    
    In the TypeScript/Bun version, this reads from GrowthBook feature flags.
    In Python, we return False by default (no feature flags system).
    
    Args:
        feature_name: Name of the feature flag to check
    
    Returns:
        bool: Whether the feature is enabled (always False in Python)
    """
    return False


__all__ = ['feature']
