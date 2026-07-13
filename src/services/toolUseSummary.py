"""
Tool use summary utilities for Cortex IDE.
"""

from typing import Any, Dict, List, Optional


def create_tool_use_summary(tool_uses: List[Dict]) -> Dict:
    """Create a summary of tool uses."""
    return {
        'total': len(tool_uses),
        'by_type': {},
    }


__all__ = ['create_tool_use_summary']
