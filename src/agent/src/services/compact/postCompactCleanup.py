"""
services/compact/postCompactCleanup.py
Python conversion of services/compact/postCompactCleanup.ts (78 lines)

Post-compaction cleanup utilities for clearing caches and tracking state
invalidated by context compaction.
"""

from typing import Optional


def run_post_compact_cleanup(query_source: Optional[str] = None) -> None:
    """
    Run cleanup of caches and tracking state after compaction.
    
    Call this after both auto-compact and manual /compact to free memory
    held by tracking structures that are invalidated by compaction.
    
    Note: We intentionally do NOT clear invoked skill content here.
    Skill content must survive across multiple compactions.
    
    Args:
        query_source: Query source to determine if this is main-thread compact.
                     Subagents run in same process and share module-level state,
                     so we only reset main-thread state for main-thread compacts.
    """
    # Determine if this is a main-thread compact
    # Subagents (agent:*) share module-level state with main thread
    is_main_thread_compact = (
        query_source is None or
        query_source.startswith('repl_main_thread') or
        query_source == 'sdk'
    )
    
    # Reset microcompact state
    from .microCompact import reset_microcompact_state
    reset_microcompact_state()
    
    # Only reset main-thread module-level state for main-thread compacts
    if is_main_thread_compact:
        # Clear user context cache (wrapper around getMemoryFiles)
        _clear_user_context_cache()
        _reset_get_memory_files_cache()
    
    # Clear system prompt sections
    _clear_system_prompt_sections()
    
    # Clear classifier approvals
    _clear_classifier_approvals()
    
    # Clear speculative checks
    _clear_speculative_checks()
    
    # Clear beta tracing state
    _clear_beta_tracing_state()
    
    # Clear session messages cache
    _clear_session_messages_cache()


def _clear_user_context_cache() -> None:
    """Clear getUserContext memoization cache"""
    # Would clear the memoized cache in real implementation
    pass


def _reset_get_memory_files_cache(reason: str = 'compact') -> None:
    """Reset getMemoryFiles one-shot hook cache"""
    # Would reset the cache in real implementation
    pass


def _clear_system_prompt_sections() -> None:
    """Clear system prompt sections cache"""
    # Would clear in real implementation
    pass


def _clear_classifier_approvals() -> None:
    """Clear classifier approval cache"""
    # Would clear in real implementation
    pass


def _clear_speculative_checks() -> None:
    """Clear speculative bash permission checks"""
    # Would clear in real implementation
    pass


def _clear_beta_tracing_state() -> None:
    """Clear beta session tracing state"""
    # Would clear in real implementation
    pass


def _clear_session_messages_cache() -> None:
    """Clear session messages storage cache"""
    # Would clear in real implementation
    pass


__all__ = ['run_post_compact_cleanup']
