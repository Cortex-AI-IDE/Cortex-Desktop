"""Bootstrap module for Cortex IDE."""
from .state import *

__all__ = [
    'getSessionId',
    'regenerateSessionId',
    'get_project_root',
    'set_project_root',
    'get_original_cwd',
    'set_original_cwd',
    'getAdditionalDirectoriesForCortexMd',
    'setAdditionalDirectoriesForCortexMd',
]
