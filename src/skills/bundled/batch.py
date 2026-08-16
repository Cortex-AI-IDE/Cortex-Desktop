# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportRedeclaration=false, reportAssignmentType=false, reportAttributeAccessIssue=false, reportInvalidTypeForm=false, reportConstantRedefinition=false, reportUnusedImport=false
"""
Batch skill for parallel work orchestration across multiple agents.

Converts batch.ts to Python with multi-LLM compatibility for Cortex IDE.
"""

import logging
from typing import List, Dict, Any
import asyncio

log = logging.getLogger("cortex.agent")

# Defensive imports with fallback stubs
try:
    from ...tools.AgentTool.constants import AGENT_TOOL_NAME as _AGENT_TOOL_NAME
except ImportError:
    _AGENT_TOOL_NAME = "Agent"

try:
    from ...tools.AskUserQuestionTool.prompt import ASK_USER_QUESTION_TOOL_NAME as _ASK_USER_QUESTION_TOOL_NAME
except ImportError:
    _ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"

try:
    from ...tools.EnterPlanModeTool.constants import ENTER_PLAN_MODE_TOOL_NAME as _ENTER_PLAN_MODE_TOOL_NAME
except ImportError:
    _ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"

try:
    from ...tools.ExitPlanModeTool.constants import EXIT_PLAN_MODE_TOOL_NAME as _EXIT_PLAN_MODE_TOOL_NAME
except ImportError:
    _EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"

try:
    from ...tools.SkillTool.constants import SKILL_TOOL_NAME as _SKILL_TOOL_NAME
except ImportError:
    _SKILL_TOOL_NAME = "Skill"

try:
    from ...utils.git import get_is_git
except ImportError:
    async def get_is_git() -> bool:
        """Fallback git check - returns False if git utils not available."""
        return False

try:
    from ...skills.bundledSkills import register_bundled_skill as _register_bundled_skill
except ImportError:
    def _register_bundled_skill(definition: Dict[str, Any]) -> None:
        """Fallback stub for skill registration."""
        pass

# Create module-level aliases for use in f-strings and code
AGENT_TOOL_NAME = _AGENT_TOOL_NAME
ASK_USER_QUESTION_TOOL_NAME = _ASK_USER_QUESTION_TOOL_NAME
ENTER_PLAN_MODE_TOOL_NAME = _ENTER_PLAN_MODE_TOOL_NAME
EXIT_PLAN_MODE_TOOL_NAME = _EXIT_PLAN_MODE_TOOL_NAME
SKILL_TOOL_NAME = _SKILL_TOOL_NAME
register_bundled_skill = _register_bundled_skill


# Constants
MIN_AGENTS = 5
MAX_AGENTS = 30


# Worker instructions template
WORKER_INSTRUCTIONS = f"""After you finish implementing the change:
1. **Simplify** — Invoke the `{SKILL_TOOL_NAME}` tool with `skill: "simplify"` to review and clean up your changes.
2. **Run unit tests** — Run the project's test suite (check for package.json scripts, Makefile targets, or common commands like `npm test`, `bun test`, `pytest`, `go test`). If tests fail, fix them.
3. **Test end-to-end** — Follow the e2e test recipe from the coordinator's prompt (below). If the recipe says to skip e2e for this unit, skip it.
4. **Commit and push** — Commit all changes with a clear message, push the branch, and create a PR with `gh pr create`. Use a descriptive title. If `gh` is not available or the push fails, note it in your final message.
5. **Report** — End with a single line: `PR: <url>` so the coordinator can track it. If no PR was created, end with `PR: none — <reason>`."""


def build_prompt(instruction: str) -> str:
    """Build the batch orchestration prompt for the given instruction."""
    return f"""# Batch: Parallel Work Orchestration

You are orchestrating a large, parallelizable change across this codebase.

## User Instruction

{instruction}

## Phase 1: Research and Plan (Plan Mode)

Call the `{ENTER_PLAN_MODE_TOOL_NAME}` tool now to enter plan mode, then:

1. **Understand the scope.** Launch one or more subagents (in the foreground — you need their results) to deeply research what this instruction touches. Find all the files, patterns, and call sites that need to change. Understand the existing conventions so the migration is consistent.

2. **Decompose into independent units.** Break the work into {MIN_AGENTS}–{MAX_AGENTS} self-contained units. Each unit must:
   - Be independently implementable in an isolated git worktree (no shared state with sibling units)
   - Be mergeable on its own without depending on another unit's PR landing first
   - Be roughly uniform in size (split large units, merge trivial ones)

   Scale the count to the actual work: few files → closer to {MIN_AGENTS}; hundreds of files → closer to {MAX_AGENTS}. Prefer per-directory or per-module slicing over arbitrary file lists.

3. **Determine the e2e test recipe.** Figure out how a worker can verify its change actually works end-to-end — not just that unit tests pass. Look for:
   - A `cortex-in-chrome` skill or browser-automation tool (for UI changes: click through the affected flow, screenshot the result)
   - A `tmux` or AI agent-verifier skill (for AI agent changes: launch the app interactively, exercise the changed behavior)
   - A dev-server + curl pattern (for API changes: start the server, hit the affected endpoints)
   - An existing e2e/integration test suite the worker can run

   If you cannot find a concrete e2e path, use the `{ASK_USER_QUESTION_TOOL_NAME}` tool to ask the user how to verify this change end-to-end. Offer 2–3 specific options based on what you found (e.g., "Screenshot via chrome extension", "Run `bun run dev` and curl the endpoint", "No e2e — unit tests are sufficient"). Do not skip this — the workers cannot ask the user themselves.

   Write the recipe as a short, concrete set of steps that a worker can execute autonomously. Include any setup (start a dev server, build first) and the exact command/interaction to verify.

4. **Write the plan.** In your plan file, include:
   - A summary of what you found during research
   - A numbered list of work units — for each: a short title, the list of files/directories it covers, and a one-line description of the change
   - The e2e test recipe (or "skip e2e because …" if the user chose that)
   - The exact worker instructions you will give each agent (the shared template)

5. Call `{EXIT_PLAN_MODE_TOOL_NAME}` to present the plan for approval.

## Phase 2: Spawn Workers (After Plan Approval)

Once the plan is approved, spawn one background agent per work unit using the `{AGENT_TOOL_NAME}` tool. **All agents must use `isolation: "worktree"` and `run_in_background: true`.** Launch them all in a single message block so they run in parallel.

For each agent, the prompt must be fully self-contained. Include:
- The overall goal (the user's instruction)
- This unit's specific task (title, file list, change description — copied verbatim from your plan)
- Any codebase conventions you discovered that the worker needs to follow
- The e2e test recipe from your plan (or "skip e2e because …")
- The worker instructions below, copied verbatim:

```
{WORKER_INSTRUCTIONS}
```

Use `subagent_type: "general-purpose"` unless a more specific agent type fits.

## Phase 3: Track Progress

After launching all workers, render an initial status table:

| # | Unit | Status | PR |
|---|------|--------|----|
| 1 | <title> | running | — |
| 2 | <title> | running | — |

As background-agent completion notifications arrive, parse the `PR: <url>` line from each agent's result and re-render the table with updated status (`done` / `failed`) and PR links. Keep a brief failure note for any agent that did not produce a PR.

When all agents have reported, render the final table and a one-line summary (e.g., "22/24 units landed as PRs").
"""


# Error messages
NOT_A_GIT_REPO_MESSAGE = """This is not a git repository. The `/batch` command requires a git repo because it spawns agents in isolated git worktrees and creates PRs from each. Initialize a repo first, or run this from inside an existing one."""

MISSING_INSTRUCTION_MESSAGE = """Provide an instruction describing the batch change you want to make.

Examples:
  /batch migrate from react to vue
  /batch replace all uses of lodash with native equivalents
  /batch add type annotations to all untyped function parameters"""


async def get_prompt_for_command(args: str) -> List[Dict[str, Any]]:
    """
    Generate the prompt for the batch skill command.
    
    Args:
        args: The user's instruction for the batch operation
        
    Returns:
        List of content blocks for the AI model
    """
    instruction = args.strip()
    if not instruction:
        return [{"type": "text", "text": MISSING_INSTRUCTION_MESSAGE}]
    
    is_git = await get_is_git()
    if not is_git:
        return [{"type": "text", "text": NOT_A_GIT_REPO_MESSAGE}]
    
    return [{"type": "text", "text": build_prompt(instruction)}]


def register_batch_skill() -> None:
    """Register the batch skill with the bundled skills system."""
    register_bundled_skill({
        "name": "batch",
        "description": "Research and plan a large-scale change, then execute it in parallel across 5–30 isolated worktree agents that each open a PR.",
        "when_to_use": "Use when the user wants to make a sweeping, mechanical change across many files (migrations, refactors, bulk renames) that can be decomposed into independent parallel units.",
        "argument_hint": "<instruction>",
        "user_invocable": True,
        "disable_model_invocation": True,
        "get_prompt_for_command": get_prompt_for_command,
    })


# For direct execution/testing
if __name__ == "__main__":
    # Test the prompt generation
    async def test():
        result = await get_prompt_for_command("migrate from lodash to native equivalents")
        log.debug(result[0]["text"][:500] + "...")
    
    asyncio.run(test())
