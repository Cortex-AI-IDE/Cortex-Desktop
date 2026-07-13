"""
Denial tracking infrastructure for autonomous AI agent mode in Cortex IDE.

Tracks consecutive and total denials to determine when the AI agent should
fallback from autonomous mode to interactive prompting. This prevents frustrating
user experiences where the AI keeps trying actions that get repeatedly denied.

Use Cases in Cortex IDE:
- AI tries to edit files but user keeps denying → fallback to plan mode
- AI attempts shell commands but they're blocked → switch to manual approval
- Autonomous coding session keeps hitting restrictions → prompt user for guidance

Multi-LLM Support: Works with all providers (Anthropic, OpenAI, Gemini, DeepSeek,
Mistral, Groq, SiliconFlow) as it's provider-agnostic safety logic.

Example:
    >>> from denial_tracking import DenialTracker
    >>> tracker = DenialTracker()
    >>> tracker.record_denial()
    >>> tracker.record_denial()
    >>> tracker.record_denial()
    >>> tracker.should_fallback_to_prompting()
    True
    >>> tracker.record_success()
    >>> tracker.consecutive_denials
    0
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DenialTrackingState:
    """State container for tracking AI action denials."""
    consecutive_denials: int = 0
    total_denials: int = 0


# Denial limits before forcing fallback to interactive mode
# These can be customized in Cortex IDE settings
DENIAL_LIMITS = {
    'max_consecutive': 3,   # Max consecutive denials before fallback
    'max_total': 20,        # Max total denials in session before fallback
}


class DenialTracker:
    """
    Tracks denial patterns for AI agent autonomous mode in Cortex IDE.
    
    When the AI agent's actions are repeatedly denied (file edits, shell commands, etc.),
    this tracker determines when to automatically switch from autonomous mode to
    interactive mode where the user provides guidance.
    
    Attributes:
        state: Current denial tracking state
        max_consecutive: Maximum consecutive denials before fallback (default: 3)
        max_total: Maximum total denials before fallback (default: 20)
    
    Example:
        >>> tracker = DenialTracker(max_consecutive=3, max_total=20)
        >>> 
        >>> # AI tries to delete a file, user denies
        >>> tracker.record_denial()
        >>> tracker.consecutive_denials
        1
        >>> 
        >>> # AI tries to run shell command, user denies
        >>> tracker.record_denial()
        >>> tracker.consecutive_denials
        2
        >>> 
        >>> # AI tries another action, user denies
        >>> tracker.record_denial()
        >>> tracker.should_fallback_to_prompting()
        True
        >>> 
        >>> # User approves an action, reset consecutive counter
        >>> tracker.record_success()
        >>> tracker.consecutive_denials
        0
        >>> tracker.total_denials
        3
    """
    
    def __init__(
        self,
        max_consecutive: int = DENIAL_LIMITS['max_consecutive'],
        max_total: int = DENIAL_LIMITS['max_total']
    ):
        """
        Initialize denial tracker for Cortex IDE autonomous mode.
        
        Args:
            max_consecutive: Max consecutive denials before forcing fallback
            max_total: Max total denials in session before forcing fallback
        """
        self.state = DenialTrackingState()
        self.max_consecutive = max_consecutive
        self.max_total = max_total
    
    @property
    def consecutive_denials(self) -> int:
        """Get current consecutive denial count."""
        return self.state.consecutive_denials
    
    @property
    def total_denials(self) -> int:
        """Get total denial count for this session."""
        return self.state.total_denials
    
    def record_denial(self) -> None:
        """
        Record that an AI action was denied by the user.
        
        Increments both consecutive and total denial counters.
        Call this when the user denies a file edit, shell command, or other action.
        """
        self.state.consecutive_denials += 1
        self.state.total_denials += 1
    
    def record_success(self) -> None:
        """
        Record that an AI action was approved by the user.
        
        Resets the consecutive denial counter to zero.
        This indicates the AI is back on track and not in a denial loop.
        """
        if self.state.consecutive_denials > 0:
            self.state.consecutive_denials = 0
    
    def should_fallback_to_prompting(self) -> bool:
        """
        Check if the AI should fallback from autonomous to interactive mode.
        
        Returns True if:
        - Consecutive denials >= max_consecutive (default: 3)
        - Total denials >= max_total (default: 20)
        
        This prevents frustrating UX where the AI keeps trying and failing.
        
        Returns:
            True if should switch to interactive/prompting mode
        """
        return (
            self.state.consecutive_denials >= self.max_consecutive or
            self.state.total_denials >= self.max_total
        )
    
    def get_denial_stats(self) -> dict:
        """
        Get current denial statistics for monitoring/UI display.
        
        Returns:
            Dictionary with denial statistics for Cortex IDE UI
        """
        return {
            'consecutive_denials': self.state.consecutive_denials,
            'total_denials': self.state.total_denials,
            'max_consecutive': self.max_consecutive,
            'max_total': self.max_total,
            'should_fallback': self.should_fallback_to_prompting(),
            'consecutive_remaining': max(0, self.max_consecutive - self.state.consecutive_denials),
            'total_remaining': max(0, self.max_total - self.state.total_denials),
        }
    
    def reset(self) -> None:
        """Reset all denial counters. Call when starting a new session or after user intervention."""
        self.state = DenialTrackingState()
    
    def __repr__(self) -> str:
        return (
            f"DenialTracker(consecutive={self.state.consecutive_denials}, "
            f"total={self.state.total_denials}, "
            f"should_fallback={self.should_fallback_to_prompting()})"
        )


# Legacy functional API for compatibility with simple use cases

def create_denial_tracking_state() -> DenialTrackingState:
    """
    Create initial denial tracking state.
    
    Returns:
        Fresh DenialTrackingState with zero counters
    """
    return DenialTrackingState()


def record_denial(state: DenialTrackingState) -> DenialTrackingState:
    """
    Record a denial in the tracking state (immutable version).
    
    Args:
        state: Current denial tracking state
        
    Returns:
        New state with incremented counters
    """
    return DenialTrackingState(
        consecutive_denials=state.consecutive_denials + 1,
        total_denials=state.total_denials + 1,
    )


def record_success(state: DenialTrackingState) -> DenialTrackingState:
    """
    Record a success (approval) in the tracking state (immutable version).
    
    Args:
        state: Current denial tracking state
        
    Returns:
        New state with consecutive_denials reset to 0
    """
    if state.consecutive_denials == 0:
        return state  # No change needed
    
    return DenialTrackingState(
        consecutive_denials=0,
        total_denials=state.total_denials,
    )


def should_fallback_to_prompting(
    state: DenialTrackingState,
    max_consecutive: int = DENIAL_LIMITS['max_consecutive'],
    max_total: int = DENIAL_LIMITS['max_total'],
) -> bool:
    """
    Check if should fallback from autonomous to interactive mode.
    
    Args:
        state: Current denial tracking state
        max_consecutive: Max consecutive denials before fallback
        max_total: Max total denials before fallback
        
    Returns:
        True if should switch to prompting mode
    """
    return (
        state.consecutive_denials >= max_consecutive or
        state.total_denials >= max_total
    )


def get_denial_limits() -> dict:
    """
    Get current denial limit configuration.
    
    Returns:
        Dictionary with max_consecutive and max_total limits
    """
    return DENIAL_LIMITS.copy()


def update_denial_limits(
    max_consecutive: Optional[int] = None,
    max_total: Optional[int] = None,
) -> dict:
    """
    Update denial limits (for customizing Cortex IDE behavior).
    
    Args:
        max_consecutive: New max consecutive denials (optional)
        max_total: New max total denials (optional)
        
    Returns:
        Updated denial limits dictionary
    """
    global DENIAL_LIMITS
    
    if max_consecutive is not None:
        DENIAL_LIMITS['max_consecutive'] = max_consecutive
    if max_total is not None:
        DENIAL_LIMITS['max_total'] = max_total
    
    return DENIAL_LIMITS.copy()


# Exported symbols
__all__ = [
    # Data structures
    'DenialTrackingState',
    'DENIAL_LIMITS',
    
    # OOP API (recommended for Cortex IDE)
    'DenialTracker',
    
    # Functional API (legacy/simple use cases)
    'create_denial_tracking_state',
    'record_denial',
    'record_success',
    'should_fallback_to_prompting',
    'get_denial_limits',
    'update_denial_limits',
]
