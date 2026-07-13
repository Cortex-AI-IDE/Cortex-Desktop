"""
Load plugin agents for Cortex AI IDE.

Loads AI agent definitions from marketplace plugins by parsing markdown
files with frontmatter metadata. Handles namespace prefixing, variable
substitution, and memory integration.
"""

import os
from os.path import basename
from typing import List, Optional, Dict, Any, Set
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

VALID_MEMORY_SCOPES = ['user', 'project', 'local']


# =============================================================================
# Type Definitions
# =============================================================================

class AgentDefinition:
    """Agent definition loaded from plugin markdown file."""
    
    def __init__(
        self,
        agent_type: str,
        when_to_use: str,
        get_system_prompt,
        source: str = 'plugin',
        tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        color: Optional[str] = None,
        model: Optional[str] = None,
        filename: Optional[str] = None,
        plugin: Optional[str] = None,
        background: Optional[bool] = None,
        memory: Optional[str] = None,
        isolation: Optional[str] = None,
        effort: Optional[Any] = None,
        max_turns: Optional[int] = None,
    ):
        self.agent_type = agent_type
        self.when_to_use = when_to_use
        self.get_system_prompt = get_system_prompt
        self.source = source
        self.tools = tools
        self.disallowed_tools = disallowed_tools
        self.skills = skills
        self.color = color
        self.model = model
        self.filename = filename
        self.plugin = plugin
        self.background = background
        self.memory = memory
        self.isolation = isolation
        self.effort = effort
        self.max_turns = max_turns
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            'agentType': self.agent_type,
            'whenToUse': self.when_to_use,
            'source': self.source,
            'getSystemPrompt': self.get_system_prompt,
        }
        
        if self.tools is not None:
            result['tools'] = self.tools
        if self.disallowed_tools is not None:
            result['disallowedTools'] = self.disallowed_tools
        if self.skills is not None:
            result['skills'] = self.skills
        if self.color is not None:
            result['color'] = self.color
        if self.model is not None:
            result['model'] = self.model
        if self.filename is not None:
            result['filename'] = self.filename
        if self.plugin is not None:
            result['plugin'] = self.plugin
        if self.background is not None:
            result['background'] = self.background
        if self.memory is not None:
            result['memory'] = self.memory
        if self.isolation is not None:
            result['isolation'] = self.isolation
        if self.effort is not None:
            result['effort'] = self.effort
        if self.max_turns is not None:
            result['maxTurns'] = self.max_turns
        
        return result


# =============================================================================
# Core Loading Functions
# =============================================================================

async def load_agents_from_directory(
    agents_path: str,
    plugin_name: str,
    source_name: str,
    plugin_path: str,
    plugin_manifest: Dict[str, Any],
    loaded_paths: Set[str],
) -> List[AgentDefinition]:
    """
    Load all agents from a plugin's agents directory.
    
    Args:
        agents_path: Path to agents directory
        plugin_name: Plugin name
        source_name: Plugin source (e.g., 'plugin@marketplace')
        plugin_path: Plugin root path
        plugin_manifest: Plugin manifest dict
        loaded_paths: Set of already loaded file paths (to prevent duplicates)
    
    Returns:
        List of AgentDefinition objects
    """
    from utils.plugins.walkPluginMarkdown import walk_plugin_markdown
    
    agents = []
    
    async def process_agent_file(full_path: str, namespace: List[str]):
        agent = await load_agent_from_file(
            full_path,
            plugin_name,
            namespace,
            source_name,
            plugin_path,
            plugin_manifest,
            loaded_paths,
        )
        if agent:
            agents.append(agent)
    
    await walk_plugin_markdown(
        agents_path,
        process_agent_file,
        {'logLabel': 'agents'},
    )
    
    return agents


async def load_agent_from_file(
    file_path: str,
    plugin_name: str,
    namespace: List[str],
    source_name: str,
    plugin_path: str,
    plugin_manifest: Dict[str, Any],
    loaded_paths: Set[str],
) -> Optional[AgentDefinition]:
    """
    Load a single agent from a markdown file.
    
    Args:
        file_path: Path to markdown file
        plugin_name: Plugin name
        namespace: Directory namespace (e.g., ['category', 'subcategory'])
        source_name: Plugin source
        plugin_path: Plugin root path
        plugin_manifest: Plugin manifest
        loaded_paths: Set of loaded paths
    
    Returns:
        AgentDefinition or None if failed
    """
    from utils.frontmatterParser import parse_frontmatter, coerce_description_to_string, parse_positive_int_from_frontmatter
    from utils.markdownConfigLoader import parse_agent_tools_from_frontmatter, parse_slash_command_tools_from_frontmatter
    from utils.plugins.pluginOptionsStorage import substitute_plugin_variables, substitute_user_config_in_content, load_plugin_options
    from utils.fsOperations import get_fs_implementation, is_duplicate_path
    
    fs = get_fs_implementation()
    
    # Check for duplicate paths
    if is_duplicate_path(fs, file_path, loaded_paths):
        return None
    
    try:
        # Read file content
        if hasattr(fs, 'read_file'):
            content = await fs.read_file(file_path, encoding='utf-8')
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        
        # Parse frontmatter
        parsed = parse_frontmatter(content, file_path)
        frontmatter = parsed.get('frontmatter', {})
        markdown_content = parsed.get('content', '')
        
        # Get agent name
        base_agent_name = frontmatter.get('name') or basename(file_path).replace('.md', '')
        
        # Apply namespace prefixing: plugin:namespace:agent-name
        name_parts = [plugin_name] + namespace + [base_agent_name]
        agent_type = ':'.join(name_parts)
        
        # Parse metadata from frontmatter
        when_to_use = (
            coerce_description_to_string(frontmatter.get('description'), agent_type) or
            coerce_description_to_string(frontmatter.get('when-to-use'), agent_type) or
            f'Agent from {plugin_name} plugin'
        )
        
        # Parse tools and skills
        tools = parse_agent_tools_from_frontmatter(frontmatter.get('tools'))
        skills = parse_slash_command_tools_from_frontmatter(frontmatter.get('skills'))
        color = frontmatter.get('color')
        
        # Parse model
        model_raw = frontmatter.get('model')
        model = None
        if isinstance(model_raw, str) and len(model_raw.strip()) > 0:
            trimmed = model_raw.strip()
            model = 'inherit' if trimmed.lower() == 'inherit' else trimmed
        
        # Parse background flag
        background_raw = frontmatter.get('background')
        background = True if background_raw == 'true' or background_raw is True else None
        
        # Substitute variables in system prompt
        system_prompt = substitute_plugin_variables(markdown_content.strip(), {
            'path': plugin_path,
            'source': source_name,
        })
        
        # Substitute user config if manifest has userConfig
        user_config = plugin_manifest.get('userConfig')
        if user_config:
            system_prompt = substitute_user_config_in_content(
                system_prompt,
                load_plugin_options(source_name),
                user_config,
            )
        
        # Parse memory scope
        memory_raw = frontmatter.get('memory')
        memory = None
        if memory_raw is not None:
            if memory_raw in VALID_MEMORY_SCOPES:
                memory = memory_raw
            else:
                logger.debug(
                    f'Plugin agent file {file_path} has invalid memory value '
                    f"'{memory_raw}'. Valid options: {', '.join(VALID_MEMORY_SCOPES)}"
                )
        
        # Parse isolation mode
        isolation_raw = frontmatter.get('isolation')
        isolation = 'worktree' if isolation_raw == 'worktree' else None
        
        # Parse effort (string level or integer)
        effort_raw = frontmatter.get('effort')
        effort = parse_effort_value(effort_raw) if effort_raw is not None else None
        if effort_raw is not None and effort is None:
            logger.debug(
                f'Plugin agent file {file_path} has invalid effort '
                f"'{effort_raw}'. Valid options: {', '.join(map(str, EFFORT_LEVELS))} or an integer"
            )
        
        # Note: permissionMode, hooks, and mcpServers are intentionally NOT parsed
        # for plugin agents (security boundary - see TypeScript original)
        for field in ['permissionMode', 'hooks', 'mcpServers']:
            if field in frontmatter:
                logger.debug(
                    f'Plugin agent file {file_path} sets {field}, which is ignored '
                    f'for plugin agents. Use .cortex/agents/ for this level of control.'
                )
        
        # Parse maxTurns
        max_turns_raw = frontmatter.get('maxTurns')
        max_turns = parse_positive_int_from_frontmatter(max_turns_raw)
        if max_turns_raw is not None and max_turns is None:
            logger.debug(
                f'Plugin agent file {file_path} has invalid maxTurns '
                f"'{max_turns_raw}'. Must be a positive integer."
            )
        
        # Parse disallowedTools
        disallowed_tools = None
        if 'disallowedTools' in frontmatter:
            disallowed_tools = parse_agent_tools_from_frontmatter(frontmatter['disallowedTools'])
        
        # If memory is enabled, inject Write/Edit/Read tools for memory access
        # (Auto-memory integration would happen here if enabled)
        
        # Create agent definition
        def get_system_prompt_func():
            """Get system prompt with optional memory injection."""
            # TODO: Integrate with memory system when available
            # if is_auto_memory_enabled() and memory:
            #     memory_prompt = load_agent_memory_prompt(agent_type, memory)
            #     return system_prompt + '\n\n' + memory_prompt
            return system_prompt
        
        return AgentDefinition(
            agent_type=agent_type,
            when_to_use=when_to_use,
            get_system_prompt=get_system_prompt_func,
            source='plugin',
            tools=tools,
            disallowed_tools=disallowed_tools,
            skills=skills,
            color=color,
            model=model,
            filename=base_agent_name,
            plugin=source_name,
            background=background,
            memory=memory,
            isolation=isolation,
            effort=effort,
            max_turns=max_turns,
        )
    
    except Exception as error:
        logger.error(f'Failed to load agent from {file_path}: {error}')
        return None


# =============================================================================
# Main Plugin Agent Loader
# =============================================================================

@lru_cache(maxsize=1)
async def load_plugin_agents() -> List[AgentDefinition]:
    """
    Load all agents from enabled plugins.
    
    Memoized to avoid repeated loading. Clear cache with clear_plugin_agent_cache().
    
    Returns:
        List of AgentDefinition objects from all enabled plugins
    """
    from utils.plugins.pluginLoader import load_all_plugins_cache_only
    
    # Only load agents from enabled plugins
    result = await load_all_plugins_cache_only()
    enabled = result.get('enabled', [])
    errors = result.get('errors', [])
    
    if errors:
        error_messages = [str(e.get('message', e)) for e in errors]
        logger.debug(f"Plugin loading errors: {', '.join(error_messages)}")
    
    # Process each plugin
    all_agents = []
    
    for plugin in enabled:
        plugin_agents = []
        loaded_paths = set()
        
        # Load agents from default agents directory
        agents_path = plugin.get('agentsPath')
        if agents_path:
            try:
                agents = await load_agents_from_directory(
                    agents_path,
                    plugin.get('name', ''),
                    plugin.get('source', ''),
                    plugin.get('path', ''),
                    plugin.get('manifest', {}),
                    loaded_paths,
                )
                plugin_agents.extend(agents)
                
                if len(agents) > 0:
                    logger.debug(
                        f"Loaded {len(agents)} agents from plugin "
                        f"{plugin.get('name', '')} default directory"
                    )
            except Exception as error:
                logger.error(
                    f"Failed to load agents from plugin {plugin.get('name', '')} "
                    f"default directory: {error}"
                )
        
        # Load agents from additional paths in manifest
        agents_paths = plugin.get('agentsPaths')
        if agents_paths:
            for agent_path in agents_paths:
                try:
                    import asyncio
                    
                    # Check if path is directory or file
                    if os.path.isdir(agent_path):
                        # Load all .md files from directory
                        agents = await load_agents_from_directory(
                            agent_path,
                            plugin.get('name', ''),
                            plugin.get('source', ''),
                            plugin.get('path', ''),
                            plugin.get('manifest', {}),
                            loaded_paths,
                        )
                        
                        if len(agents) > 0:
                            logger.debug(
                                f"Loaded {len(agents)} agents from plugin "
                                f"{plugin.get('name', '')} custom path: {agent_path}"
                            )
                        plugin_agents.extend(agents)
                    
                    elif os.path.isfile(agent_path) and agent_path.endswith('.md'):
                        # Load single agent file
                        agent = await load_agent_from_file(
                            agent_path,
                            plugin.get('name', ''),
                            [],  # Empty namespace for single file
                            plugin.get('source', ''),
                            plugin.get('path', ''),
                            plugin.get('manifest', {}),
                            loaded_paths,
                        )
                        
                        if agent:
                            logger.debug(
                                f"Loaded agent from plugin {plugin.get('name', '')} "
                                f"custom file: {agent_path}"
                            )
                            plugin_agents.append(agent)
                
                except Exception as error:
                    logger.error(
                        f"Failed to load agents from plugin {plugin.get('name', '')} "
                        f"custom path {agent_path}: {error}"
                    )
        
        all_agents.extend(plugin_agents)
    
    logger.debug(f'Total plugin agents loaded: {len(all_agents)}')
    return all_agents


def clear_plugin_agent_cache() -> None:
    """Clear the plugin agent cache."""
    load_plugin_agents.cache_clear()
    logger.debug('Plugin agent cache cleared')
