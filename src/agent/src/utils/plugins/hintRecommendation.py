"""
Plugin-hint recommendations.

Companion to lspRecommendation.py: where LSP recommendations are triggered
by file edits, plugin hints are triggered by AI agent tools emitting a
`<cortex-code-hint />` tag (detected by the Bash/PowerShell tools).

State persists in GlobalConfig.cortexCodeHints — a show-once record per
plugin and a disabled flag (user picked "don't show again"). Official-
marketplace filtering is hardcoded for v1.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Set, List
from functools import lru_cache

log = logging.getLogger("cortex.agent")

# Constants
MAX_SHOWN_PLUGINS = 100

@dataclass
class CortexCodeHint:
    value: str
    source_command: str

@dataclass
class PluginHintRecommendation:
    plugin_id: str
    plugin_name: str
    marketplace_name: str
    plugin_description: Optional[str] = None
    source_command: str = ""

# Global session state (similar to TypeScript's module-level variable)
_tried_this_session: Set[str] = set()

class HintRecommendationSystem:
    """Main class handling plugin hint recommendations in Python."""
    
    def __init__(self):
        self.tried_this_session = set()
    
    def maybe_record_plugin_hint(self, hint: CortexCodeHint) -> None:
        """
        Pre-store gate called by shell tools when a `type="plugin"` hint is detected.
        Drops the hint if:
        
        - a dialog has already been shown this session
        - user has disabled hints
        - the shown-plugins list has hit the config-growth cap
        - plugin slug doesn't parse as `name@marketplace`
        - marketplace isn't official (hardcoded for v1)
        - plugin is already installed
        - plugin was already shown in a prior session
        
        Synchronous on purpose — shell tools shouldn't await a marketplace lookup
        just to strip a stderr line. The async marketplace-cache check happens
        later in resolve_plugin_hint.
        """
        
        # Feature flag check
        if not self._get_feature_value_cached('tengu_lapis_finch', False):
            return
        
        # Session check
        if self._has_shown_hint_this_session():
            return
        
        # Config state check
        config = self._get_global_config()
        cortex_hints = config.get('cortexCodeHints', {})
        
        if cortex_hints.get('disabled', False):
            return
        
        shown_plugins = cortex_hints.get('plugin', [])
        if len(shown_plugins) >= MAX_SHOWN_PLUGINS:
            return
        
        # Plugin identifier parsing
        plugin_id = hint.value
        parsed = self._parse_plugin_identifier(plugin_id)
        if not parsed or not parsed.get('name') or not parsed.get('marketplace'):
            return
        
        name = parsed['name']
        marketplace = parsed['marketplace']
        
        # Marketplace check
        if not self._is_official_marketplace_name(marketplace):
            return
        
        # Already shown check
        if plugin_id in shown_plugins:
            return
        
        # Already installed check
        if self._is_plugin_installed(plugin_id):
            return
        
        # Policy block check
        if self._is_plugin_blocked_by_policy(plugin_id):
            return
        
        # Bound repeat lookups on the same slug
        if plugin_id in _tried_this_session:
            return
        
        _tried_this_session.add(plugin_id)
        self._set_pending_hint(hint)
    
    async def resolve_plugin_hint(
        self, 
        hint: CortexCodeHint
    ) -> Optional[PluginHintRecommendation]:
        """
        Resolve the pending hint to a renderable recommendation. Runs the async
        marketplace lookup that the sync pre-store gate skipped. Returns None if
        the plugin isn't in the marketplace cache — the hint is discarded.
        """
        
        plugin_id = hint.value
        parsed = self._parse_plugin_identifier(plugin_id)
        if not parsed:
            return None
        
        name = parsed.get('name', '')
        marketplace = parsed.get('marketplace', '')
        
        # Async marketplace lookup
        plugin_data = await self._get_plugin_by_id(plugin_id)
        
        # Analytics logging
        self._log_event('tengu_plugin_hint_detected', {
            '_PROTO_plugin_name': name or '',
            '_PROTO_marketplace_name': marketplace or '',
            'result': 'passed' if plugin_data else 'not_in_cache'
        })
        
        if not plugin_data:
            self._log_for_debugging(
                f"[hintRecommendation] {plugin_id} not found in marketplace cache"
            )
            return None
        
        return PluginHintRecommendation(
            plugin_id=plugin_id,
            plugin_name=plugin_data.get('entry', {}).get('name', ''),
            marketplace_name=marketplace or '',
            plugin_description=plugin_data.get('entry', {}).get('description'),
            source_command=hint.source_command
        )
    
    def mark_hint_plugin_shown(self, plugin_id: str) -> None:
        """
        Record that a prompt for this plugin was surfaced. Called regardless of
        the user's yes/no response — show-once semantics.
        
        Bug Fix #5: Must use callback pattern matching TypeScript saveGlobalConfig
        """
        try:
            from config import saveGlobalConfig
            
            def update_config(current: Dict[str, Any]) -> Dict[str, Any]:
                cortex_hints = current.get('cortexCodeHints', {})
                existing_plugins = cortex_hints.get('plugin', [])
                
                if plugin_id in existing_plugins:
                    return current  # No change needed
                
                return {
                    **current,
                    'cortexCodeHints': {
                        **cortex_hints,
                        'plugin': [*existing_plugins, plugin_id]
                    }
                }
            
            saveGlobalConfig(update_config)
        except ImportError:
            pass
    
    def disable_hint_recommendations(self) -> None:
        """
        Called when the user picks "don't show plugin installation hints again".
        
        Bug Fix #6: Must use callback pattern for atomic updates
        """
        try:
            from config import saveGlobalConfig
            
            def update_config(current: Dict[str, Any]) -> Dict[str, Any]:
                cortex_hints = current.get('cortexCodeHints', {})
                
                if cortex_hints.get('disabled', False):
                    return current  # Already disabled
                
                return {
                    **current,
                    'cortexCodeHints': {**cortex_hints, 'disabled': True}
                }
            
            saveGlobalConfig(update_config)
        except ImportError:
            pass
    
    def reset_hint_recommendation_for_testing(self) -> None:
        """Test-only reset."""
        global _tried_this_session
        _tried_this_session.clear()
    
    # Helper methods (stubs - to be implemented based on your actual backend)
    
    def _get_feature_value_cached(self, feature: str, default: bool) -> bool:
        """Get feature flag value (stub implementation)."""
        # Import from analytics module when available
        try:
            from services.analytics.growthbook import getFeatureValue_CACHED_MAY_BE_STALE
            return getFeatureValue_CACHED_MAY_BE_STALE(feature, default)
        except ImportError:
            return default
    
    def _has_shown_hint_this_session(self) -> bool:
        """Check if hint shown this session."""
        try:
            from cortexCodeHints import hasShownHintThisSession
            return hasShownHintThisSession()
        except ImportError:
            return False
    
    def _get_global_config(self) -> Dict[str, Any]:
        """Get global configuration."""
        try:
            from config import getGlobalConfig
            return getGlobalConfig()
        except ImportError:
            return {'cortexCodeHints': {}}
    
    def _save_global_config(self, config: Dict[str, Any]) -> None:
        """Save global configuration.
        
        Bug Fix #2: saveGlobalConfig uses callback pattern in TypeScript
        We need to handle both patterns
        """
        try:
            from config import saveGlobalConfig
            # TypeScript version: saveGlobalConfig(current => updated)
            # We need to pass a function that receives current state
            def updater(current: Dict[str, Any]) -> Dict[str, Any]:
                return config
            saveGlobalConfig(updater)
        except ImportError:
            pass
    
    def _parse_plugin_identifier(self, plugin_id: str) -> Optional[Dict[str, str]]:
        """
        Parse plugin identifier into name and marketplace.
        Example: "my-plugin@official" -> {"name": "my-plugin", "marketplace": "official"}
        
        Bug Fix #1: Must use rsplit('@', 1) to handle plugin names containing '@'
        Example: "plugin@name@marketplace" -> name="plugin@name", marketplace="marketplace"
        """
        if '@' not in plugin_id:
            return None
        
        # FIX: Use rsplit instead of split to handle @ in plugin names
        parts = plugin_id.rsplit('@', 1)
        if len(parts) != 2 or not parts[0]:
            return None
        
        name, marketplace = parts
        if not marketplace:
            return None
        
        return {'name': name, 'marketplace': marketplace}
    
    def _is_official_marketplace_name(self, marketplace: str) -> bool:
        """Check if marketplace is official."""
        try:
            from pluginIdentifier import isOfficialMarketplaceName
            return isOfficialMarketplaceName(marketplace)
        except ImportError:
            return marketplace == 'official'
    
    def _is_plugin_installed(self, plugin_id: str) -> bool:
        """Check if plugin is already installed."""
        try:
            # Bug Fix #3: This should use installedPluginsManager when available
            # For now, this is a circular dependency - installedPluginsManager needs this file
            # Solution: Import lazily or use dependency injection
            from installedPluginsManager import isPluginInstalled
            return isPluginInstalled(plugin_id)
        except ImportError:
            return False
    
    def _is_plugin_blocked_by_policy(self, plugin_id: str) -> bool:
        """Check if plugin is blocked by policy."""
        try:
            from pluginPolicy import isPluginBlockedByPolicy
            return isPluginBlockedByPolicy(plugin_id)
        except ImportError:
            return False
    
    def _set_pending_hint(self, hint: CortexCodeHint) -> None:
        """Set pending hint for later resolution."""
        try:
            from cortexCodeHints import setPendingHint
            setPendingHint(hint)
        except ImportError:
            pass
    
    async def _get_plugin_by_id(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """
        Get plugin data from marketplace cache (async).
        
        Bug Fix #4: Must handle case where plugin not found gracefully
        """
        try:
            from marketplaceManager import getPluginById
            return await getPluginById(plugin_id)
        except ImportError:
            return None
    
    def _log_event(self, event_name: str, metadata: Dict[str, Any]) -> None:
        """Log analytics event - disabled."""
        pass
    
    def _log_for_debugging(self, message: str) -> None:
        """Log debug message."""
        try:
            from debug import logForDebugging
            logForDebugging(message)
        except ImportError:
            pass

# Singleton instance for convenience
hint_system = HintRecommendationSystem()

# Public API functions (mirroring TypeScript exports)
def maybe_record_plugin_hint(hint: CortexCodeHint) -> None:
    """Public wrapper for synchronous hint recording."""
    hint_system.maybe_record_plugin_hint(hint)

async def resolve_plugin_hint(hint: CortexCodeHint) -> Optional[PluginHintRecommendation]:
    """Public wrapper for async hint resolution."""
    return await hint_system.resolve_plugin_hint(hint)

def mark_hint_plugin_shown(plugin_id: str) -> None:
    """Public wrapper for marking plugin shown."""
    hint_system.mark_hint_plugin_shown(plugin_id)

def disable_hint_recommendations() -> None:
    """Public wrapper for disabling recommendations."""
    hint_system.disable_hint_recommendations()

def _reset_hint_recommendation_for_testing() -> None:
    """Public wrapper for test reset."""
    hint_system.reset_hint_recommendation_for_testing()

# Example usage
if __name__ == "__main__":
    # Example 1: Synchronous hint recording
    hint = CortexCodeHint(
        value="python-linter@official",
        source_command="python my_script.py"
    )
    maybe_record_plugin_hint(hint)
    
    # Example 2: Async resolution
    async def example_resolution():
        recommendation = await resolve_plugin_hint(hint)
        if recommendation:
            log.debug(f"Found plugin: {recommendation.plugin_name}")
        else:
            log.debug("Plugin not found in marketplace")
    
    asyncio.run(example_resolution())
    
    # Example 3: Mark plugin as shown
    mark_hint_plugin_shown("python-linter@official")
    
    # Example 4: Disable recommendations
    disable_hint_recommendations()
