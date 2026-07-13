# ------------------------------------------------------------
# userPromptKeywords.py
# Python conversion of utils/userPromptKeywords.ts
#
# Checks if user input matches negative or keep-going keyword patterns.
# Used by processTextPrompt for analytics/event logging.
# ------------------------------------------------------------

import re
from typing import Final

__all__ = [
    "matches_negative_keyword",
    "matches_keep_going_keyword",
]

# Compiled once at module load
_NEGATIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b("
    r"wtf|wth|ffs|omfg|"
    r"shit(ty|tiest)?|dumbass|horrible|awful|"
    r"piss(ed|ing)? off|"
    r"piece of (shit|crap|junk)|"
    r"what the (fuck|hell)|"
    r"fucking? (broken|useless|terrible|awful|horrible)|"
    r"fuck you|screw (this|you)|"
    r"so frustrating|this sucks|"
    r"damn it"
    r")\b",
    flags=re.IGNORECASE,
)

_KEEP_GOING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(keep going|go on)\b",
    flags=re.IGNORECASE,
)


def matches_negative_keyword(input_str: str) -> bool:
    """
    Check if input matches negative/frustration keyword patterns.

    Mirrors TS matchesNegativeKeyword() exactly.
    Uses case-insensitive matching.
    """
    return bool(_NEGATIVE_PATTERN.search(input_str.lower()))


def matches_keep_going_keyword(input_str: str) -> bool:
    """
    Check if input matches keep-going/continuation keyword patterns.

    Mirrors TS matchesKeepGoingKeyword() exactly:
    - "continue" only matches if it's the ENTIRE prompt
    - "keep going" or "go on" match anywhere
    """
    trimmed = input_str.lower().strip()

    if trimmed == "continue":
        return True

    return bool(_KEEP_GOING_PATTERN.search(trimmed))
