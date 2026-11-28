"""
Plugin-only policy enforcement for customization surfaces.

Check whether a customization surface is locked to plugin-only sources
by the managed strictPluginOnlyCustomization policy.
"""

from typing import List, Optional, Set


# Type definition for customization surfaces
CustomizationSurface = str  # 'skills', 'commands', 'hooks', 'agents', etc.


def isRestrictedToPluginOnly(surface: CustomizationSurface) -> bool:
    """
    Check whether a customization surface is locked to plugin-only sources.

    "Locked" means user-level and project-level sources are skipped for that
    surface; managed (policySettings) and plugin-provided sources always load.

    Args:
        surface: The customization surface to check.

    Returns:
        True if the surface is restricted to plugin-only sources.
    """
    # In full implementation, would read from policy settings
    # For now, default to False (no restrictions)
    # TODO: Implement actual policy reading
    return False


# Sources that bypass strictPluginOnlyCustomization
# Admin-trusted because:
#   plugin, gated separately by strictKnownMarketplaces
#   policySettings, from managed settings, admin-controlled by definition
#   built-in / builtin / bundled, ship with Cortex, not user-authored
ADMIN_TRUSTED_SOURCES: Set[str] = {
    'plugin',
    'policySettings',
    'built-in',
    'builtin',
    'bundled',
}


def isSourceAdminTrusted(source: Optional[str]) -> bool:
    """True when a customization's source is admin-trusted.

    Gates frontmatter-hook registration and similar per-item checks, used as
    ``allowed = not isRestrictedToPluginOnly(surface) or
    isSourceAdminTrusted(item.source)``.
    """
    return source is not None and source in ADMIN_TRUSTED_SOURCES
