"""
PlanBuildTool - Create structured plans in plan mode.

After exploring the codebase in plan mode, the AI uses PlanBuild to create
a structured implementation plan saved as a .md file with YAML frontmatter.
The plan renders as an interactive card in the chat UI with Build All support.
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict

# Defensive imports
try:
    from ...bootstrap.state import get_project_root
except ImportError:
    def get_project_root():
        return os.getcwd()

try:
    from ...Tool import buildTool, ToolDef
except ImportError:
    def buildTool(**kwargs):
        return kwargs

    class ToolDef:
        pass

try:
    from .constants import PLAN_BUILD_TOOL_NAME
except ImportError:
    PLAN_BUILD_TOOL_NAME = 'PlanBuild'

try:
    from .prompt import getPlanBuildToolPrompt
except ImportError:
    def getPlanBuildToolPrompt():
        return 'Creates a structured implementation plan with steps, saved as .md file.'

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PlanStep(TypedDict):
    title: str
    description: str
    files: List[str]


class Input(TypedDict):
    title: str
    overview: str
    steps: List[PlanStep]


class PlanStepOutput(TypedDict):
    id: str
    title: str
    description: str
    files: List[str]
    status: str


class Output(TypedDict):
    plan_id: str
    title: str
    overview: str
    status: str
    file_path: str
    steps: List[PlanStepOutput]
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_yaml_value(value: str) -> str:
    """Escape a string for YAML value — quote if it contains special chars."""
    if any(c in value for c in [':', '{', '}', '[', ']', ',', '&', '*', '?', '|', '-', '<', '>', '=', '!', '%', '@', '`', '"', "'", '#']):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return value


def _build_plan_markdown(plan_data: Dict[str, Any], plan_id: str) -> str:
    """Build a .md file with YAML frontmatter and human-readable body."""

    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    steps = plan_data.get('steps', [])

    # YAML frontmatter
    yaml_lines = [
        '---',
        f'id: {plan_id}',
        f"title: {_escape_yaml_value(plan_data['title'])}",
        'status: planning',
        f'created: "{timestamp}"',
        'steps:',
    ]
    for i, step in enumerate(steps):
        step_id = f'step_{i + 1}'
        yaml_lines.append(f'  - id: {step_id}')
        yaml_lines.append(f'    title: {_escape_yaml_value(step["title"])}')
        yaml_lines.append('    status: pending')
        files_str = json.dumps(step.get('files', [])).replace("'", "''")
        yaml_lines.append(f'    files: {files_str}')

    yaml_lines.append('---')
    yaml_lines.append('')

    # Markdown body
    md_lines = [
        f'# {plan_data["title"]}',
        '',
        '## Overview',
        plan_data.get('overview', ''),
        '',
        '## Steps',
        '',
    ]
    for i, step in enumerate(steps):
        files_list = ', '.join(f'`{f}`' for f in step.get('files', []))
        md_lines.append(f'### Step {i + 1}: {step["title"]}')
        md_lines.append(f'**Status**: pending')
        md_lines.append('')
        md_lines.append(step.get('description', ''))
        md_lines.append('')
        if files_list:
            md_lines.append(f'**Files**: {files_list}')
            md_lines.append('')

    return '\n'.join(yaml_lines) + '\n'.join(md_lines)


def _create_output(plan_data: Dict[str, Any], plan_id: str, file_path: str) -> Output:
    """Build the structured Output from plan data."""
    steps = plan_data.get('steps', [])
    step_outputs: List[PlanStepOutput] = []
    for i, step in enumerate(steps):
        step_outputs.append(PlanStepOutput(
            id=f'step_{i + 1}',
            title=step['title'],
            description=step.get('description', ''),
            files=step.get('files', []),
            status='pending',
        ))

    return Output(
        plan_id=plan_id,
        title=plan_data['title'],
        overview=plan_data.get('overview', ''),
        status='planning',
        file_path=file_path,
        steps=step_outputs,
        message=f'Plan "{plan_data["title"]}" created with {len(steps)} steps. Saved to {file_path}. The plan card is now visible in the chat UI — click "Build All" to auto-execute every step.',
    )


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

def isEnabled() -> bool:
    """PlanBuild is always enabled when EnterPlanMode is available."""
    return True


async def call(input_data: Input, context) -> Dict[str, Any]:
    """Execute PlanBuild — create a structured plan file and render in chat."""
    title = input_data.get('title', 'Untitled Plan')
    overview = input_data.get('overview', '')
    steps = input_data.get('steps', [])

    if not steps:
        return {
            'data': {
                'message': 'Plan requires at least one step.',
                'error': True,
            }
        }

    # Determine project root and plans directory
    try:
        project_root = get_project_root()
    except Exception:
        project_root = os.getcwd()

    plans_dir = os.path.join(project_root, 'plans')
    os.makedirs(plans_dir, exist_ok=True)

    # Generate plan ID and file path
    plan_id = f'plan_{uuid.uuid4().hex[:8]}'
    file_path = os.path.join(plans_dir, f'{plan_id}.md')

    # Build plan data dict
    plan_data = {
        'title': title,
        'overview': overview,
        'steps': steps,
    }

    # Write plan .md file
    plan_markdown = _build_plan_markdown(plan_data, plan_id)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(plan_markdown)

    # Build output
    output = _create_output(plan_data, plan_id, file_path)

    return {
        'data': dict(output),
    }


def mapToolResultToToolResultBlockParam(content: Output, toolUseID: str) -> Dict[str, Any]:
    """Map the tool output to an Anthropic API tool result block."""
    plan_id = content.get('plan_id', 'unknown')
    title = content.get('title', 'Untitled')
    step_count = len(content.get('steps', []))
    file_path = content.get('file_path', '')

    instructions = f'''Plan "{title}" created successfully ({step_count} steps).

The plan has been saved to: {file_path}

A plan card is now visible in the chat UI with all steps listed. The user can:
- Review each step and its files
- Click "Build All" to auto-execute all steps in order

Tell the user the plan is ready and ask if they want to Build it now.'''

    return {
        'type': 'tool_result',
        'content': instructions,
        'tool_use_id': toolUseID,
    }


# Build the tool definition
PlanBuildTool = buildTool(
    name=PLAN_BUILD_TOOL_NAME,
    description=getPlanBuildToolPrompt,
    inputSchema=Input,
    outputSchema=Output,
    call=call,
    isConcurrencySafe=False,
    isReadOnly=False,
    shouldDefer=True,
    searchHint='create structured implementation plans in plan mode',
    mapToolResultToToolResultBlockParam=mapToolResultToToolResultBlockParam,
)
