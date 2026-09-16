"""
PlanBuildTool prompts and description.
"""

import os

try:
    from ...utils.planModeV2 import isPlanModeInterviewPhaseEnabled
except ImportError:
    def isPlanModeInterviewPhaseEnabled():
        return False


def getPlanBuildToolPrompt() -> str:
    """Generate the PlanBuildTool usage prompt for the AI agent."""
    return '''Use PlanBuild to create a structured implementation plan after exploring the codebase in plan mode.

## When to Use
Call PlanBuild AFTER you have:
1. Explored the codebase with Glob, Grep, and Read tools
2. Identified all files that need changes
3. Understood the architecture and existing patterns
4. Designed a concrete implementation approach

## Input Format
Provide a clear plan with:
- **title**: Short descriptive name for the plan
- **overview**: 2-3 sentence summary of the approach
- **steps**: Ordered list of implementation steps, each with:
  - **title**: Step name (action-oriented)
  - **description**: What to do and why
  - **files**: List of file paths this step will modify

## Example
```json
{
  "title": "Add User Authentication",
  "overview": "Implement JWT-based authentication with login endpoint, middleware, and token refresh.",
  "steps": [
    {
      "title": "Add auth dependencies",
      "description": "Install PyJWT and bcrypt, update requirements.txt",
      "files": ["requirements.txt"]
    },
    {
      "title": "Create auth middleware",
      "description": "JWT verification middleware that checks tokens on protected routes",
      "files": ["src/middleware/auth.py"]
    },
    {
      "title": "Add login API endpoint",
      "description": "POST /auth/login endpoint that validates credentials and returns JWT",
      "files": ["src/api/auth.py"]
    }
  ]
}
```

## What Happens
1. Plan is saved to `plans/plan_{id}.md` in the project root
2. A plan card appears in the chat UI showing all steps
3. Each step becomes a tracked todo item
4. User can click "Build All" to auto-execute every step in order
5. Progress updates live in the plan card

After calling PlanBuild, present the plan to the user and ask if they want to Build.'''
