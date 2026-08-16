"""
Plugin dependency resolution for Cortex AI IDE.

Pure functions for resolving plugin dependencies â€” no I/O.

Semantics are `apt`-style: a dependency is a *presence guarantee*, not a
module graph. Plugin A depending on Plugin B means "B's namespaced
components (MCP servers, commands, agents) must be available when A runs."

Two entry points:
  - resolve_dependency_closure() â€” install-time DFS walk, cycle detection
  - verify_and_demote() â€” load-time fixed-point check, demotes plugins with
    unsatisfied deps (session-local, does NOT write settings)

Multi-LLM Support: Works with all providers as it's provider-agnostic
dependency resolution logic.
"""

from typing import Any, TypedDict, Union


# ============================================================================
# Type Definitions
# ============================================================================

class DependencyLookupResult(TypedDict, total=False):
    """
    Minimal shape the resolver needs from a marketplace lookup.
    Keeping this narrow means the resolver stays testable without
    constructing full PluginMarketplaceEntry objects.
    """
    # Entries may be bare names; qualify_dependency normalizes them.
    dependencies: list[str]


class ResolutionSuccess(TypedDict):
    """Successful dependency resolution."""
    ok: bool
    closure: list[str]


class ResolutionCycleError(TypedDict):
    """Cycle detected in dependency graph."""
    ok: bool
    reason: str
    chain: list[str]


class ResolutionNotFoundError(TypedDict):
    """Dependency not found in marketplace."""
    ok: bool
    reason: str
    missing: str
    required_by: str


class ResolutionCrossMarketplaceError(TypedDict):
    """Cross-marketplace dependency blocked."""
    ok: bool
    reason: str
    dependency: str
    required_by: str


ResolutionResult = Union[
    ResolutionSuccess,
    ResolutionCycleError,
    ResolutionNotFoundError,
    ResolutionCrossMarketplaceError,
]


class LoadedPlugin(TypedDict, total=False):
    """Minimal shape of a loaded plugin for verify_and_demote."""
    source: str
    name: str
    enabled: bool
    manifest: dict[str, Any]


class PluginError(TypedDict):
    """Plugin error for dependency issues."""
    type: str
    source: str
    plugin: str
    dependency: str
    reason: str


# ============================================================================
# Constants
# ============================================================================

# Synthetic marketplace sentinel for `--plugin-dir` plugins (pluginLoader.ts
# sets `source = "{name}@inline"`). Not a real marketplace â€” bare deps from
# these plugins cannot meaningfully inherit it.
INLINE_MARKETPLACE = 'inline'


# ============================================================================
# Dependency Qualification
# ============================================================================

def parse_plugin_identifier(plugin_id: str) -> dict[str, str | None]:
    """
    Parse a plugin ID into name and marketplace components.
    
    Note: This is duplicated from pluginInstallationHelpers to keep this
    module self-contained and testable without imports.
    
    Args:
        plugin_id: Plugin ID in "name@marketplace" format
        
    Returns:
        Dict with 'name' and 'marketplace' keys
        
    Example:
        >>> parse_plugin_identifier("my-plugin@marketplace")
        {'name': 'my-plugin', 'marketplace': 'marketplace'}
    """
    if '@' not in plugin_id:
        return {'name': plugin_id, 'marketplace': None}
    
    parts = plugin_id.rsplit('@', 1)
    if len(parts) == 2 and parts[0]:
        return {'name': parts[0], 'marketplace': parts[1]}
    
    return {'name': plugin_id, 'marketplace': None}


def qualify_dependency(dep: str, declaring_plugin_id: str) -> str:
    """
    Normalize a dependency reference to fully-qualified "name@marketplace" form.
    
    Bare names (no @) inherit the marketplace of the plugin declaring them â€”
    cross-marketplace deps are blocked anyway, so the @-suffix is boilerplate
    in the common case.
    
    EXCEPTION: if the declaring plugin is @inline (loaded via --plugin-dir),
    bare deps are returned unchanged. `inline` is a synthetic sentinel, not a
    real marketplace â€” fabricating "dep@inline" would never match anything.
    verify_and_demote handles bare deps via name-only matching.
    
    Args:
        dep: Dependency reference (may be bare name or "name@marketplace")
        declaring_plugin_id: The plugin declaring this dependency
        
    Returns:
        Fully-qualified plugin ID, or bare name if from @inline plugin
        
    Example:
        >>> qualify_dependency("dep", "plugin@marketplace")
        'dep@marketplace'
        >>> qualify_dependency("dep@other", "plugin@marketplace")
        'dep@other'
        >>> qualify_dependency("dep", "plugin@inline")
        'dep'
    """
    if parse_plugin_identifier(dep)['marketplace']:
        return dep
    
    mkt = parse_plugin_identifier(declaring_plugin_id)['marketplace']
    if not mkt or mkt == INLINE_MARKETPLACE:
        return dep
    
    return f"{dep}@{mkt}"


# ============================================================================
# Dependency Closure Resolution (Install-Time)
# ============================================================================

async def resolve_dependency_closure(
    root_id: str,
    lookup: Any,
    already_enabled: set[str],
    allowed_cross_marketplaces: set[str] | None = None,
) -> ResolutionResult:
    """
    Walk the transitive dependency closure of `root_id` via DFS.
    
    The returned `closure` ALWAYS contains `root_id`, plus every transitive
    dependency that is NOT in `already_enabled`. Already-enabled deps are
    skipped (not recursed into) â€” this avoids surprise settings writes when a
    dep is already installed at a different scope. The root is never skipped,
    even if already enabled, so re-installing a plugin always re-caches it.
    
    Cross-marketplace dependencies are BLOCKED by default: a plugin in
    marketplace A cannot auto-install a plugin from marketplace B. This is
    a security boundary â€” installing from a trusted marketplace shouldn't
    silently pull from an untrusted one. Two escapes: (1) install the
    cross-mkt dep yourself first (already-enabled deps are skipped, so the
    closure won't touch it), or (2) the ROOT marketplace's
    `allow_cross_marketplace_dependencies_on` allowlist â€” only the root's list
    applies for the whole walk (no transitive trust: if A allows B, B's
    plugin depending on C is still blocked unless A also allows C).
    
    Args:
        root_id: Root plugin to resolve from (format: "name@marketplace")
        lookup: Async lookup function returning `{dependencies}` or `None` if not found
        already_enabled: Plugin IDs to skip (deps only, root is never skipped)
        allowed_cross_marketplaces: Marketplace names the root trusts for
            auto-install (from the root marketplace's manifest)
            
    Returns:
        Closure to install, or a cycle/not-found/cross-marketplace error
        
    Example:
        >>> async def lookup(pid):
        ...     if pid == "a@m":
        ...         return {"dependencies": ["b@m"]}
        ...     elif pid == "b@m":
        ...         return {"dependencies": []}
        ...     return None
        >>> await resolve_dependency_closure("a@m", lookup, set(), {"m"})
        {'ok': True, 'closure': ['b@m', 'a@m']}
    """
    if allowed_cross_marketplaces is None:
        allowed_cross_marketplaces = set()
    
    root_marketplace = parse_plugin_identifier(root_id)['marketplace']
    closure: list[str] = []
    visited: set[str] = set()
    stack: list[str] = []

    async def walk(id: str, required_by: str) -> ResolutionResult | None:
        # Skip already-enabled DEPENDENCIES (avoids surprise settings writes),
        # but NEVER skip the root: installing an already-enabled plugin must
        # still cache/register it. Without this guard, re-installing a plugin
        # that's in settings but missing from disk (e.g., cache cleared,
        # installed_plugins.json stale) would return an empty closure and
        # `cache_and_register_plugin` would never fire â€” user sees
        # "âœ” Successfully installed" but nothing materializes.
        if id != root_id and id in already_enabled:
            return None
        
        # Security: block auto-install across marketplace boundaries. Runs AFTER
        # the alreadyEnabled check â€” if the user manually installed a cross-mkt
        # dep, it's in alreadyEnabled and we never reach this.
        id_marketplace = parse_plugin_identifier(id)['marketplace']
        if (
            id_marketplace != root_marketplace
            and not (id_marketplace and id_marketplace in allowed_cross_marketplaces)
        ):
            return {
                'ok': False,
                'reason': 'cross-marketplace',
                'dependency': id,
                'required_by': required_by,
            }
        
        if id in stack:
            return {
                'ok': False,
                'reason': 'cycle',
                'chain': [*stack, id],
            }
        
        if id in visited:
            return None
        
        visited.add(id)

        entry = await lookup(id)
        if entry is None:
            return {
                'ok': False,
                'reason': 'not-found',
                'missing': id,
                'required_by': required_by,
            }

        stack.append(id)
        for raw_dep in entry.get('dependencies') or []:
            dep = qualify_dependency(raw_dep, id)
            err = await walk(dep, id)
            if err:
                return err
        stack.pop()

        closure.append(id)
        return None

    err = await walk(root_id, root_id)
    if err:
        return err
    
    return {'ok': True, 'closure': closure}


# ============================================================================
# Load-Time Dependency Verification
# ============================================================================

def verify_and_demote(plugins: list[LoadedPlugin]) -> dict[str, Any]:
    """
    Load-time safety net: for each enabled plugin, verify all manifest
    dependencies are also in the enabled set. Demote any that fail.
    
    Fixed-point loop: demoting plugin A may break plugin B that depends on A,
    so we iterate until nothing changes.
    
    The `reason` field distinguishes:
      - `'not-enabled'` â€” dep exists in the loaded set but is disabled
      - `'not-found'` â€” dep is entirely absent (not in any marketplace)
    
    Does NOT mutate input. Returns the set of plugin IDs (sources) to demote.
    
    Args:
        plugins: All loaded plugins (enabled + disabled)
        
    Returns:
        Dict with 'demoted' (set of plugin IDs) and 'errors' (list of PluginError)
        
    Example:
        >>> plugins = [
        ...     {"source": "a@m", "name": "a", "enabled": True, "manifest": {"dependencies": ["b@m"]}},
        ...     {"source": "b@m", "name": "b", "enabled": False, "manifest": {}},
        ... ]
        >>> result = verify_and_demote(plugins)
        >>> result['demoted']
        {'a@m'}
    """
    known = set(p['source'] for p in plugins)
    enabled = set(p['source'] for p in plugins if p.get('enabled', False))
    
    # Name-only indexes for bare deps from --plugin-dir (@inline) plugins:
    # the real marketplace is unknown, so match "B" against any enabled "B@*".
    # enabledByName is a multiset: if B@epic AND B@other are both enabled,
    # demoting one mustn't make "B" disappear from the index.
    known_by_name = set(
        parse_plugin_identifier(p['source'])['name'] for p in plugins
    )
    enabled_by_name: dict[str, int] = {}
    for id in enabled:
        n = parse_plugin_identifier(id)['name']
        enabled_by_name[n] = enabled_by_name.get(n, 0) + 1
    
    errors: list[PluginError] = []

    changed = True
    while changed:
        changed = False
        for p in plugins:
            if p['source'] not in enabled:
                continue
            
            for raw_dep in p.get('manifest', {}).get('dependencies') or []:
                dep = qualify_dependency(raw_dep, p['source'])
                
                # Bare dep â† @inline plugin: match by name only (see enabledByName)
                is_bare = parse_plugin_identifier(dep)['marketplace'] is None
                satisfied = (
                    enabled_by_name.get(dep, 0) > 0
                    if is_bare
                    else dep in enabled
                )
                
                if not satisfied:
                    enabled.discard(p['source'])
                    count = enabled_by_name.get(p['name'], 0)
                    if count <= 1:
                        enabled_by_name.pop(p['name'], None)
                    else:
                        enabled_by_name[p['name']] = count - 1
                    
                    errors.append({
                        'type': 'dependency-unsatisfied',
                        'source': p['source'],
                        'plugin': p['name'],
                        'dependency': dep,
                        'reason': (
                            'not-enabled'
                            if (known_by_name.has(dep) if is_bare else dep in known)
                            else 'not-found'
                        ),
                    })
                    changed = True
                    break

    demoted = set(
        p['source']
        for p in plugins
        if p.get('enabled', False) and p['source'] not in enabled
    )
    
    return {'demoted': demoted, 'errors': errors}


# ============================================================================
# Reverse Dependency Detection
# ============================================================================

def find_reverse_dependents(
    plugin_id: str,
    plugins: list[LoadedPlugin],
) -> list[str]:
    """
    Find all enabled plugins that declare `plugin_id` as a dependency.
    Used to warn on uninstall/disable ("required by: X, Y").
    
    Args:
        plugin_id: The plugin being removed/disabled
        plugins: All loaded plugins (only enabled ones are checked)
        
    Returns:
        Names of plugins that will break if `plugin_id` goes away
        
    Example:
        >>> plugins = [
        ...     {"source": "a@m", "name": "a", "enabled": True, "manifest": {"dependencies": ["b@m"]}},
        ...     {"source": "b@m", "name": "b", "enabled": True, "manifest": {}},
        ... ]
        >>> find_reverse_dependents("b@m", plugins)
        ['a']
    """
    target_name = parse_plugin_identifier(plugin_id)['name']
    
    result = []
    for p in plugins:
        if not p.get('enabled', False):
            continue
        if p['source'] == plugin_id:
            continue
        
        for d in p.get('manifest', {}).get('dependencies') or []:
            qualified = qualify_dependency(d, p['source'])
            # Bare dep (from @inline plugin): match by name only
            if parse_plugin_identifier(qualified)['marketplace']:
                if qualified == plugin_id:
                    result.append(p['name'])
                    break
            else:
                if qualified == target_name:
                    result.append(p['name'])
                    break
    
    return result


# ============================================================================
# Formatting Utilities
# ============================================================================

def format_dependency_count_suffix(installed_deps: list[str]) -> str:
    """
    Format the "(+ N dependencies)" suffix for install success messages.
    Returns empty string when `installed_deps` is empty.
    
    Args:
        installed_deps: List of installed dependency plugin IDs
        
    Returns:
        Formatted suffix string
        
    Example:
        >>> format_dependency_count_suffix(["a@m", "b@m", "c@m"])
        ' (+ 3 dependencies)'
        >>> format_dependency_count_suffix(["a@m"])
        ' (+ 1 dependency)'
        >>> format_dependency_count_suffix([])
        ''
    """
    if not installed_deps:
        return ''
    
    n = len(installed_deps)
    return f" (+ {n} {'dependency' if n == 1 else 'dependencies'})"


def format_reverse_dependents_suffix(rdeps: list[str] | None) -> str:
    """
    Format the "warning: required by X, Y" suffix for uninstall/disable
    results. Uses em-dash style for result messages (not the middot style
    used in the notification UI). Returns empty string when no dependents.
    
    Args:
        rdeps: List of reverse dependent plugin names
        
    Returns:
        Formatted warning suffix
        
    Example:
        >>> format_reverse_dependents_suffix(["plugin-a", "plugin-b"])
        ' â€” warning: required by plugin-a, plugin-b'
        >>> format_reverse_dependents_suffix([])
        ''
    """
    if not rdeps or len(rdeps) == 0:
        return ''
    
    return f" â€” warning: required by {', '.join(rdeps)}"


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'DependencyLookupResult',
    'ResolutionResult',
    'ResolutionSuccess',
    'ResolutionCycleError',
    'ResolutionNotFoundError',
    'ResolutionCrossMarketplaceError',
    'LoadedPlugin',
    'PluginError',
    'qualify_dependency',
    'resolve_dependency_closure',
    'verify_and_demote',
    'find_reverse_dependents',
    'format_dependency_count_suffix',
    'format_reverse_dependents_suffix',
]
