# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportRedeclaration=false, reportAssignmentType=false
# ------------------------------------------------------------
# loadSkillsDir.py
# Python conversion of loadSkillsDir.ts (1087 lines)
#
# Loads skills from /skills/ and legacy /commands/ directories,
# handles dynamic skill discovery, conditional skills, and MCP integration.
#
# CONVERSION PHASES:
#   Phase 1: Core imports, types, utility functions
#   Phase 2: Frontmatter parsing and skill command creation
#   Phase 3: Skills directory loader (/skills/ format)
#   Phase 4: Legacy /commands/ loader + main getSkillDirCommands
#   Phase 5: Dynamic/conditional skills + MCP registration
# ------------------------------------------------------------

from __future__ import annotations

import asyncio
import os
import platform
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

# ============================================================
# PHASE 1: Core imports, type definitions, utility functions
# ============================================================

# Zod-like Result type for schema validation
@dataclass
class ZodSafeParseResult:
    """Result from schema validation (like Zod's safeParse)."""
    success: bool
    data: Any = None
    error: Optional[Exception] = None

# Try to import from src, fallback to stubs
try:
    from ..bootstrap.state import getAdditionalDirectoriesForCortexMd, getSessionId
    from ..services.analytics.index import (
        logEvent,
    )
    from ..services.tokenEstimation import roughTokenCountEstimation
    from ..utils.argumentSubstitution import parseArgumentNames, substituteArguments
    from ..utils.errors import isENOENT, isFsInaccessible
    from ..utils.frontmatterParser import (
        coerceDescriptionToString,
        FrontmatterData,
        parseBooleanFrontmatter,
        parseFrontmatter,
        parseShellFrontmatter,
        splitPathInFrontmatter,
    )
    from ..utils.fsOperations import FsOperations, getFsImplementation
    from ..utils.git.gitignore import isPathGitignored
    from ..utils.log import logError
    from ..utils.markdownConfigLoader import (
        extractDescriptionFromMarkdown,
        getProjectDirsUpToHome,
        loadMarkdownFilesForSubdir,
        parseSlashCommandToolsFromFrontmatter,
    )
    from ..utils.model.model import parseUserSpecifiedModel
    from ..utils.promptShellExecution import executeShellCommandsInPrompt
    from ..utils.settings.managedPath import getManagedFilePath
    from ..utils.settings.pluginOnlyPolicy import isRestrictedToPluginOnly
    from ..utils.signal import createSignal
except ImportError:
    # Fallback stubs for type checking
    AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS = Any
    Command = Any
    PromptCommand = Any
    EffortValue = Any
    FrontmatterData = Dict[str, Any]
    FrontmatterShell = Any
    HooksSettings = Any
    MarkdownFile = Any
    SettingSource = str

    def getAdditionalDirectoriesForCortexMd() -> List[str]: return []
    def getSessionId() -> str: return "test-session"
    def logEvent(*_args: Any, **_kwargs: Any) -> None: pass
    def roughTokenCountEstimation(content: str, bytesPerToken: int = 4) -> int: return len(content) // bytesPerToken
    def parseArgumentNames(_argumentNames: Any) -> List[str]: return []
    def substituteArguments(content: str, _args: Any, appendIfNoPlaceholder: bool = True, _argumentNames: Any = None) -> str: return content
    def logForDebugging(msg: str, **_kwargs: Any) -> None: pass
    def parseEffortValue(_val: Any) -> None: return None
    EFFORT_LEVELS = ['minimal', 'medium', 'high']
    def getCortexConfigHomeDir() -> str: return os.path.expanduser("~/.cortex")
    def isBareMode() -> bool: return False
    def isEnvTruthy(_val: Any) -> bool: return False
    def isENOENT(e: Any) -> bool: return False
    def isFsInaccessible(e: Any) -> bool: return False
    def coerceDescriptionToString(desc: Any, _name: Any) -> Any: return desc
    def parseBooleanFrontmatter(val: Any) -> bool: return bool(val)
    def parseFrontmatter(content: str, _file_path: str = '') -> Dict[str, Any]: return {'frontmatter': {}, 'content': content}
    def parseShellFrontmatter(_shell: Any, _name: Any) -> None: return None
    def splitPathInFrontmatter(paths: Any) -> Any: return paths if isinstance(paths, list) else []
    def getFsImplementation() -> FsOperations:
        from ..utils.fsOperations import FsOperations as FsOps
        class MockFs(FsOps):
            async def readdir(self, path: str) -> List[Any]: return []
            async def read_file(self, path: str, options: Dict[str, str]) -> str: return ''
            async def stat(self, path: str) -> Any: pass
        return MockFs()
    async def isPathGitignored(filePath: str, cwd: str) -> bool: return False
    def logError(_error: Any) -> None: pass
    def extractDescriptionFromMarkdown(content: str, _defaultDescription: str = 'Custom item') -> str: return _defaultDescription
    def getProjectDirsUpToHome(_subdir: str, _cwd: str) -> List[str]: return []
    async def loadMarkdownFilesForSubdir(_subdir: str, _cwd: str) -> List[Any]: return []
    def parseSlashCommandToolsFromFrontmatter(_toolsValue: Any) -> List[str]: return []
    def parseUserSpecifiedModel(model_str: str) -> Optional[str]: return model_str
    async def executeShellCommandsInPrompt(text: str, _context: Any, _slashCommandName: str, _shell: Any = None) -> str: return text
    def isSettingSourceEnabled(_source: Any) -> bool: return True
    def getManagedFilePath() -> str: return os.path.expanduser("~/.cortex")
    def isRestrictedToPluginOnly(_surface: str) -> bool: return False
    def HooksSchema() -> Any: 
        class Schema:
            def safeParse(self, data: Any) -> ZodSafeParseResult:
                return ZodSafeParseResult(success=True, data=data)
        return Schema()
    def createSignal() -> Any:
        class Signal:
            def subscribe(self, cb: Callable[[], None]) -> Callable[[], None]: return lambda: None
            def emit(self) -> None: pass
        return Signal()


LoadedFrom = str  # 'commands_DEPRECATED' | 'skills' | 'plugin' | 'managed' | 'bundled' | 'mcp'


def getSkillsPath(
    source: str,  # SettingSource | 'plugin'
    dir_name: str,  # 'skills' | 'commands'
) -> str:
    """
    Returns a cortex config directory path for a given source.
    """
    if source == 'policySettings':
        return os.path.join(getManagedFilePath(), '.cortex', dir_name)
    elif source == 'userSettings':
        return os.path.join(getCortexConfigHomeDir(), dir_name)
    elif source == 'projectSettings':
        return f".cortex/{dir_name}"
    elif source == 'plugin':
        return 'plugin'
    else:
        return ''


def estimateSkillFrontmatterTokens(skill: Command) -> int:
    """
    Estimates token count for a skill based on frontmatter only
    (name, description, whenToUse) since full content is only loaded on invocation.
    """
    frontmatter_text = ' '.join([
        skill.get('name', ''),
        skill.get('description', ''),
        skill.get('whenToUse', ''),
    ]).strip()
    return roughTokenCountEstimation(frontmatter_text)


async def getFileIdentity(file_path: str) -> Optional[str]:
    """
    Gets a unique identifier for a file by resolving symlinks to a canonical path.
    This allows detection of duplicate files accessed through different paths
    (e.g., via symlinks or overlapping parent directories).
    Returns None if the file doesn't exist or can't be resolved.

    Uses os.path.realpath to resolve symlinks, which is filesystem-agnostic and avoids
    issues with filesystems that report unreliable inode values.
    See: upstream issue #13893
    """
    try:
        return os.path.realpath(file_path)
    except Exception:
        return None


# Internal type to track skill with its file path for deduplication
@dataclass
class SkillWithPath:
    skill: Command
    file_path: str


def parseHooksFromFrontmatter(
    frontmatter: FrontmatterData,
    skill_name: str,
) -> Optional[HooksSettings]:
    """
    Parse and validate hooks from frontmatter.
    Returns None if hooks are not defined or invalid.
    """
    hooks = frontmatter.get('hooks')
    if not hooks:
        return None

    result = HooksSchema().safeParse(hooks)
    if not result.success:
        # result may not have 'error' attribute in all cases
        error_msg = getattr(result, 'error', None)
        error_detail = getattr(error_msg, 'message', str(error_msg)) if error_msg else 'unknown error'
        logForDebugging(
            f"Invalid hooks in skill '{skill_name}': {error_detail}"
        )
        return None

    return result.data


def parseSkillPaths(frontmatter: FrontmatterData) -> Optional[List[str]]:
    """
    Parse paths frontmatter from a skill, using the same format as CORTEX.md rules.
    Returns None if no paths are specified or if all patterns are match-all.
    """
    paths = frontmatter.get('paths')
    if not paths:
        return None

    patterns = splitPathInFrontmatter(paths)
    # Remove /** suffix - ignore library treats 'path' as matching both
    # the path itself and everything inside it
    patterns = [
        pattern[:-3] if pattern.endswith('/**') else pattern
        for pattern in patterns
    ]
    patterns = [p for p in patterns if len(p) > 0]

    # If all patterns are ** (match-all), treat as no paths (None)
    if len(patterns) == 0 or all(p == '**' for p in patterns):
        return None

    return patterns


# ============================================================
# PHASE 2: Frontmatter parsing and skill command creation
# ============================================================

def parseSkillFrontmatterFields(
    frontmatter: FrontmatterData,
    markdown_content: str,
    resolved_name: str,
    description_fallback_label: str = 'Skill',  # 'Skill' | 'Custom command'
) -> Dict[str, Any]:
    """
    Parses all skill frontmatter fields that are shared between file-based and
    MCP skill loading. Caller supplies the resolved skill name and the
    source/loadedFrom/baseDir/paths fields separately.
    """
    validated_description = coerceDescriptionToString(
        frontmatter.get('description'),
        resolved_name,
    )
    description = (
        validated_description if validated_description is not None
        else extractDescriptionFromMarkdown(markdown_content, description_fallback_label)
    )

    user_invocable = (
        True if 'user-invocable' not in frontmatter
        else parseBooleanFrontmatter(frontmatter['user-invocable'])
    )

    model = None
    if frontmatter.get('model') == 'inherit':
        model = None
    elif frontmatter.get('model'):
        model = parseUserSpecifiedModel(str(frontmatter['model']))

    effort_raw = frontmatter.get('effort')
    effort = parseEffortValue(effort_raw) if effort_raw is not None else None
    if effort_raw is not None and effort is None:
        logForDebugging(
            f"Skill {resolved_name} has invalid effort '{effort_raw}'. Valid options: {', '.join(EFFORT_LEVELS)} or an integer"
        )

    return {
        'displayName': str(frontmatter['name']) if frontmatter.get('name') is not None else None,
        'description': description,
        'hasUserSpecifiedDescription': validated_description is not None,
        'allowedTools': parseSlashCommandToolsFromFrontmatter(
            frontmatter.get('allowed-tools'),
        ),
        'argumentHint': str(frontmatter['argument-hint']) if frontmatter.get('argument-hint') is not None else None,
        'argumentNames': parseArgumentNames(
            frontmatter.get('arguments')
        ),
        'whenToUse': frontmatter.get('when_to_use'),
        'version': frontmatter.get('version'),
        'model': model,
        'disableModelInvocation': parseBooleanFrontmatter(
            frontmatter.get('disable-model-invocation')
        ),
        'userInvocable': user_invocable,
        'hooks': parseHooksFromFrontmatter(frontmatter, resolved_name),
        'executionContext': 'fork' if frontmatter.get('context') == 'fork' else None,
        'agent': frontmatter.get('agent'),
        'effort': effort,
        'shell': parseShellFrontmatter(frontmatter.get('shell'), resolved_name),
    }


def createSkillCommand(
    skill_name: str,
    display_name: Optional[str],
    description: str,
    has_user_specified_description: bool,
    markdown_content: str,
    allowed_tools: List[str],
    argument_hint: Optional[str],
    argument_names: List[str],
    when_to_use: Optional[str],
    version: Optional[str],
    model: Optional[str],
    disable_model_invocation: bool,
    user_invocable: bool,
    source: str,  # PromptCommand['source']
    base_dir: Optional[str],
    loaded_from: LoadedFrom,
    hooks: Optional[HooksSettings],
    execution_context: Optional[str],  # 'inline' | 'fork'
    agent: Optional[str],
    paths: Optional[List[str]],
    effort: Optional[Any],  # EffortValue
    shell: Optional[Any],  # FrontmatterShell
) -> Command:
    """
    Creates a skill command from parsed data
    """
    async def get_prompt_for_command(args: str, tool_use_context: Any) -> List[Dict[str, Any]]:
        final_content = (
            f"Base directory for this skill: {base_dir}\n\n{markdown_content}"
            if base_dir
            else markdown_content
        )

        final_content = substituteArguments(
            final_content,
            args,
            True,
            argument_names,
        )

        # Replace ${CORTEX_SKILL_DIR} with the skill's own directory so bash
        # injection (!`...`) can reference bundled scripts. Normalize backslashes
        # to forward slashes on Windows so shell commands don't treat them as escapes.
        if base_dir:
            skill_dir = base_dir.replace('\\', '/') if platform.system() == 'Windows' else base_dir
            final_content = final_content.replace('${CORTEX_SKILL_DIR}', skill_dir)

        # Replace ${CORTEX_SESSION_ID} with the current session ID
        final_content = final_content.replace(
            '${CORTEX_SESSION_ID}',
            getSessionId(),
        )

        # Security: MCP skills are remote and untrusted — never execute inline
        # shell commands (!`…` / ```! … ```) from their markdown body.
        # ${CORTEX_SKILL_DIR} is meaningless for MCP skills anyway.
        if loaded_from != 'mcp':
            # Create a modified tool_use_context with updated getAppState
            class ModifiedContext:
                def __init__(self, ctx: Any) -> None:
                    self._ctx = ctx
                
                def getAppState(self) -> Dict[str, Any]:
                    app_state = self._ctx.getAppState()
                    return {
                        **app_state,
                        'toolPermissionContext': {
                            **app_state.get('toolPermissionContext', {}),
                            'alwaysAllowRules': {
                                **app_state.get('toolPermissionContext', {}).get('alwaysAllowRules', {}),
                                'command': allowed_tools,
                            },
                        },
                    }
                
                def __getattr__(self, name: str) -> Any:
                    return getattr(self._ctx, name)
            
            final_content = await executeShellCommandsInPrompt(
                final_content,
                ModifiedContext(tool_use_context),
                f'/{skill_name}',
                shell,
            )

        return [{'type': 'text', 'text': final_content}]

    return {
        'type': 'prompt',
        'name': skill_name,
        'description': description,
        'hasUserSpecifiedDescription': has_user_specified_description,
        'allowedTools': allowed_tools,
        'argumentHint': argument_hint,
        'argNames': argument_names if len(argument_names) > 0 else None,
        'whenToUse': when_to_use,
        'version': version,
        'model': model,
        'disableModelInvocation': disable_model_invocation,
        'userInvocable': user_invocable,
        'context': execution_context,
        'agent': agent,
        'effort': effort,
        'paths': paths,
        'contentLength': len(markdown_content),
        'isHidden': not user_invocable,
        'progressMessage': 'running',
        'userFacingName': lambda: display_name or skill_name,
        'source': source,
        'loadedFrom': loaded_from,
        'hooks': hooks,
        'skillRoot': base_dir,
        'getPromptForCommand': get_prompt_for_command,
    }


# ============================================================
# PHASE 3: Skills directory loader (/skills/ format)
# ============================================================

async def loadSkillsFromSkillsDir(
    base_path: str,
    source: str,
) -> List[SkillWithPath]:
    """
    Loads skills from a /skills/ directory path.
    Only supports directory format: skill-name/SKILL.md
    """
    fs = getFsImplementation()

    try:
        entries = await fs.readdir(base_path)
    except Exception as e:
        if not isFsInaccessible(e):
            logError(e)
        return []

    results = []
    for entry in entries:
        try:
            # Only support directory format: skill-name/SKILL.md
            if not entry.is_dir() and not entry.is_symlink():
                # Single .md files are NOT supported in /skills/ directory
                continue

            skill_dir_path = os.path.join(base_path, entry.name)
            skill_file_path = os.path.join(skill_dir_path, 'SKILL.md')

            try:
                content = await fs.read_file(skill_file_path, {'encoding': 'utf-8'})
            except Exception as e:
                # SKILL.md doesn't exist, skip this entry. Log non-ENOENT errors
                # (EACCES/EPERM/EIO) so permission/IO problems are diagnosable.
                if not isENOENT(e):
                    logForDebugging(f"[skills] failed to read {skill_file_path}: {e}", level='warn')
                continue

            parsed_frontmatter = parseFrontmatter(content, skill_file_path)
            frontmatter = parsed_frontmatter.get('frontmatter', {})
            markdown_content = parsed_frontmatter.get('content', content)

            skill_name = entry.name
            parsed = parseSkillFrontmatterFields(
                frontmatter,
                markdown_content,
                skill_name,
            )
            paths = parseSkillPaths(frontmatter)

            results.append(SkillWithPath(
                skill=createSkillCommand(
                    skill_name=skill_name,
                    display_name=parsed.get('displayName'),
                    description=parsed['description'],
                    has_user_specified_description=parsed['hasUserSpecifiedDescription'],
                    markdown_content=markdown_content,
                    allowed_tools=parsed['allowedTools'],
                    argument_hint=parsed['argumentHint'],
                    argument_names=parsed['argumentNames'],
                    when_to_use=parsed['whenToUse'],
                    version=parsed['version'],
                    model=parsed['model'],
                    disable_model_invocation=parsed['disableModelInvocation'],
                    user_invocable=parsed['userInvocable'],
                    source=source,
                    base_dir=skill_dir_path,
                    loaded_from='skills',
                    hooks=parsed['hooks'],
                    execution_context=parsed['executionContext'],
                    agent=parsed['agent'],
                    paths=paths,
                    effort=parsed['effort'],
                    shell=parsed['shell'],
                ),
                file_path=skill_file_path,
            ))
        except Exception as error:
            logError(error)

    return results


# ============================================================
# PHASE 4: Legacy /commands/ loader + main getSkillDirCommands
# ============================================================

def isSkillFile(file_path: str) -> bool:
    """Check if a file is named SKILL.md (case-insensitive)"""
    return os.path.basename(file_path).lower() == 'skill.md'


def transformSkillFiles(files: List[Any]) -> List[Any]:
    """
    Transforms markdown files to handle "skill" commands in legacy /commands/ folder.
    When a SKILL.md file exists in a directory, only that file is loaded
    and it takes the name of its parent directory.
    """
    files_by_dir: Dict[str, Any] = {}

    for file in files:
        dir_name = os.path.dirname(file.file_path if hasattr(file, 'file_path') else file['file_path'])
        dir_files = files_by_dir.get(dir_name, [])
        dir_files.append(file)
        files_by_dir[dir_name] = dir_files

    result = []

    for dir_name, dir_files in files_by_dir.items():
        skill_files = [f for f in dir_files if isSkillFile(f.file_path if hasattr(f, 'file_path') else f['file_path'])]
        if len(skill_files) > 0:
            skill_file = skill_files[0]
            if len(skill_files) > 1:
                logForDebugging(
                    f"Multiple skill files found in {dir_name}, using {os.path.basename(skill_file.file_path if hasattr(skill_file, 'file_path') else skill_file['file_path'])}"
                )
            result.append(skill_file)
        else:
            result.extend(dir_files)

    return result


def buildNamespace(target_dir: str, base_dir: str) -> str:
    """Build namespace from directory path relative to base"""
    normalized_base_dir = base_dir[:-1] if base_dir.endswith(os.sep) else base_dir

    if target_dir == normalized_base_dir:
        return ''

    relative_path = target_dir[len(normalized_base_dir) + 1:]
    return relative_path.replace(os.sep, ':') if relative_path else ''


def getSkillCommandName(file_path: str, base_dir: str) -> str:
    """Get command name for skill files"""
    skill_directory = os.path.dirname(file_path)
    parent_of_skill_dir = os.path.dirname(skill_directory)
    command_base_name = os.path.basename(skill_directory)

    namespace = buildNamespace(parent_of_skill_dir, base_dir)
    return f"{namespace}:{command_base_name}" if namespace else command_base_name


def getRegularCommandName(file_path: str, base_dir: str) -> str:
    """Get command name for regular .md files"""
    file_name = os.path.basename(file_path)
    file_directory = os.path.dirname(file_path)
    command_base_name = file_name.replace('.md', '')

    namespace = buildNamespace(file_directory, base_dir)
    return f"{namespace}:{command_base_name}" if namespace else command_base_name


def getCommandName(file: Any) -> str:
    """Get command name based on whether it's a skill file or regular file"""
    file_path = file.file_path if hasattr(file, 'file_path') else file['file_path']
    is_skill = isSkillFile(file_path)
    base_dir = file.base_dir if hasattr(file, 'base_dir') else file['base_dir']
    return (
        getSkillCommandName(file_path, base_dir)
        if is_skill
        else getRegularCommandName(file_path, base_dir)
    )


async def loadSkillsFromCommandsDir(cwd: str) -> List[SkillWithPath]:
    """
    Loads skills from legacy /commands/ directories.
    Supports both directory format (SKILL.md) and single .md file format.
    Commands from /commands/ default to user-invocable: true
    """
    try:
        markdown_files: List[Any] = await loadMarkdownFilesForSubdir('commands', cwd)
        processed_files: List[Any] = transformSkillFiles(markdown_files)

        skills: List[SkillWithPath] = []

        for file in processed_files:
            try:
                _base_dir: str = file.base_dir if hasattr(file, 'base_dir') else str(file.get('base_dir', ''))
                file_path: str = file.file_path if hasattr(file, 'file_path') else str(file.get('file_path', ''))
                frontmatter: Dict[str, Any] = file.frontmatter if hasattr(file, 'frontmatter') else file.get('frontmatter', {})
                content: str = file.content if hasattr(file, 'content') else str(file.get('content', ''))
                source: str = file.source if hasattr(file, 'source') else str(file.get('source', ''))

                is_skill_format = isSkillFile(file_path)
                skill_directory = os.path.dirname(file_path) if is_skill_format else None
                cmd_name = getCommandName(file)

                parsed = parseSkillFrontmatterFields(
                    frontmatter,
                    content,
                    cmd_name,
                    'Custom command',
                )

                skills.append(SkillWithPath(
                    skill=createSkillCommand(
                        skill_name=cmd_name,
                        display_name=None,
                        description=parsed['description'],
                        has_user_specified_description=parsed['hasUserSpecifiedDescription'],
                        markdown_content=content,
                        allowed_tools=parsed['allowedTools'],
                        argument_hint=parsed['argumentHint'],
                        argument_names=parsed['argumentNames'],
                        when_to_use=parsed['whenToUse'],
                        version=parsed['version'],
                        model=parsed['model'],
                        disable_model_invocation=parsed['disableModelInvocation'],
                        user_invocable=parsed['userInvocable'],
                        source=source,
                        base_dir=skill_directory,
                        loaded_from='commands_DEPRECATED',
                        hooks=parsed['hooks'],
                        execution_context=parsed['executionContext'],
                        agent=parsed['agent'],
                        paths=None,
                        effort=parsed['effort'],
                        shell=parsed['shell'],
                    ),
                    file_path=file_path,
                ))
            except Exception as error:
                logError(error)

        return skills
    except Exception as error:
        logError(error)
        return []


# Memoization cache for getSkillDirCommands
_skill_dir_commands_cache: Dict[str, List[Command]] = {}


async def getSkillDirCommands(cwd: str) -> List[Command]:
    """
    Loads all skills from both /skills/ and legacy /commands/ directories.

    Skills from /skills/ directories:
    - Only support directory format: skill-name/SKILL.md
    - Default to user-invocable: true (can opt-out with user-invocable: false)

    Skills from legacy /commands/ directories:
    - Support both directory format (SKILL.md) and single .md file format
    - Default to user-invocable: true (user can type /cmd)

    Uses simple memoization (not lodash memoize)
    """
    # Check cache
    if cwd in _skill_dir_commands_cache:
        return _skill_dir_commands_cache[cwd]

    user_skills_dir = os.path.join(getCortexConfigHomeDir(), 'skills')
    managed_skills_dir = os.path.join(getManagedFilePath(), '.cortex', 'skills')
    project_skills_dirs: List[str] = getProjectDirsUpToHome('skills', cwd)

    logForDebugging(
        f"Loading skills from: managed={managed_skills_dir}, user={user_skills_dir}, project=[{', '.join(project_skills_dirs)}]"
    )

    # Load from additional directories (--add-dir)
    additional_dirs = getAdditionalDirectoriesForCortexMd()
    skills_locked = isRestrictedToPluginOnly('skills')
    project_settings_enabled = isSettingSourceEnabled('projectSettings') and not skills_locked

    # --bare: skip auto-discovery (managed/user/project dir walks + legacy
    # commands-dir). Load ONLY explicit --add-dir paths. Bundled skills
    # register separately. skillsLocked still applies — --bare is not a
    # policy bypass.
    if isBareMode():
        if len(additional_dirs) == 0 or not project_settings_enabled:
            logForDebugging(
                f"[bare] Skipping skill dir discovery ({'no --add-dir' if len(additional_dirs) == 0 else 'projectSettings disabled or skillsLocked'})"
            )
            return []
        additional_skills_nested = await asyncio.gather(*[
            loadSkillsFromSkillsDir(
                os.path.join(dir, '.cortex', 'skills'),
                'projectSettings',
            )
            for dir in additional_dirs
        ])
        # No dedup needed — explicit dirs, user controls uniqueness.
        result = [s.skill for sublist in additional_skills_nested for s in sublist]
        _skill_dir_commands_cache[cwd] = result
        return result

    # Load from /skills/ directories, additional dirs, and legacy /commands/ in parallel
    # (all independent — different directories, no shared state)
    
    # Helper to create empty list coroutine
    async def empty_list() -> List[Any]:
        return []
    
    managed_skills_task: asyncio.Future = (
        asyncio.ensure_future(empty_list())
        if isEnvTruthy(os.environ.get('CORTEX_CODE_DISABLE_POLICY_SKILLS', ''))
        else asyncio.ensure_future(loadSkillsFromSkillsDir(managed_skills_dir, 'policySettings'))
    )
    
    user_skills_task: asyncio.Future = (
        asyncio.ensure_future(loadSkillsFromSkillsDir(user_skills_dir, 'userSettings'))
        if isSettingSourceEnabled('userSettings') and not skills_locked
        else asyncio.ensure_future(empty_list())
    )
    
    project_skills_task: asyncio.Future = (
        asyncio.gather(*[
            loadSkillsFromSkillsDir(dir, 'projectSettings')
            for dir in project_skills_dirs
        ])
        if project_settings_enabled
        else asyncio.ensure_future(empty_list())
    )
    
    additional_skills_task: asyncio.Future = (
        asyncio.gather(*[
            loadSkillsFromSkillsDir(
                os.path.join(dir, '.cortex', 'skills'),
                'projectSettings',
            )
            for dir in additional_dirs
        ])
        if project_settings_enabled
        else asyncio.ensure_future(empty_list())
    )
    
    legacy_commands_task: asyncio.Future = (
        asyncio.ensure_future(empty_list())
        if skills_locked
        else asyncio.ensure_future(loadSkillsFromCommandsDir(cwd))
    )

    managed_skills, user_skills, project_skills_nested, additional_skills_nested, legacy_commands = await asyncio.gather(
        managed_skills_task,
        user_skills_task,
        project_skills_task,
        additional_skills_task,
        legacy_commands_task,
    )

    # Flatten and combine all skills
    all_skills_with_paths = [
        *managed_skills,
        *user_skills,
        *[s for sublist in project_skills_nested for s in sublist],
        *[s for sublist in additional_skills_nested for s in sublist],
        *legacy_commands,
    ]

    # Deduplicate by resolved path (handles symlinks and duplicate parent directories)
    # Pre-compute file identities in parallel (realpath calls are independent),
    # then dedup synchronously (order-dependent first-wins)
    # Helper to create None coroutine
    async def none_coro() -> None:
        return None
    
    file_ids = await asyncio.gather(*[
        getFileIdentity(entry.file_path) if entry.skill.get('type') == 'prompt' else none_coro()
        for entry in all_skills_with_paths
    ])

    seen_file_ids: Dict[str, str] = {}
    deduplicated_skills: List[Command] = []

    for i, entry in enumerate(all_skills_with_paths):
        if entry is None or entry.skill.get('type') != 'prompt':
            continue
        skill = entry.skill

        file_id = file_ids[i]
        if file_id is None:
            deduplicated_skills.append(skill)
            continue

        existing_source = seen_file_ids.get(file_id)
        if existing_source is not None:
            logForDebugging(
                f"Skipping duplicate skill '{skill.get('name')}' from {skill.get('source')} (same file already loaded from {existing_source})"
            )
            continue

        seen_file_ids[file_id] = skill.get('source')
        deduplicated_skills.append(skill)

    duplicates_removed = len(all_skills_with_paths) - len(deduplicated_skills)
    if duplicates_removed > 0:
        logForDebugging(f"Deduplicated {duplicates_removed} skills (same file)")

    # Separate conditional skills (with paths frontmatter) from unconditional ones
    unconditional_skills = []
    new_conditional_skills = []
    for skill in deduplicated_skills:
        if (
            skill.get('type') == 'prompt' and
            skill.get('paths') and
            len(skill.get('paths', [])) > 0 and
            skill.get('name') not in activated_conditional_skill_names
        ):
            new_conditional_skills.append(skill)
        else:
            unconditional_skills.append(skill)

    # Store conditional skills for later activation when matching files are touched
    for skill in new_conditional_skills:
        conditional_skills[skill.get('name')] = skill

    if len(new_conditional_skills) > 0:
        logForDebugging(
            f"[skills] {len(new_conditional_skills)} conditional skills stored (activated when matching files are touched)"
        )

    logForDebugging(
        f"Loaded {len(deduplicated_skills)} unique skills ({len(unconditional_skills)} unconditional, {len(new_conditional_skills)} conditional, managed: {len(managed_skills)}, user: {len(user_skills)}, project: {len([s for sublist in project_skills_nested for s in sublist])}, additional: {len([s for sublist in additional_skills_nested for s in sublist])}, legacy commands: {len(legacy_commands)})"
    )

    # Cache result
    _skill_dir_commands_cache[cwd] = unconditional_skills
    return unconditional_skills


def clearSkillCaches():
    """Clear all skill caches"""
    _skill_dir_commands_cache.clear()
    conditional_skills.clear()
    activated_conditional_skill_names.clear()


# Backwards-compatible aliases for tests
getCommandDirCommands = getSkillDirCommands
clearCommandCaches = clearSkillCaches


# ============================================================
# PHASE 5: Dynamic/conditional skills + MCP registration
# ============================================================

# State for dynamically discovered skills
dynamic_skill_dirs: Set[str] = set()
dynamic_skills: Dict[str, Command] = {}

# Conditional skills (path-filtered)
# Skills with paths frontmatter that haven't been activated yet
conditional_skills: Dict[str, Command] = {}
# Names of skills that have been activated (survives cache clears within a session)
activated_conditional_skill_names: Set[str] = set()

# Signal fired when dynamic skills are loaded
skills_loaded = createSignal()


def onDynamicSkillsLoaded(callback: Callable[[], None]) -> Callable[[], None]:
    """
    Register a callback to be invoked when dynamic skills are loaded.
    Used by other modules to clear caches without creating import cycles.
    Returns an unsubscribe function.
    """
    # Wrap at subscribe time so a throwing listener is logged and skipped
    # rather than aborting skills_loaded.emit() and breaking skill loading.
    def safe_callback():
        try:
            callback()
        except Exception as error:
            logError(error)
    
    return skills_loaded.subscribe(safe_callback)


async def discoverSkillDirsForPaths(
    file_paths: List[str],
    cwd: str,
) -> List[str]:
    """
    Discovers skill directories by walking up from file paths to cwd.
    Only discovers directories below cwd (cwd-level skills are loaded at startup).

    Args:
        file_paths: Array of file paths to check
        cwd: Current working directory (upper bound for discovery)
    Returns:
        Array of newly discovered skill directories, sorted deepest first
    """
    fs = getFsImplementation()
    resolved_cwd = cwd[:-1] if cwd.endswith(os.sep) else cwd
    new_dirs = []

    for file_path in file_paths:
        # Start from the file's parent directory
        current_dir = os.path.dirname(file_path)

        # Walk up to cwd but NOT including cwd itself
        # CWD-level skills are already loaded at startup, so we only discover nested ones
        # Use prefix+separator check to avoid matching /project-backup when cwd is /project
        while current_dir.startswith(resolved_cwd + os.sep):
            skill_dir = os.path.join(current_dir, '.cortex', 'skills')

            # Skip if we've already checked this path (hit or miss) — avoids
            # repeating the same failed stat on every Read/Write/Edit call when
            # the directory doesn't exist (the common case).
            if skill_dir not in dynamic_skill_dirs:
                dynamic_skill_dirs.add(skill_dir)
                try:
                    await fs.stat(skill_dir)
                    # Skills dir exists. Before loading, check if the containing dir
                    # is gitignored — blocks e.g. node_modules/pkg/.cortex/skills from
                    # loading silently. `git check-ignore` handles nested .gitignore,
                    # .git/info/exclude, and global gitignore. Fails open outside a
                    # git repo (exit 128 → false); the invocation-time trust dialog
                    # is the actual security boundary.
                    if await isPathGitignored(current_dir, resolved_cwd):
                        logForDebugging(
                            f"[skills] Skipped gitignored skills dir: {skill_dir}"
                        )
                        continue
                    new_dirs.append(skill_dir)
                except Exception:
                    # Directory doesn't exist — already recorded above, continue
                    pass

            # Move to parent
            parent = os.path.dirname(current_dir)
            if parent == current_dir:
                break  # Reached root
            current_dir = parent

    # Sort by path depth (deepest first) so skills closer to the file take precedence
    return sorted(new_dirs, key=lambda x: x.count(os.sep), reverse=True)


async def addSkillDirectories(dirs: List[str]) -> None:
    """
    Loads skills from the given directories and merges them into the dynamic skills map.
    Skills from directories closer to the file (deeper paths) take precedence.

    Args:
        dirs: Array of skill directories to load from (should be sorted deepest first)
    """
    import asyncio
    
    if not isSettingSourceEnabled('projectSettings') or isRestrictedToPluginOnly('skills'):
        logForDebugging(
            '[skills] Dynamic skill discovery skipped: projectSettings disabled or plugin-only policy'
        )
        return
    
    if len(dirs) == 0:
        return

    previous_skill_names_for_logging = set(dynamic_skills.keys())

    # Load skills from all directories
    loaded_skills = await asyncio.gather(*[
        loadSkillsFromSkillsDir(dir, 'projectSettings')
        for dir in dirs
    ])

    # Process in reverse order (shallower first) so deeper paths override
    for i in range(len(loaded_skills) - 1, -1, -1):
        for entry in loaded_skills[i] or []:
            if entry.skill.get('type') == 'prompt':
                dynamic_skills[entry.skill.get('name')] = entry.skill

    new_skill_count = sum(len(sublist or []) for sublist in loaded_skills)
    if new_skill_count > 0:
        added_skills = [name for name in dynamic_skills.keys() if name not in previous_skill_names_for_logging]
        logForDebugging(
            f"[skills] Dynamically discovered {new_skill_count} skills from {len(dirs)} directories"
        )
        if len(added_skills) > 0:
            logEvent('tengu_dynamic_skills_changed', {
                'source': 'file_operation',
                'previousCount': len(previous_skill_names_for_logging),
                'newCount': len(dynamic_skills),
                'addedCount': len(added_skills),
                'directoryCount': len(dirs),
            })

    # Notify listeners that skills were loaded (so they can clear caches)
    skills_loaded.emit()


def getDynamicSkills() -> List[Command]:
    """
    Gets all dynamically discovered skills.
    These are skills discovered from file paths during the session.
    """
    return list(dynamic_skills.values())


def activateConditionalSkillsForPaths(
    file_paths: List[str],
    cwd: str,
) -> List[str]:
    """
    Activates conditional skills (skills with paths frontmatter) whose path
    patterns match the given file paths. Activated skills are added to the
    dynamic skills map, making them available to the model.

    Uses gitignore-style matching (would use 'ignore' library in full implementation),
    matching the behavior of CORTEX.md conditional rules.

    Args:
        file_paths: Array of file paths being operated on
        cwd: Current working directory (paths are matched relative to cwd)
    Returns:
        Array of newly activated skill names
    """
    if len(conditional_skills) == 0:
        return []

    activated = []

    for name, skill in list(conditional_skills.items()):
        if skill.get('type') != 'prompt' or not skill.get('paths') or len(skill.get('paths', [])) == 0:
            continue

        # Note: In full implementation, use 'ignore' library for gitignore-style matching
        # For now, simple pattern matching
        skill_paths = skill.get('paths', [])
        
        for file_path in file_paths:
            relative_path = (
                os.path.relpath(file_path, cwd)
                if os.path.isabs(file_path)
                else file_path
            )

            # ignore() throws on empty strings, paths escaping the base (../),
            # and absolute paths (Windows cross-drive relative() returns absolute).
            # Files outside cwd can't match cwd-relative patterns anyway.
            if (
                not relative_path or
                relative_path.startswith('..') or
                os.path.isabs(relative_path)
            ):
                continue

            # Simple pattern matching (full implementation would use ignore library)
            # Check if any pattern matches the relative path
            matches = any(
                relative_path.startswith(pattern) or relative_path == pattern
                for pattern in skill_paths
            )
            
            if matches:
                # Activate this skill by moving it to dynamic skills
                dynamic_skills[name] = skill
                del conditional_skills[name]
                activated_conditional_skill_names.add(name)
                activated.append(name)
                logForDebugging(
                    f"[skills] Activated conditional skill '{name}' (matched path: {relative_path})"
                )
                break

    if len(activated) > 0:
        logEvent('tengu_dynamic_skills_changed', {
            'source': 'conditional_paths',
            'previousCount': len(dynamic_skills) - len(activated),
            'newCount': len(dynamic_skills),
            'addedCount': len(activated),
            'directoryCount': 0,
        })

        # Notify listeners that skills were loaded (so they can clear caches)
        skills_loaded.emit()

    return activated


def getConditionalSkillCount() -> int:
    """
    Gets the number of pending conditional skills (for testing/debugging).
    """
    return len(conditional_skills)


def clearDynamicSkills() -> None:
    """
    Clears dynamic skill state (for testing).
    """
    dynamic_skill_dirs.clear()
    dynamic_skills.clear()
    conditional_skills.clear()
    activated_conditional_skill_names.clear()


# Expose createSkillCommand + parseSkillFrontmatterFields to MCP skill
# discovery via a leaf registry module. See mcpSkillBuilders.py for why this
# indirection exists.
try:
    from .mcpSkillBuilders import registerMCPSkillBuilders
    registerMCPSkillBuilders({
        'createSkillCommand': createSkillCommand,
        'parseSkillFrontmatterFields': parseSkillFrontmatterFields,
    })
except (ImportError, Exception):
    # MCP skill builders not available or failed to register
    pass
