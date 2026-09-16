"""
UI utilities for sandbox-related output.
"""

import re


def remove_sandbox_violation_tags(text: str) -> str:
    """Remove <sandbox_violations>...</sandbox_violations> blocks from text."""
    return re.sub(r"<sandbox_violations>[\s\S]*?</sandbox_violations>", "", text or "")


# camelCase alias for TS parity
removeSandboxViolationTags = remove_sandbox_violation_tags

