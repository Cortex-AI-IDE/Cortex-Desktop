# bun/bundle.py
# Python stub for bun.bundle module
# Provides feature flag functionality

def feature(feature_name: str) -> bool:
    """
    Check if a feature flag is enabled.
    
    There is no feature flag system yet, so this returns False by default.
    
    Args:
        feature_name: Name of the feature flag to check
    
    Returns:
        bool: Whether the feature is enabled (always False in Python)
    """
    return False


__all__ = ['feature']
