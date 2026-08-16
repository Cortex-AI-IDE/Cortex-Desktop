"""
Settings - TypeScript to Python conversion (COMPLETE - All 10 Phases).
TypeScript source: utils/settings/settings.ts (1016 lines)

"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Set
from copy import deepcopy


def _primary_config_home_dir() -> str:
    return os.path.join(os.path.expanduser('~'), '.cortex')


# ============================================================
# PHASE 1: Core types and imports
# ============================================================

try:
    from .constants import getEnabledSettingSources, SettingSource, EditableSettingSource
except ImportError:
    SettingSource = str
    EditableSettingSource = str
    def getEnabledSettingSources() -> List[str]:
        return ['userSettings', 'projectSettings', 'localSettings', 'policySettings', 'flagSettings']

try:
    from .types import SettingsJson, SettingsSchema
except ImportError:
    SettingsJson = Dict[str, Any]
    def SettingsSchema():
        class Schema:
            def safe_parse(self, data):
                class Result:
                    success = True
                    data = data
                return Result()
            def strip(self):
                return self
            def parse(self, data):
                return data
        return Schema()

try:
    from .validation import filterInvalidPermissionRules, formatZodError, SettingsWithErrors, ValidationError
except ImportError:
    class ValidationError:
        def __init__(self, file: str, path: str, message: str):
            self.file = file
            self.path = path
            self.message = message
    
    class SettingsWithErrors:
        def __init__(self, settings: Dict, errors: List):
            self.settings = settings
            self.errors = errors

try:
    from .settingsCache import (
        getCachedParsedFile,
        getCachedSettingsForSource,
        getPluginSettingsBase,
        getSessionSettingsCache,
        resetSettingsCache,
        setCachedParsedFile,
        setCachedSettingsForSource,
        setSessionSettingsCache,
    )
except ImportError:
    _parsed_file_cache = {}
    _settings_source_cache = {}
    _session_cache = None
    
    def getCachedParsedFile(path):
        return _parsed_file_cache.get(path)
    
    def setCachedParsedFile(path, result):
        _parsed_file_cache[path] = result
    
    def getCachedSettingsForSource(source):
        return _settings_source_cache.get(source)
    
    def setCachedSettingsForSource(source, settings):
        _settings_source_cache[source] = settings
    
    def getPluginSettingsBase():
        return None
    
    def getSessionSettingsCache():
        return _session_cache
    
    def setSessionSettingsCache(result):
        global _session_cache
        _session_cache = result
    
    def resetSettingsCache():
        global _parsed_file_cache, _settings_source_cache, _session_cache
        _parsed_file_cache.clear()
        _settings_source_cache.clear()
        _session_cache = None

try:
    from .managedPath import getManagedFilePath, getManagedSettingsDropInDir
except ImportError:
    def getManagedFilePath():
        return _primary_config_home_dir()
    
    def getManagedSettingsDropInDir():
        return os.path.join(getManagedFilePath(), 'managed-settings.d')

try:
    from .mdm.settings import getHkcuSettings, getMdmSettings
except ImportError:
    def getHkcuSettings():
        return {'settings': {}, 'errors': []}
    
    def getMdmSettings():
        return {'settings': {}, 'errors': []}

try:
    from ..bootstrap.state import (
        getFlagSettingsInline,
        getFlagSettingsPath,
        getOriginalCwd,
        getUseCoworkPlugins,
    )
except ImportError:
    def getFlagSettingsInline():
        return None
    
    def getFlagSettingsPath():
        return None
    
    def getOriginalCwd():
        return os.getcwd()
    
    def getUseCoworkPlugins():
        return False

try:
    from ..services.remoteManagedSettings.syncCacheState import getRemoteManagedSettingsSyncFromCache
except ImportError:
    def getRemoteManagedSettingsSyncFromCache():
        return None

try:
    from .internalWrites import markInternalWrite
except ImportError:
    def markInternalWrite(path):
        pass


# ============================================================
# PHASE 2: Managed settings file loading
# ============================================================

def getManagedSettingsFilePath() -> str:
    """Get the path to the managed settings file"""
    return os.path.join(getManagedFilePath(), 'managed-settings.json')


def settingsMergeCustomizer(objValue: Any, srcValue: Any) -> Any:
    """Custom merge function for lodash mergeWith when merging settings"""
    if isinstance(objValue, list) and isinstance(srcValue, list):
        return mergeArrays(objValue, srcValue)
    return None  # Let default merge behavior handle it


def mergeArrays(targetArray: List, sourceArray: List) -> List:
    """Custom merge function for arrays - concatenate and deduplicate"""
    seen = set()
    result = []
    for item in targetArray + sourceArray:
        # Use json.dumps for hashability
        key = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def mergeWith(base: Dict, override: Dict, customizer) -> Dict:
    """Python implementation of lodash mergeWith"""
    result = deepcopy(base)
    
    for key, srcValue in override.items():
        if key in result:
            objValue = result[key]
            # Apply customizer if provided
            if customizer:
                customized = customizer(objValue, srcValue, key, result)
                if customized is not None:
                    result[key] = customized
                    continue
            
            # Default merge behavior
            if isinstance(objValue, dict) and isinstance(srcValue, dict):
                result[key] = mergeWith(objValue, srcValue, customizer)
            else:
                result[key] = deepcopy(srcValue)
        else:
            result[key] = deepcopy(srcValue)
    
    return result


def loadManagedFileSettings() -> Dict[str, Any]:
    """
    Load file-based managed settings: managed-settings.json + managed-settings.d/*.json.
    
    managed-settings.json is merged first (lowest precedence / base), then drop-in
    files are sorted alphabetically and merged on top (higher precedence).
    """
    errors = []
    merged = {}
    found = False
    
    # Load base file
    base_result = parseSettingsFile(getManagedSettingsFilePath())
    errors.extend(base_result.get('errors', []))
    settings = base_result.get('settings')
    if settings and len(settings) > 0:
        merged = mergeWith(merged, settings, settingsMergeCustomizer)
        found = True
    
    # Load drop-in directory
    dropInDir = getManagedSettingsDropInDir()
    try:
        entries = sorted([
            f for f in os.listdir(dropInDir)
            if f.endswith('.json') and not f.startswith('.')
            and os.path.isfile(os.path.join(dropInDir, f))
        ])
        
        for name in entries:
            file_result = parseSettingsFile(os.path.join(dropInDir, name))
            errors.extend(file_result.get('errors', []))
            settings = file_result.get('settings')
            if settings and len(settings) > 0:
                merged = mergeWith(merged, settings, settingsMergeCustomizer)
                found = True
    except FileNotFoundError:
        pass
    except NotADirectoryError:
        pass
    except Exception as e:
        logError(e)
    
    return {'settings': merged if found else None, 'errors': errors}


def getManagedFileSettingsPresence() -> Dict[str, bool]:
    """Check which file-based managed settings sources are present"""
    base_result = parseSettingsFile(getManagedSettingsFilePath())
    hasBase = bool(base_result.get('settings') and len(base_result['settings']) > 0)
    
    hasDropIns = False
    dropInDir = getManagedSettingsDropInDir()
    try:
        hasDropIns = any(
            f.endswith('.json') and not f.startswith('.')
            and os.path.isfile(os.path.join(dropInDir, f))
            for f in os.listdir(dropInDir)
        )
    except:
        pass
    
    return {'hasBase': hasBase, 'hasDropIns': hasDropIns}


# ============================================================
# PHASE 3: Settings file parsing
# ============================================================

def handleFileSystemError(error: Exception, path: str) -> None:
    """Handles file system errors appropriately"""
    if isinstance(error, FileNotFoundError) or (
        hasattr(error, 'errno') and error.errno == 2  # ENOENT
    ):
        logForDebugging(
            f'Broken symlink or missing file encountered for settings.json at path: {path}'
        )
    else:
        logError(error)


def parseSettingsFile(path: str) -> Dict[str, Any]:
    """
    Parses a settings file into a structured format
    Returns: {settings: SettingsJson | None, errors: List[ValidationError]}
    """
    cached = getCachedParsedFile(path)
    if cached:
        # Clone so callers can't mutate the cached entry
        return {
            'settings': deepcopy(cached.get('settings')) if cached.get('settings') else None,
            'errors': cached.get('errors', []),
        }
    
    result = parseSettingsFileUncached(path)
    setCachedParsedFile(path, result)
    # Clone the first return too
    return {
        'settings': deepcopy(result.get('settings')) if result.get('settings') else None,
        'errors': result.get('errors', []),
    }


def parseSettingsFileUncached(path: str) -> Dict[str, Any]:
    """Parse settings file without caching"""
    try:
        resolvedPath = str(Path(path).resolve())
        
        if not os.path.exists(resolvedPath):
            return {'settings': None, 'errors': []}
        
        with open(resolvedPath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if content.strip() == '':
            return {'settings': {}, 'errors': []}
        
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return {'settings': None, 'errors': []}
        
        if not isinstance(data, dict):
            return {'settings': None, 'errors': []}
        
        # Filter invalid permission rules before schema validation
        ruleWarnings = []
        try:
            ruleWarnings = filterInvalidPermissionRules(data, path)
        except:
            pass
        
        # Validate against schema
        try:
            schema = SettingsSchema()
            result = schema.safe_parse(data)
            if result.success:
                return {'settings': result.data, 'errors': ruleWarnings}
            else:
                errors = formatZodError(result.error, path)
                return {'settings': None, 'errors': ruleWarnings + errors}
        except:
            # If schema validation fails, return raw data
            return {'settings': data, 'errors': ruleWarnings}
    
    except Exception as error:
        handleFileSystemError(error, path)
        return {'settings': None, 'errors': []}


# ============================================================
# PHASE 4: Settings source paths
# ============================================================

def getSettingsRootPathForSource(source: SettingSource) -> str:
    """Get the absolute path to the associated file root for a given settings source"""
    if source == 'userSettings':
        return str(Path(_primary_config_home_dir()).resolve())
    elif source in ['policySettings', 'projectSettings', 'localSettings']:
        return str(Path(getOriginalCwd()).resolve())
    elif source == 'flagSettings':
        path = getFlagSettingsPath()
        if path:
            return str(Path(path).parent.resolve())
        return str(Path(getOriginalCwd()).resolve())
    
    return str(Path(getOriginalCwd()).resolve())


def getUserSettingsFilePath() -> str:
    """
    Get the user settings filename based on cowork mode.
    Returns 'cowork_settings.json' when in cowork mode, 'settings.json' otherwise.
    """
    try:
        from .envUtils import isEnvTruthy
    except ImportError:
        def isEnvTruthy(v):
            return v.lower() in ('1', 'true', 'yes') if v else False
    
    if getUseCoworkPlugins() or isEnvTruthy(os.environ.get('CORTEX_CODE_USE_COWORK_PLUGINS')):
        return 'cowork_settings.json'
    return 'settings.json'


def getSettingsFilePathForSource(source: SettingSource) -> Optional[str]:
    """Get file path for a settings source"""
    if source == 'userSettings':
        return os.path.join(
            getSettingsRootPathForSource(source),
            getUserSettingsFilePath(),
        )
    elif source in ['projectSettings', 'localSettings']:
        return os.path.join(
            getSettingsRootPathForSource(source),
            getRelativeSettingsFilePathForSource(source),
        )
    elif source == 'policySettings':
        return getManagedSettingsFilePath()
    elif source == 'flagSettings':
        return getFlagSettingsPath()
    
    return None


def getRelativeSettingsFilePathForSource(source: str) -> str:
    """Get relative file path for project/local settings"""
    if source == 'projectSettings':
        return os.path.join('.cortex', 'settings.json')
    elif source == 'localSettings':
        return os.path.join('.cortex', 'settings.local.json')
    return ''


# ============================================================
# PHASE 5: Settings retrieval per source
# ============================================================

def getSettingsForSource(source: SettingSource) -> Optional[SettingsJson]:
    """Get settings for a specific source with caching"""
    cached = getCachedSettingsForSource(source)
    if cached is not None:
        return cached
    
    result = getSettingsForSourceUncached(source)
    setCachedSettingsForSource(source, result)
    return result


def getSettingsForSourceUncached(source: SettingSource) -> Optional[SettingsJson]:
    """Get settings for a specific source without caching"""
    # For policySettings: first source wins (remote > HKLM/plist > file > HKCU)
    if source == 'policySettings':
        remote_settings = getRemoteManagedSettingsSyncFromCache()
        if remote_settings and len(remote_settings) > 0:
            return remote_settings
        
        mdm_result = getMdmSettings()
        if len(mdm_result.get('settings', {})) > 0:
            return mdm_result['settings']
        
        file_result = loadManagedFileSettings()
        if file_result.get('settings'):
            return file_result['settings']
        
        hkcu = getHkcuSettings()
        if len(hkcu.get('settings', {})) > 0:
            return hkcu['settings']
        
        return None
    
    settingsFilePath = getSettingsFilePathForSource(source)
    if settingsFilePath:
        file_result = parseSettingsFile(settingsFilePath)
        fileSettings = file_result.get('settings')
    else:
        fileSettings = None
    
    # For flagSettings, merge in any inline settings set via the SDK
    if source == 'flagSettings':
        inlineSettings = getFlagSettingsInline()
        if inlineSettings:
            try:
                schema = SettingsSchema()
                parsed = schema.safe_parse(inlineSettings)
                if parsed.success:
                    return mergeWith(
                        fileSettings or {},
                        parsed.data,
                        settingsMergeCustomizer,
                    )
            except:
                pass
    
    return fileSettings


def getPolicySettingsOrigin() -> Optional[str]:
    """
    Get the origin of the highest-priority active policy settings source.
    Uses "first source wins" — returns the first source that has content.
    Priority: remote > plist/hklm > file (managed-settings.json) > hkcu
    """
    # 1. Remote (highest)
    remote_settings = getRemoteManagedSettingsSyncFromCache()
    if remote_settings and len(remote_settings) > 0:
        return 'remote'
    
    # 2. Admin-only MDM (HKLM / macOS plist)
    mdm_result = getMdmSettings()
    if len(mdm_result.get('settings', {})) > 0:
        try:
            from ..utils.platform import getPlatform
            platform = getPlatform()
            return 'plist' if platform == 'macos' else 'hklm'
        except:
            return 'hklm'
    
    # 3. managed-settings.json + managed-settings.d/ (file-based)
    file_result = loadManagedFileSettings()
    if file_result.get('settings'):
        return 'file'
    
    # 4. HKCU (lowest — user-writable)
    hkcu = getHkcuSettings()
    if len(hkcu.get('settings', {})) > 0:
        return 'hkcu'
    
    return None


# ============================================================
# PHASE 6: Settings update/write
# ============================================================

def updateSettingsForSource(
    source: EditableSettingSource,
    settings: SettingsJson,
) -> Dict[str, Any]:
    """
    Merges settings into the existing settings for source using mergeWith.
    
    To delete a key from a record field, set it to None.
    """
    if source in ['policySettings', 'flagSettings']:
        return {'error': None}
    
    # Create the folder if needed
    filePath = getSettingsFilePathForSource(source)
    if not filePath:
        return {'error': None}
    
    try:
        os.makedirs(os.path.dirname(filePath), exist_ok=True)
        
        # Try to get existing settings with validation. Bypass the per-source
        # cache — mergeWith below mutates its target.
        existingSettings = getSettingsForSourceUncached(source)
        
        # If validation failed, check if file exists with a JSON syntax error
        if not existingSettings:
            content = None
            try:
                with open(filePath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except FileNotFoundError:
                # File doesn't exist — fall through to merge with empty settings
                pass
            except Exception as e:
                raise
            
            if content is not None:
                try:
                    rawData = json.loads(content)
                except json.JSONDecodeError:
                    # JSON syntax error - return validation error instead of overwriting
                    return {
                        'error': Exception(f'Invalid JSON syntax in settings file at {filePath}'),
                    }
                
                if rawData and isinstance(rawData, dict):
                    existingSettings = rawData
                    logForDebugging(
                        f'Using raw settings from {filePath} due to validation failure'
                    )
        
        def customMergeCustomizer(objValue, srcValue, key, obj):
            """Handle None as deletion, replace arrays"""
            if srcValue is None and obj and isinstance(key, str):
                del obj[key]
                return None
            # For arrays, always replace with the provided array
            if isinstance(srcValue, list):
                return srcValue
            return None
        
        updatedSettings = mergeWith(
            existingSettings or {},
            settings,
            customMergeCustomizer,
        )
        
        # Mark this as an internal write before writing the file
        markInternalWrite(filePath)
        
        # Write file
        with open(filePath, 'w', encoding='utf-8') as f:
            f.write(json.dumps(updatedSettings, indent=2) + '\n')
            f.flush()
            os.fsync(f.fileno())
        
        # Invalidate the session cache since settings have been updated
        resetSettingsCache()
        
        if source == 'localSettings':
            # Add to gitignore async (non-blocking)
            try:
                from ..utils.git.gitignore import addFileGlobRuleToGitignore
                addFileGlobRuleToGitignore(
                    getRelativeSettingsFilePathForSource('localSettings'),
                    getOriginalCwd(),
                )
            except:
                pass
    
    except Exception as e:
        error = Exception(f'Failed to read raw settings from {filePath}: {e}')
        logError(error)
        return {'error': error}
    
    return {'error': None}


# ============================================================
# PHASE 7: Settings merge utilities
# ============================================================

# Already defined in Phase 2:
# - mergeArrays()
# - settingsMergeCustomizer()
# - mergeWith()


# ============================================================
# PHASE 8: Settings logging helpers
# ============================================================

def getManagedSettingsKeysForLogging(settings: SettingsJson) -> List[str]:
    """
    Get a list of setting keys from managed settings for logging purposes.
    For certain nested settings (permissions, sandbox, hooks), expands to show
    one level of nesting.
    """
    # Use .strip() to get only valid schema keys
    try:
        schema = SettingsSchema()
        validSettings = schema.strip().parse(settings)
    except:
        validSettings = settings
    
    keysToExpand = ['permissions', 'sandbox', 'hooks']
    allKeys = []
    
    # Define valid nested keys for each nested setting we expand
    validNestedKeys = {
        'permissions': {
            'allow', 'deny', 'ask', 'defaultMode',
            'disableBypassPermissionsMode', 'disableAutoMode',
            'additionalDirectories',
        },
        'sandbox': {
            'enabled', 'failIfUnavailable', 'allowUnsandboxedCommands',
            'network', 'filesystem', 'ignoreViolations', 'excludedCommands',
            'autoAllowBashIfSandboxed', 'enableWeakerNestedSandbox',
            'enableWeakerNetworkIsolation', 'ripgrep',
        },
        'hooks': {
            'PreToolUse', 'PostToolUse', 'Notification', 'UserPromptSubmit',
            'SessionStart', 'SessionEnd', 'Stop', 'SubagentStop',
            'PreCompact', 'PostCompact', 'TeammateIdle', 'TaskCreated',
            'TaskCompleted',
        },
    }
    
    for key in validSettings.keys():
        if key in keysToExpand and validSettings.get(key) and isinstance(validSettings[key], dict):
            # Expand nested keys for these special settings (one level deep only)
            nestedObj = validSettings[key]
            validKeys = validNestedKeys.get(key, set())
            
            for nestedKey in nestedObj.keys():
                # Only include known valid nested keys
                if nestedKey in validKeys:
                    allKeys.append(f'{key}.{nestedKey}')
        else:
            # For other settings, just use the top-level key
            allKeys.append(key)
    
    return sorted(allKeys)


# ============================================================
# PHASE 9: Main settings loading and caching
# ============================================================

# Flag to prevent infinite recursion when loading settings
isLoadingSettings = False


def loadSettingsFromDisk() -> SettingsWithErrors:
    """Load settings from disk without using cache"""
    global isLoadingSettings
    
    # Prevent recursive calls
    if isLoadingSettings:
        return {'settings': {}, 'errors': []}
    
    import time
    startTime = time.time() * 1000
    logForDiagnosticsNoPII('info', 'settings_load_started')
    
    isLoadingSettings = True
    try:
        # Start with plugin settings as the lowest priority base
        pluginSettings = getPluginSettingsBase()
        mergedSettings = {}
        if pluginSettings:
            mergedSettings = mergeWith(
                mergedSettings,
                pluginSettings,
                settingsMergeCustomizer,
            )
        
        allErrors = []
        seenErrors = set()
        seenFiles = set()
        
        # Merge settings from each source in priority order
        for source in getEnabledSettingSources():
            # policySettings: "first source wins"
            if source == 'policySettings':
                policySettings = None
                policyErrors = []
                
                # 1. Remote (highest priority)
                remote_settings = getRemoteManagedSettingsSyncFromCache()
                if remote_settings and len(remote_settings) > 0:
                    try:
                        schema = SettingsSchema()
                        result = schema.safe_parse(remote_settings)
                        if result.success:
                            policySettings = result.data
                        else:
                            policyErrors.extend(
                                formatZodError(result.error, 'remote managed settings')
                            )
                    except:
                        pass
                
                # 2. Admin-only MDM (HKLM / macOS plist)
                if not policySettings:
                    mdm_result = getMdmSettings()
                    if len(mdm_result.get('settings', {})) > 0:
                        policySettings = mdm_result['settings']
                    policyErrors.extend(mdm_result.get('errors', []))
                
                # 3. managed-settings.json + managed-settings.d/
                if not policySettings:
                    file_result = loadManagedFileSettings()
                    if file_result.get('settings'):
                        policySettings = file_result['settings']
                    policyErrors.extend(file_result.get('errors', []))
                
                # 4. HKCU (lowest)
                if not policySettings:
                    hkcu = getHkcuSettings()
                    if len(hkcu.get('settings', {})) > 0:
                        policySettings = hkcu['settings']
                    policyErrors.extend(hkcu.get('errors', []))
                
                # Merge the winning policy source
                if policySettings:
                    mergedSettings = mergeWith(
                        mergedSettings,
                        policySettings,
                        settingsMergeCustomizer,
                    )
                
                # Deduplicate errors
                for error in policyErrors:
                    errorKey = f'{error.file}:{error.path}:{error.message}'
                    if errorKey not in seenErrors:
                        seenErrors.add(errorKey)
                        allErrors.append(error)
                
                continue
            
            # File-based sources
            filePath = getSettingsFilePathForSource(source)
            if filePath:
                resolvedPath = str(Path(filePath).resolve())

                # Skip if we've already loaded this file
                if resolvedPath not in seenFiles:
                    seenFiles.add(resolvedPath)

                    file_result = parseSettingsFile(filePath)

                    # Deduplicate errors
                    for error in file_result.get('errors', []):
                        errorKey = f'{error.file}:{error.path}:{error.message}'
                        if errorKey not in seenErrors:
                            seenErrors.add(errorKey)
                            allErrors.append(error)

                    if file_result.get('settings'):
                        selected_result = file_result
                    else:
                        selected_result = None
                else:
                    selected_result = None

                if selected_result is not None and selected_result.get('settings'):
                    mergedSettings = mergeWith(
                        mergedSettings,
                        selected_result['settings'],
                        settingsMergeCustomizer,
                    )
            
            # For flagSettings, also merge inline settings
            if source == 'flagSettings':
                inlineSettings = getFlagSettingsInline()
                if inlineSettings:
                    try:
                        schema = SettingsSchema()
                        parsed = schema.safe_parse(inlineSettings)
                        if parsed.success:
                            mergedSettings = mergeWith(
                                mergedSettings,
                                parsed.data,
                                settingsMergeCustomizer,
                            )
                    except:
                        pass
        
        elapsed = time.time() * 1000 - startTime
        logForDiagnosticsNoPII('info', 'settings_load_completed', {
            'duration_ms': elapsed,
            'source_count': len(seenFiles),
            'error_count': len(allErrors),
        })
        
        return {'settings': mergedSettings, 'errors': allErrors}
    
    finally:
        isLoadingSettings = False


def getInitialSettings() -> SettingsJson:
    """Get merged settings from all sources in priority order"""
    result = getSettingsWithErrors()
    return result.get('settings') or {}


# Deprecated alias for backwards compatibility
getSettings_DEPRECATED = getInitialSettings


def getSettingsWithSources() -> Dict[str, Any]:
    """
    Get the effective merged settings alongside the raw per-source settings,
    in merge-priority order.
    """
    # Reset both caches so they agree on current disk state
    resetSettingsCache()
    
    sources = []
    for source in getEnabledSettingSources():
        settings = getSettingsForSource(source)
        if settings and len(settings) > 0:
            sources.append({'source': source, 'settings': settings})
    
    return {
        'effective': getInitialSettings(),
        'sources': sources,
    }


def getSettingsWithErrors() -> Dict[str, Any]:
    """Get merged settings and validation errors from all sources"""
    # Use cached result if available
    cached = getSessionSettingsCache()
    if cached is not None:
        return cached
    
    # Load from disk and cache the result
    result = loadSettingsFromDisk()
    setSessionSettingsCache(result)
    return result


# ============================================================
# PHASE 10: Permission and auto-mode checks
# ============================================================

def hasSkipDangerousModePermissionPrompt() -> bool:
    """
    Returns true if any trusted settings source has accepted the bypass
    permissions mode dialog. projectSettings is intentionally excluded.
    """
    return bool(
        getSettingsForSource('userSettings', {}).get('skipDangerousModePermissionPrompt') or
        getSettingsForSource('localSettings', {}).get('skipDangerousModePermissionPrompt') or
        getSettingsForSource('flagSettings', {}).get('skipDangerousModePermissionPrompt') or
        getSettingsForSource('policySettings', {}).get('skipDangerousModePermissionPrompt')
    )


def hasAutoModeOptIn() -> bool:
    """
    Returns true if any trusted settings source has accepted the auto
    mode opt-in dialog. projectSettings is intentionally excluded.
    """
    try:
        from bun.bundle import feature
    except ImportError:
        def feature(name):
            return name == 'TRANSCRIPT_CLASSIFIER'
    
    if feature('TRANSCRIPT_CLASSIFIER'):
        user = getSettingsForSource('userSettings', {}).get('skipAutoPermissionPrompt')
        local = getSettingsForSource('localSettings', {}).get('skipAutoPermissionPrompt')
        flag = getSettingsForSource('flagSettings', {}).get('skipAutoPermissionPrompt')
        policy = getSettingsForSource('policySettings', {}).get('skipAutoPermissionPrompt')
        
        result = bool(user or local or flag or policy)
        logForDebugging(
            f'[auto-mode] hasAutoModeOptIn={result} skipAutoPermissionPrompt: '
            f'user={user} local={local} flag={flag} policy={policy}'
        )
        return result
    
    return False


def getUseAutoModeDuringPlan() -> bool:
    """
    Returns whether plan mode should use auto mode semantics. Default true.
    Returns false if any trusted source explicitly sets false.
    """
    try:
        from bun.bundle import feature
    except ImportError:
        def feature(name):
            return name == 'TRANSCRIPT_CLASSIFIER'
    
    if feature('TRANSCRIPT_CLASSIFIER'):
        return (
            getSettingsForSource('policySettings', {}).get('useAutoModeDuringPlan') is not False and
            getSettingsForSource('flagSettings', {}).get('useAutoModeDuringPlan') is not False and
            getSettingsForSource('userSettings', {}).get('useAutoModeDuringPlan') is not False and
            getSettingsForSource('localSettings', {}).get('useAutoModeDuringPlan') is not False
        )
    
    return True


def getAutoModeConfig() -> Optional[Dict[str, Any]]:
    """
    Returns the merged autoMode config from trusted settings sources.
    Only available when TRANSCRIPT_CLASSIFIER is active.
    """
    try:
        from bun.bundle import feature
    except ImportError:
        def feature(name):
            return name == 'TRANSCRIPT_CLASSIFIER'
    
    if feature('TRANSCRIPT_CLASSIFIER'):
        allow = []
        soft_deny = []
        environment = []
        
        for source in ['userSettings', 'localSettings', 'flagSettings', 'policySettings']:
            settings = getSettingsForSource(source)
            if not settings:
                continue
            
            autoMode = settings.get('autoMode', {})
            if not isinstance(autoMode, dict):
                continue
            
            if autoMode.get('allow'):
                allow.extend(autoMode['allow'])
            if autoMode.get('soft_deny'):
                soft_deny.extend(autoMode['soft_deny'])
            if os.environ.get('USER_TYPE') == 'ant':
                if autoMode.get('deny'):
                    soft_deny.extend(autoMode['deny'])
            if autoMode.get('environment'):
                environment.extend(autoMode['environment'])
        
        if allow or soft_deny or environment:
            result = {}
            if allow:
                result['allow'] = allow
            if soft_deny:
                result['soft_deny'] = soft_deny
            if environment:
                result['environment'] = environment
            return result
    
    return None


def rawSettingsContainsKey(key: str) -> bool:
    """
    Check if any raw settings file contains a specific key, regardless of validation.
    Useful for detecting user intent even when settings validation fails.
    """
    for source in getEnabledSettingSources():
        # Skip policySettings - we only care about user-configured settings
        if source == 'policySettings':
            continue
        
        filePath = getSettingsFilePathForSource(source)
        if not filePath:
            continue
        
        try:
            resolvedPath = str(Path(filePath).resolve())
            if not os.path.exists(resolvedPath):
                continue
            
            with open(resolvedPath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if not content.strip():
                continue
            
            rawData = json.loads(content)
            if rawData and isinstance(rawData, dict) and key in rawData:
                return True
        
        except FileNotFoundError:
            # File not found is expected
            pass
        except json.JSONDecodeError:
            # Invalid JSON, skip
            pass
        except Exception as error:
            handleFileSystemError(error, filePath)
    
    return False


# ============================================================
# STUB FUNCTIONS - For missing imports
# ============================================================

def logForDebugging(msg: str, level: str = 'info') -> None:
    """Stub logging"""
    pass


def logError(error: Exception) -> None:
    """Stub error logging"""
    pass


def logForDiagnosticsNoPII(level: str, message: str, metadata: Dict = None) -> None:
    """Stub diagnostics logging (no PII)"""
    pass
