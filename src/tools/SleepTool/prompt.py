"""
Python conversion of SleepTool/prompt.ts

Sleep tool for pausing execution with resource efficiency.
- Non-blocking sleep that doesn't hold shell processes
- Periodic tick check-ins for work detection
- Cost-aware with cache expiration guidance
"""

# ============================================================================
# Defensive Imports
# ============================================================================

try:
    from constants.xml import TICK_TAG
except ImportError:
    TICK_TAG = 'tick'  # Fallback


# ============================================================================
# Constants
# ============================================================================

SLEEP_TOOL_NAME = 'Sleep'

DESCRIPTION = 'Wait for a specified duration'

SLEEP_TOOL_PROMPT = f"""Wait for a specified duration. The user can interrupt the sleep at any time.

Use this when the user tells you to sleep or rest, when you have nothing to do, or when you're waiting for something.

You may receive <{TICK_TAG}> prompts — these are periodic check-ins. Look for useful work to do before sleeping.

You can call this concurrently with other tools — it won't interfere with them.

Prefer this over `Bash(sleep ...)` — it doesn't hold a shell process.

Each wake-up costs an API call, but the prompt cache expires after 5 minutes of inactivity — balance accordingly."""
