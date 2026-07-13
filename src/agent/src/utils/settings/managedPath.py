"""
Managed settings path utilities.

Provides the path to the managed settings directory based on the current platform.
"""

import os
import platform
from functools import lru_cache


@lru_cache(maxsize=1)
def getManagedFilePath() -> str:
    """
    Get the path to the managed settings directory based on the current platform.
    
    Returns:
        Platform-specific managed settings path
    """
    # Allow override for testing/demos
    managed_path = os.environ.get('CORTEX_MANAGED_SETTINGS_PATH')
    if managed_path:
        return managed_path
    
    system = platform.system()
    if system == 'Darwin':  # macOS
        return '/Library/Application Support/Cortex'
    elif system == 'Windows':
        return 'C:\\Program Files\\Cortex'
    else:  # Linux and others
        return '/etc/cortex'


@lru_cache(maxsize=1)
def getManagedSettingsDropInDir() -> str:
    """
    Get the path to the managed-settings.d/ drop-in directory.
    
    Returns:
        Path to drop-in directory
    """
    return os.path.join(getManagedFilePath(), 'managed-settings.d')
