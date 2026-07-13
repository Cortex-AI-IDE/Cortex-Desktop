"""
Load plugin commands for Cortex AI IDE.

Loads AI command/skill definitions from marketplace plugins by parsing markdown
files with frontmatter metadata. Handles skill directories, variable
substitution, argument parsing, and shell command execution.
"""

import os
from os.path import basename, dirname
from typing import List, Optional, Dict, Any, Set, Callable
import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)


# =============================================================================
# Type Definitions
# =============================================================================

class PluginMarkdownFile:
    """Represents a parsed markdown file from a plugin."""
    
    def __init__(
        self,
        file_path: str,
        base_dir: str,
        frontmatter: Dict[str, Any],
        content: str,
    ):
        self.file_path = file_path
        self.base_dir = base_dir
        self.frontmatter = frontmatter
        self.content = content


class LoadConfig:
    """Configuration for loading commands or skills."""
    
    def __init__(self, is_skill_mode: bool = False):
        self.is_skill_mode = is_skill_mode


class Command:
    """Command definition loaded from plugin."""
    
    def __init__(
        self,
        cmd_type: str,
        name: str,
        description: str,
        get_prompt_for_command: Callable,
        source: str = 'plugin',
        has_user_specified_description: bool = False,
        allowed_tools: Optional[List[str]] = None,
        argument_hint: Optional[str] = None,
        arg_names: Optional[List[str]] = None,
        when_to_use: Optional[str] = None,
        version: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[Any] = None,
        disable_model_invocation: Optional[bool] = None,
        user_invocable: bool = True,
        content_length: int = 0,
        plugin_info: Optional[Dict[str, Any]] = None,
        is_hidden: bool = False,
        progress_message: str = 'running',
        display_name: Optional[str] = None,
        loaded_from: Optional[str] = None,
    ):
        self.type = cmd_type
        self.name = name
        self.description = description
        self.get_prompt_for_command = get_prompt_for_command
        self.source = source
        self.has_user_specified_description = has_user_specified_description
        self.allowed_tools = allowed_tools or []
        self.argument_hint = argument_hint
        self.arg_names = arg_names
        self.when_to_use = when_to_use
        self.version = version
        self.model = model
        self.effort = effort
        self.disable_model_invocation = disable_model_invocation
        self.user_invocable = user_invocable
        self.content_length = content_length
        self.plugin_info = plugin_info or {}
        self.is_hidden = is_hidden
        self.progress_message = progress_message
        self.display_name = display_name
        self.loaded_from = loaded_from
    
    def user_facing_name(self) -> str:
        """Get user-facing command name."""
        return self.display_name or self.name
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'type': self.type,
            'name': self.name,
            'description': self.description,
            'source': self.source,
            'allowedTools': self.allowed_tools,
            'argumentHint': self.argument_hint,
            'argNames': self.arg_names,
            'whenToUse': self.when_to_use,
            'version': self.version,
            'model': self.model,
            'effort': self.effort,
            'userInvocable': self.user_invocable,
            'isHidden': self.is_hidden,
            'progressMessage': self.progress_message,
        }


# =============================================================================
# Helper Functions
# =============================================================================

def is_skill_file(file_path: str) -> bool:
    """Check if a file path is a skill file (SKILL.md)."""
    return bool(re.match(r'^skill\.md$', basename(file_path), re.IGNORECASE))


def get_command_name_from_file(
    file_path: str,
    base_dir: str,
    plugin_name: str,
) -> str:
    """
    Get command name from file path, handling both regular files and skills.
    
    For skills: uses parent directory name
    For regular files: uses filename without .md
    """
    is_skill = is_skill_file(file_path)
    
    if is_skill:
        # For skills, use the parent directory name
        skill_directory = dirname(file_path)
        parent_of_skill_dir = dirname(skill_directory)
        command_base_name = basename(skill_directory)
        
        # Build namespace from parent of skill directory
        relative_path = parent_of_skill_dir[len(base_dir):].lstrip('/\\') if parent_of_skill_dir.startswith(base_dir) else ''
        namespace = ':'.join(relative_path.split(os.sep)) if relative_path else ''
        
        return f'{plugin_name}:{namespace}:{command_base_name}' if namespace else f'{plugin_name}:{command_base_name}'
    else:
        # For regular files, use filename without .md
        file_directory = dirname(file_path)
        command_base_name = basename(file_path).replace('.md', '')
        
        # Build namespace from file directory
        relative_path = file_directory[len(base_dir):].lstrip('/\\') if file_directory.startswith(base_dir) else ''
        namespace = ':'.join(relative_path.split(os.sep)) if relative_path else ''
        
        return f'{plugin_name}:{namespace}:{command_base_name}' if namespace else f'{plugin_name}:{command_base_name}'


async def collect_markdown_files(
    dir_path: str,
    base_dir: str,
    loaded_paths: Set[str],
) -> List[PluginMarkdownFile]:
    """Recursively collects all markdown files from a directory."""
    from utils.plugins.walkPluginMarkdown import walk_plugin_markdown
    from utils.fsOperations import get_fs_implementation, is_duplicate_path
    from utils.frontmatterParser import parse_frontmatter
    
    files = []
    fs = get_fs_implementation()
    
    async def process_file(full_path: str, namespace: List[str]):
        if is_duplicate_path(fs, full_path, loaded_paths):
            return
        
        # Read file content
        if hasattr(fs, 'read_file'):
            content = await fs.read_file(full_path, encoding='utf-8')
        else:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
        
        # Parse frontmatter
        parsed = parse_frontmatter(content, full_path)
        frontmatter = parsed.get('frontmatter', {})
        markdown_content = parsed.get('content', '')
        
        files.append(PluginMarkdownFile(
            file_path=full_path,
            base_dir=base_dir,
            frontmatter=frontmatter,
            content=markdown_content,
        ))
    
    await walk_plugin_markdown(
        dir_path,
        process_file,
        {'logLabel': 'commands'},
    )
    
    return files


def transform_plugin_skill_files(
    files: List[PluginMarkdownFile],
) -> List[PluginMarkdownFile]:
    """
    Transforms plugin markdown files to handle skill directories.
    
    If a directory has SKILL.md, only include the skill file (not other .md files).
    """
    files_by_dir: Dict[str, List[PluginMarkdownFile]] = {}
    
    for file in files:
        dir_path = dirname(file.file_path)
        if dir_path not in files_by_dir:
            files_by_dir[dir_path] = []
        files_by_dir[dir_path].append(file)
    
    result = []
    
    for dir_path, dir_files in files_by_dir.items():
        skill_files = [f for f in dir_files if is_skill_file(f.file_path)]
        
        if skill_files:
            # Use the first skill file if multiple exist
            skill_file = skill_files[0]
            if len(skill_files) > 1:
                logger.debug(
                    f'Multiple skill files found in {dir_path}, '
                    f'using {basename(skill_file.file_path)}'
                )
            # Directory has a skill - only include the skill file
            result.append(skill_file)
        else:
            result.extend(dir_files)
    
    return result


# =============================================================================
# Command Creation
# =============================================================================

def create_plugin_command(
    command_name: str,
    file: PluginMarkdownFile,
    source_name: str,
    plugin_manifest: Dict[str, Any],
    plugin_path: str,
    is_skill: bool,
    config: LoadConfig = None,
) -> Optional[Command]:
    """
    Create a Command from a plugin markdown file.
    
    Args:
        command_name: Fully qualified command name
        file: Parsed markdown file
        source_name: Plugin source identifier
        plugin_manifest: Plugin manifest
        plugin_path: Plugin root path
        is_skill: Whether this is a skill file
        config: Load configuration
    
    Returns:
        Command or None if failed
    """
    try:
        from utils.frontmatterParser import (
            coerce_description_to_string,
            parse_boolean_frontmatter,
            parse_shell_frontmatter,
        )
        from utils.markdownConfigLoader import (
            extract_description_from_markdown,
            parse_slash_command_tools_from_frontmatter,
        )
        from utils.plugins.pluginOptionsStorage import (
            substitute_plugin_variables,
            substitute_user_config_in_content,
            load_plugin_options,
        )
        from utils.argumentSubstitution import parse_argument_names, substitute_arguments
        
        frontmatter = file.frontmatter
        content = file.content
        
        # Parse description
        validated_description = coerce_description_to_string(
            frontmatter.get('description'),
            command_name,
        )
        description = (
            validated_description or
            extract_description_from_markdown(
                content,
                'Plugin skill' if is_skill else 'Plugin command',
            )
        )
        
        # Substitute ${CORTEX_PLUGIN_ROOT} in allowed-tools before parsing
        raw_allowed_tools = frontmatter.get('allowed-tools')
        if isinstance(raw_allowed_tools, str):
            substituted_allowed_tools = substitute_plugin_variables(
                raw_allowed_tools,
                {'path': plugin_path, 'source': source_name},
            )
        elif isinstance(raw_allowed_tools, list):
            substituted_allowed_tools = [
                substitute_plugin_variables(tool, {'path': plugin_path, 'source': source_name})
                if isinstance(tool, str) else tool
                for tool in raw_allowed_tools
            ]
        else:
            substituted_allowed_tools = raw_allowed_tools
        
        allowed_tools = parse_slash_command_tools_from_frontmatter(substituted_allowed_tools)
        
        # Parse metadata
        argument_hint = frontmatter.get('argument-hint')
        argument_names = parse_argument_names(frontmatter.get('arguments'))
        when_to_use = frontmatter.get('when_to_use')
        version = frontmatter.get('version')
        display_name = frontmatter.get('name')
        
        # Handle model configuration
        model_raw = frontmatter.get('model')
        model = None
        if model_raw == 'inherit':
            model = None
        elif model_raw:
            # TODO: Integrate with model parsing when available
            # model = parse_user_specified_model(model_raw)
            model = model_raw if isinstance(model_raw, str) else None
        
        # Parse effort
        effort_raw = frontmatter.get('effort')
        effort = parse_effort_value(effort_raw) if effort_raw is not None else None
        if effort_raw is not None and effort is None:
            logger.debug(
                f'Plugin command {command_name} has invalid effort '
                f"'{effort_raw}'. Valid options: {', '.join(map(str, EFFORT_LEVELS))} or an integer"
            )
        
        # Parse boolean flags
        disable_model_invocation = parse_boolean_frontmatter(
            frontmatter.get('disable-model-invocation')
        )
        
        user_invocable_value = frontmatter.get('user-invocable')
        user_invocable = (
            True if user_invocable_value is None
            else parse_boolean_frontmatter(user_invocable_value)
        )
        
        # Parse shell configuration
        shell = parse_shell_frontmatter(frontmatter.get('shell'), command_name)
        
        # Create command with lazy prompt getter
        async def get_prompt_for_command(args=None, context=None):
            """Get prompt content for command execution."""
            # For skills from skills/ directory, include base directory
            if config and config.is_skill_mode:
                final_content = f'Base directory for this skill: {dirname(file.file_path)}\n\n{content}'
            else:
                final_content = content
            
            # Substitute arguments
            final_content = substitute_arguments(
                final_content,
                args or {},
                True,
                argument_names,
            )
            
            # Replace ${CORTEX_PLUGIN_ROOT} and ${plugin_source}
            final_content = substitute_plugin_variables(final_content, {
                'path': plugin_path,
                'source': source_name,
            })
            
            # Replace ${user_config.X} with saved option values
            if plugin_manifest.get('userConfig'):
                final_content = substitute_user_config_in_content(
                    final_content,
                    load_plugin_options(source_name),
                    plugin_manifest['userConfig'],
                )
            
            # Replace ${CORTEX_SKILL_DIR} with this specific skill's directory
            if config and config.is_skill_mode:
                raw_skill_dir = dirname(file.file_path)
                # Normalize to forward slashes
                skill_dir = raw_skill_dir.replace('\\', '/')
                final_content = final_content.replace(
                    '${CORTEX_SKILL_DIR}',
                    skill_dir,
                )
            
            # Replace ${CORTEX_SESSION_ID} with the current session ID
            # TODO: Integrate with session management when available
            # from bootstrap.state import get_session_id
            # final_content = final_content.replace('${CORTEX_SESSION_ID}', get_session_id())
            
            # TODO: Execute shell commands in prompt when available
            # from utils.promptShellExecution import execute_shell_commands_in_prompt
            # final_content = await execute_shell_commands_in_prompt(
            #     final_content, context, f'/{command_name}', shell
            # )
            
            return [{'type': 'text', 'text': final_content}]
        
        return Command(
            cmd_type='prompt',
            name=command_name,
            description=description or '',
            get_prompt_for_command=get_prompt_for_command,
            source='plugin',
            has_user_specified_description=validated_description is not None,
            allowed_tools=allowed_tools,
            argument_hint=argument_hint,
            arg_names=argument_names if argument_names else None,
            when_to_use=when_to_use,
            version=version,
            model=model,
            effort=effort,
            disable_model_invocation=disable_model_invocation,
            user_invocable=user_invocable,
            content_length=len(content),
            plugin_info={
                'pluginManifest': plugin_manifest,
                'repository': source_name,
            },
            is_hidden=not user_invocable,
            progress_message='loading' if (is_skill or (config and config.is_skill_mode)) else 'running',
            display_name=display_name,
            loaded_from='plugin' if (is_skill or (config and config.is_skill_mode)) else None,
        )
    
    except Exception as error:
        logger.error(f'Failed to create command {command_name}: {error}')
        return None


# =============================================================================
# Directory Loading
# =============================================================================

async def load_commands_from_directory(
    commands_path: str,
    plugin_name: str,
    source_name: str,
    plugin_manifest: Dict[str, Any],
    plugin_path: str,
    config: LoadConfig = None,
    loaded_paths: Set[str] = None,
) -> List[Command]:
    """
    Load all commands from a plugin's commands directory.
    
    Args:
        commands_path: Path to commands directory
        plugin_name: Plugin name
        source_name: Plugin source (e.g., 'plugin@marketplace')
        plugin_manifest: Plugin manifest dict
        plugin_path: Plugin root path
        config: Load configuration
        loaded_paths: Set of already loaded file paths
    
    Returns:
        List of Command objects
    """
    if config is None:
        config = LoadConfig(is_skill_mode=False)
    if loaded_paths is None:
        loaded_paths = set()
    
    # Collect all markdown files
    markdown_files = await collect_markdown_files(
        commands_path,
        commands_path,
        loaded_paths,
    )
    
    # Apply skill transformation
    processed_files = transform_plugin_skill_files(markdown_files)
    
    # Convert to commands
    commands = []
    for file in processed_files:
        command_name = get_command_name_from_file(
            file.file_path,
            file.base_dir,
            plugin_name,
        )
        
        command = create_plugin_command(
            command_name,
            file,
            source_name,
            plugin_manifest,
            plugin_path,
            is_skill_file(file.file_path),
            config,
        )
        
        if command:
            commands.append(command)
    
    return commands


# =============================================================================
# Main Plugin Command Loader
# =============================================================================

@lru_cache(maxsize=1)
async def load_plugin_commands() -> List[Command]:
    """
    Load all commands from enabled plugins.
    
    Memoized to avoid repeated loading. Clear cache with clear_plugin_command_cache().
    
    Returns:
        List of Command objects from all enabled plugins
    """
    from utils.plugins.pluginLoader import load_all_plugins_cache_only
    
    # Only load commands from enabled plugins
    result = await load_all_plugins_cache_only()
    enabled = result.get('enabled', [])
    errors = result.get('errors', [])
    
    if errors:
        error_messages = [str(e.get('message', e)) for e in errors]
        logger.debug(f"Plugin loading errors: {', '.join(error_messages)}")
    
    # Process each plugin
    all_commands = []
    
    for plugin in enabled:
        plugin_commands = []
        loaded_paths = set()
        
        # Load commands from default commands directory
        commands_path = plugin.get('commandsPath')
        if commands_path:
            try:
                commands = await load_commands_from_directory(
                    commands_path,
                    plugin.get('name', ''),
                    plugin.get('source', ''),
                    plugin.get('manifest', {}),
                    plugin.get('path', ''),
                    LoadConfig(is_skill_mode=False),
                    loaded_paths,
                )
                plugin_commands.extend(commands)
                
                if len(commands) > 0:
                    logger.debug(
                        f"Loaded {len(commands)} commands from plugin "
                        f"{plugin.get('name', '')} default directory"
                    )
            except Exception as error:
                logger.error(
                    f"Failed to load commands from plugin {plugin.get('name', '')} "
                    f"default directory: {error}"
                )
        
        # Load commands/skills from skills directory
        skills_path = plugin.get('skillsPath')
        if skills_path:
            try:
                skills = await load_commands_from_directory(
                    skills_path,
                    plugin.get('name', ''),
                    plugin.get('source', ''),
                    plugin.get('manifest', {}),
                    plugin.get('path', ''),
                    LoadConfig(is_skill_mode=True),
                    loaded_paths,
                )
                plugin_commands.extend(skills)
                
                if len(skills) > 0:
                    logger.debug(
                        f"Loaded {len(skills)} skills from plugin "
                        f"{plugin.get('name', '')} skills directory"
                    )
            except Exception as error:
                logger.error(
                    f"Failed to load skills from plugin {plugin.get('name', '')} "
                    f"skills directory: {error}"
                )
        
        # Load commands from additional paths in manifest
        commands_paths = plugin.get('commandsPaths')
        if commands_paths:
            for cmd_path in commands_paths:
                try:
                    if os.path.isdir(cmd_path):
                        commands = await load_commands_from_directory(
                            cmd_path,
                            plugin.get('name', ''),
                            plugin.get('source', ''),
                            plugin.get('manifest', {}),
                            plugin.get('path', ''),
                            LoadConfig(is_skill_mode=False),
                            loaded_paths,
                        )
                        
                        if len(commands) > 0:
                            logger.debug(
                                f"Loaded {len(commands)} commands from plugin "
                                f"{plugin.get('name', '')} custom path: {cmd_path}"
                            )
                        plugin_commands.extend(commands)
                
                except Exception as error:
                    logger.error(
                        f"Failed to load commands from plugin {plugin.get('name', '')} "
                        f"custom path {cmd_path}: {error}"
                    )
        
        all_commands.extend(plugin_commands)
    
    logger.debug(f'Total plugin commands loaded: {len(all_commands)}')
    return all_commands


def clear_plugin_command_cache() -> None:
    """Clear the plugin command cache."""
    load_plugin_commands.cache_clear()
    logger.debug('Plugin command cache cleared')
