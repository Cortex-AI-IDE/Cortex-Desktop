# explore_agent.py
"""
Explore Agent - File search specialist for Cortex IDE.

Specialized in thoroughly navigating and exploring codebases.
"""

from __future__ import annotations

import os
from typing import List
from dataclasses import dataclass

# Project-specific imports
from ...BashTool.toolName import BASH_TOOL_NAME
from ...ExitPlanModeTool.constants import EXIT_PLAN_MODE_TOOL_NAME
from ...FileEditTool.constants import FILE_EDIT_TOOL_NAME
from ...FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
from ...GlobTool.prompt import GLOB_TOOL_NAME
from ...GrepTool.prompt import GREP_TOOL_NAME
from ...NotebookEditTool.constants import NOTEBOOK_EDIT_TOOL_NAME
from ..constants import AGENT_TOOL_NAME


def get_explore_system_prompt() -> str:
    """Generate the system prompt for the Explore agent."""
    # Ant-native builds alias find/grep to embedded bfs/ugrep and remove the
    # dedicated Glob/Grep tools, so point at find/grep via Bash instead.
    embedded = has_embedded_search_tools()
    glob_guidance = (
        f"- Use `find` via {BASH_TOOL_NAME} for broad file pattern matching"
        if embedded
        else f"- Use {GLOB_TOOL_NAME} for broad file pattern matching"
    )
    grep_guidance = (
        f"- Use `grep` via {BASH_TOOL_NAME} for searching file contents with regex"
        if embedded
        else f"- Use {GREP_TOOL_NAME} for searching file contents with regex"
    )

    return f"""You are a file search specialist for Cortex IDE. You excel at thoroughly navigating and exploring codebases.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code. You do NOT have access to file editing tools - attempting to edit files will fail.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
{glob_guidance}
{grep_guidance}
- Use {FILE_READ_TOOL_NAME} when you know the specific file path you need to read
- Use {BASH_TOOL_NAME} ONLY for read-only operations (ls, git status, git log, git diff, find{', grep' if embedded else ''}, cat, head, tail)
- NEVER use {BASH_TOOL_NAME} for: mkdir, touch, rm, cp, mv, git add, git commit, npm install, pip install, or any file creation/modification
- Adapt your search approach based on the thoroughness level specified by the caller
- Communicate your final report directly as a regular message - do NOT attempt to create files

NOTE: You are meant to be a fast agent that returns output as quickly as possible. In order to achieve this you must:
- Make efficient use of the tools that you have at your disposal: be smart about how you search for files and implementations
- Wherever possible you should try to spawn multiple parallel tool calls for grepping and reading files

Complete the user's search request efficiently and report your findings clearly."""


EXPLORE_AGENT_MIN_QUERIES = 3

EXPLORE_WHEN_TO_USE = (
    "Fast agent specialized for exploring codebases. Use this when you need to quickly find files by patterns "
    "(eg. \"src/components/**/*.tsx\"), search code for keywords (eg. \"API endpoints\"), or answer questions "
    "about the codebase (eg. \"how do API endpoints work?\"). When calling this agent, specify the desired "
    "thoroughness level: \"quick\" for basic searches, \"medium\" for moderate exploration, or \"very thorough\" "
    "for comprehensive analysis across multiple locations and naming conventions."
)


@dataclass
class BuiltInAgentDefinition:
    """Definition for a built-in agent."""
    agent_type: str
    when_to_use: str
    disallowed_tools: List[str]
    source: str
    base_dir: str
    model: str
    omit_cortex_md: bool = False
    get_system_prompt: callable = None


EXPLORE_AGENT = BuiltInAgentDefinition(
    agent_type="Explore",
    when_to_use=EXPLORE_WHEN_TO_USE,
    disallowed_tools=[
        AGENT_TOOL_NAME,
        EXIT_PLAN_MODE_TOOL_NAME,
        FILE_EDIT_TOOL_NAME,
        FILE_WRITE_TOOL_NAME,
        NOTEBOOK_EDIT_TOOL_NAME,
    ],
    source="built-in",
    base_dir="built-in",
    # Ants get inherit to use the main agent's model; external users get haiku for speed
    # Note: For ants, get_agent_model() checks tengu_explore_agent GrowthBook flag at runtime
    model="inherit" if os.environ.get("USER_TYPE") == "ant" else "haiku",
    # Explore is a fast read-only search agent — it doesn't need commit/PR/lint
    # rules from CORTEX.md. The main agent has full context and interprets results.
    omit_cortex_md=True,
    get_system_prompt=get_explore_system_prompt,
)
