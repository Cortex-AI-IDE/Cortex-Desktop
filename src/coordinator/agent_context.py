"""
Agent context builder for multi-agent orchestration.

Provides utilities to build worker tool context and agent configuration
for AutoGen/OpenHands integration in Cortex IDE.
"""

from typing import List, Dict, Optional


# Default tools available to all workers
DEFAULT_WORKER_TOOLS = [
    "Bash",
    "FileRead", 
    "FileEdit",
    "GitCommit",
]

# Internal tools that coordinators use but workers don't need
INTERNAL_COORDINATOR_TOOLS = {
    "SendMessage",
    "TaskStop",
    "TeamCreate",
    "TeamDelete",
    "SyntheticOutput",
}


def get_worker_tool_context(
    mcp_servers: Optional[List[Dict[str, str]]] = None,
    simple_mode: bool = False
) -> Dict[str, str]:
    """
    Build context telling workers what tools they have access to.
    
    This is injected into the worker's system prompt so they know
    their capabilities.
    
    Args:
        mcp_servers: List of MCP server configs with 'name' key
        simple_mode: If True, use minimal tool set
    
    Returns:
        Dictionary with 'worker_tools_context' key containing formatted string
    """
    if mcp_servers is None:
        mcp_servers = []
    
    # Determine which tools workers have
    if simple_mode:
        worker_tools = sorted(["Bash", "FileRead", "FileEdit"])
    else:
        # In full mode, workers get all async agent tools except internal ones
        worker_tools = sorted(DEFAULT_WORKER_TOOLS)
    
    # Build context string
    content = f"Workers have access to these tools: {', '.join(worker_tools)}"
    
    # Add MCP servers if available
    if mcp_servers:
        server_names = ", ".join(server["name"] for server in mcp_servers)
        content += f"\n\nWorkers also have access to MCP tools from connected MCP servers: {server_names}"
    
    return {"worker_tools_context": content}


def format_worker_prompt(
    task_description: str,
    purpose: Optional[str] = None,
    file_paths: Optional[List[str]] = None,
    expected_output: Optional[str] = None
) -> str:
    """
    Format a well-structured worker prompt following best practices.
    
    Args:
        task_description: Clear description of what to do
        purpose: Why this task matters (helps worker calibrate depth)
        file_paths: Specific files to work with
        expected_output: What "done" looks like
    
    Returns:
        Formatted prompt string
    """
    parts = []
    
    # Add purpose statement if provided
    if purpose:
        parts.append(f"**Purpose:** {purpose}\n")
    
    # Add file context if provided
    if file_paths:
        parts.append(f"**Files to work with:**\n")
        for path in file_paths:
            parts.append(f"- {path}\n")
        parts.append("\n")
    
    # Add main task
    parts.append(f"**Task:**\n{task_description}\n")
    
    # Add expected output if provided
    if expected_output:
        parts.append(f"\n**Expected output:**\n{expected_output}")
    
    return "".join(parts)


def create_research_prompt(
    topic: str,
    scope: Optional[str] = None,
    deliverable: str = "Report findings — do not modify files"
) -> str:
    """
    Create a research-focused worker prompt.
    
    Args:
        topic: What to research
        scope: Optional scope limitation
        deliverable: What to report back
    
    Returns:
        Research prompt string
    """
    prompt = f"Investigate: {topic}"
    
    if scope:
        prompt += f"\n\nScope: {scope}"
    
    prompt += f"\n\n{deliverable}"
    
    return prompt


def create_implementation_prompt(
    spec: str,
    files: List[str],
    verification_steps: str = "Run relevant tests and typecheck, then commit your changes and report the hash"
) -> str:
    """
    Create an implementation-focused worker prompt.
    
    Args:
        spec: Detailed implementation specification
        files: Files to modify
        verification_steps: How to verify the implementation
    
    Returns:
        Implementation prompt string
    """
    prompt = f"**Implementation Spec:**\n{spec}\n\n"
    
    prompt += f"**Files to modify:**\n"
    for f in files:
        prompt += f"- {f}\n"
    
    prompt += f"\n**Verification:**\n{verification_steps}"
    
    return prompt


def create_verification_prompt(
    change_description: str,
    test_focus: Optional[str] = None,
    edge_cases: Optional[List[str]] = None
) -> str:
    """
    Create a verification-focused worker prompt.
    
    Args:
        change_description: What was changed
        test_focus: Specific areas to test
        edge_cases: Edge cases to check
    
    Returns:
        Verification prompt string
    """
    prompt = f"**Verify this change:**\n{change_description}\n\n"
    
    prompt += "**Verification requirements:**\n"
    prompt += "- Prove the code works, don't just confirm it exists\n"
    prompt += "- Try edge cases and error paths\n"
    prompt += "- Investigate failures — don't dismiss as unrelated without evidence\n"
    
    if test_focus:
        prompt += f"\n**Focus testing on:**\n{test_focus}\n"
    
    if edge_cases:
        prompt += f"\n**Check these edge cases:**\n"
        for case in edge_cases:
            prompt += f"- {case}\n"
    
    return prompt


def should_continue_worker(
    context_overlap: str,
    task_type: str = "implementation"
) -> bool:
    """
    Decide whether to continue an existing worker or spawn a fresh one.
    
    Args:
        context_overlap: Description of how much the worker's current context
                        overlaps with the next task ("high", "medium", "low", "none")
        task_type: Type of next task ("research", "implementation", "verification", "correction")
    
    Returns:
        True to continue same worker, False to spawn fresh
    """
    # Correction always continues (worker has error context)
    if task_type == "correction":
        return True
    
    # High overlap -> continue
    if context_overlap == "high":
        return True
    
    # Low/no overlap -> spawn fresh
    if context_overlap in ["low", "none"]:
        return False
    
    # Medium overlap depends on task type
    if task_type == "verification":
        # Verifiers should see code with fresh eyes
        return False
    elif task_type == "implementation":
        # Implementation can benefit from some context
        return True
    else:
        # Research - spawn fresh for clean exploration
        return False
