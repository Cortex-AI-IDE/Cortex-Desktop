"""
Agent tool filtering and lifecycle management for multi-agent coordination.

Ported from Claude Code's agentToolUtils.ts:
- filterToolsForAgent() -> Tool allowlists/blocklists per agent type
- resolveAgentTools() -> Tool definition validation
- runAsyncAgentLifecycle() -> Agent spawn/progress/complete lifecycle

Manages which tools are available to different agent types
(coordinator, worker, vision agent, code agent).
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional, Set, Callable

log = logging.getLogger("agent_tools")


# ==================== AGENT TYPES ====================

class AgentType(Enum):
    """Types of agents in the multi-agent system."""
    COORDINATOR = "coordinator"     # Orchestrates workers, synthesizes results
    VISION_WORKER = "vision"        # Image analysis specialist
    CODE_WORKER = "code"            # Code reading/writing specialist
    CONTEXT_WORKER = "context"      # Project context extraction
    RESEARCH_WORKER = "research"    # Codebase investigation
    IMPLEMENTATION_WORKER = "impl"  # Code modification
    VERIFICATION_WORKER = "verify"  # Testing and verification


class AgentStatus(Enum):
    """Agent lifecycle status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


# ==================== TOOL FILTERING (from agentToolUtils.ts) ====================

# Tools available to each agent type
# Ported from Claude Code's filterToolsForAgent() allowlists

TOOL_ALLOWLISTS: Dict[AgentType, Set[str]] = {
    AgentType.COORDINATOR: {
        "AgentTool",           # Spawn workers
        "SendMessageTool",     # Continue workers
        "TaskStopTool",        # Stop workers
        "FileRead",            # Read files for synthesis
        "Bash",                # Run commands for verification
    },
    AgentType.VISION_WORKER: {
        "VisionAnalyze",       # Core vision capability
        "FileRead",            # Read image files
        "MemoryStore",         # Store vision context
    },
    AgentType.CODE_WORKER: {
        "FileRead",            # Read code files
        "FileEdit",            # Edit code files
        "Bash",                # Run commands
        "GitCommit",           # Commit changes
        "GrepTool",            # Search code
        "ListDir",             # Browse directory
    },
    AgentType.CONTEXT_WORKER: {
        "FileRead",            # Read project files
        "ListDir",             # Browse directory structure
        "GrepTool",            # Search for patterns
        "SemanticSearch",      # Search codebase semantically
    },
    AgentType.RESEARCH_WORKER: {
        "FileRead",            # Read files
        "ListDir",             # Browse directory
        "GrepTool",            # Search patterns
        "Bash",                # Run read-only commands
        "SemanticSearch",      # Semantic search
    },
    AgentType.IMPLEMENTATION_WORKER: {
        "FileRead",            # Read files
        "FileEdit",            # Edit files
        "Bash",                # Run commands (build, test)
        "GitCommit",           # Commit changes
        "GrepTool",            # Search code
        "ListDir",             # Browse directory
    },
    AgentType.VERIFICATION_WORKER: {
        "FileRead",            # Read files
        "Bash",                # Run tests
        "GrepTool",            # Search for issues
        "ListDir",             # Check file structure
    },
}

# Tools that are NEVER available to workers (coordinator-only)
COORDINATOR_ONLY_TOOLS: Set[str] = {
    "AgentTool",
    "SendMessageTool",
    "TaskStopTool",
    "TeamCreate",
    "TeamDelete",
}

# Tools that are NEVER available to any agent (internal/dangerous)
BLOCKED_TOOLS: Set[str] = {
    "SyntheticOutput",     # Internal only
    "DestructiveCommand",  # Too dangerous for agents
}


def filter_tools_for_agent(
    available_tools: List[Dict[str, Any]],
    agent_type: AgentType,
    custom_allowlist: Optional[Set[str]] = None,
    custom_blocklist: Optional[Set[str]] = None
) -> List[Dict[str, Any]]:
    """Filter tools based on agent type.
    
    Ported from Claude Code's filterToolsForAgent().
    
    Args:
        available_tools: Full list of tool definitions
        agent_type: Type of agent requesting tools
        custom_allowlist: Optional additional allowed tools
        custom_blocklist: Optional additional blocked tools
    
    Returns:
        Filtered list of tool definitions
    """
    # Get base allowlist for this agent type
    allowlist = TOOL_ALLOWLISTS.get(agent_type, set()).copy()
    
    # Apply custom modifications
    if custom_allowlist:
        allowlist |= custom_allowlist
    
    # Build blocklist
    blocklist = BLOCKED_TOOLS.copy()
    if custom_blocklist:
        blocklist |= custom_blocklist
    
    # Non-coordinator agents can't use coordinator-only tools
    if agent_type != AgentType.COORDINATOR:
        blocklist |= COORDINATOR_ONLY_TOOLS
    
    # Filter
    filtered = []
    for tool in available_tools:
        tool_name = tool.get("name", tool.get("function", {}).get("name", ""))
        if tool_name in blocklist:
            continue
        if tool_name in allowlist:
            filtered.append(tool)
    
    log.info(f"Filtered tools for {agent_type.value}: {len(filtered)}/{len(available_tools)} tools available")
    return filtered


def get_tools_description_for_agent(agent_type: AgentType) -> str:
    """Get human-readable description of tools available to an agent type.
    
    Used in system prompts to inform agents of their capabilities.
    """
    tools = TOOL_ALLOWLISTS.get(agent_type, set())
    if not tools:
        return "No tools available."
    
    tool_descriptions = {
        "AgentTool": "Spawn new worker agents",
        "SendMessageTool": "Send follow-up instructions to existing workers",
        "TaskStopTool": "Stop a running worker",
        "FileRead": "Read file contents",
        "FileEdit": "Edit/create files",
        "Bash": "Execute shell commands",
        "GitCommit": "Commit changes to git",
        "GrepTool": "Search code with regex patterns",
        "ListDir": "List directory contents",
        "SemanticSearch": "Search codebase semantically",
        "VisionAnalyze": "Analyze images (OCR, object detection, description)",
        "MemoryStore": "Store data in session memory",
    }
    
    lines = [f"Available tools for {agent_type.value} agent:"]
    for tool_name in sorted(tools):
        desc = tool_descriptions.get(tool_name, tool_name)
        lines.append(f"  - {tool_name}: {desc}")
    
    return "\n".join(lines)


# ==================== AGENT LIFECYCLE (from agentToolUtils.ts) ====================

@dataclass
class AgentTask:
    """Represents a spawned agent task.
    
    Ported from Claude Code's async agent lifecycle management.
    """
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_type: AgentType = AgentType.RESEARCH_WORKER
    status: AgentStatus = AgentStatus.PENDING
    prompt: str = ""
    result: Optional[str] = None
    error: Optional[str] = None
    start_time: float = 0.0
    end_time: float = 0.0
    tool_calls: int = 0
    token_usage: int = 0
    
    @property
    def duration(self) -> float:
        """Task duration in seconds."""
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0.0
    
    def to_result_dict(self) -> Dict[str, Any]:
        """Convert to structured result for coordinator consumption."""
        return {
            "task_id": self.task_id,
            "agent_type": self.agent_type.value,
            "status": self.status.value,
            "summary": self._generate_summary(),
            "result": self.result,
            "error": self.error,
            "usage": {
                "duration_seconds": round(self.duration, 2),
                "tool_calls": self.tool_calls,
                "tokens": self.token_usage,
            }
        }
    
    def _generate_summary(self) -> str:
        """Generate human-readable status summary."""
        if self.status == AgentStatus.COMPLETED:
            return f"{self.agent_type.value} worker completed in {self.duration:.1f}s ({self.tool_calls} tool calls)"
        elif self.status == AgentStatus.FAILED:
            return f"{self.agent_type.value} worker failed: {self.error or 'Unknown error'}"
        elif self.status == AgentStatus.STOPPED:
            return f"{self.agent_type.value} worker was stopped"
        elif self.status == AgentStatus.RUNNING:
            return f"{self.agent_type.value} worker is running ({self.duration:.1f}s elapsed)"
        else:
            return f"{self.agent_type.value} worker is pending"


class AgentLifecycleManager:
    """Manages the lifecycle of spawned agents.
    
    Ported from Claude Code's runAsyncAgentLifecycle().
    Handles spawn, progress tracking, completion, and cleanup.
    """
    
    def __init__(self):
        self._tasks: Dict[str, AgentTask] = {}
        self._on_progress: Optional[Callable] = None
        self._on_complete: Optional[Callable] = None
    
    def set_callbacks(
        self,
        on_progress: Optional[Callable] = None,
        on_complete: Optional[Callable] = None
    ):
        """Set lifecycle callbacks."""
        self._on_progress = on_progress
        self._on_complete = on_complete
    
    def spawn_agent(
        self,
        agent_type: AgentType,
        prompt: str,
        task_id: Optional[str] = None
    ) -> AgentTask:
        """Spawn a new agent task.
        
        Args:
            agent_type: Type of agent to spawn
            prompt: Task prompt for the agent
            task_id: Optional custom task ID
        
        Returns:
            AgentTask with pending status
        """
        task = AgentTask(
            task_id=task_id or str(uuid.uuid4())[:8],
            agent_type=agent_type,
            status=AgentStatus.PENDING,
            prompt=prompt,
            start_time=time.time(),
        )
        self._tasks[task.task_id] = task
        log.info(f"[Lifecycle] Spawned {agent_type.value} agent: {task.task_id}")
        return task
    
    def start_agent(self, task_id: str):
        """Mark agent as running."""
        if task_id in self._tasks:
            self._tasks[task_id].status = AgentStatus.RUNNING
    
    def complete_agent(self, task_id: str, result: str):
        """Mark agent as completed with result."""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task.status = AgentStatus.COMPLETED
            task.result = result
            task.end_time = time.time()
            log.info(f"[Lifecycle] Agent {task_id} completed in {task.duration:.1f}s")
            if self._on_complete:
                self._on_complete(task)
    
    def fail_agent(self, task_id: str, error: str):
        """Mark agent as failed with error."""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task.status = AgentStatus.FAILED
            task.error = error
            task.end_time = time.time()
            log.warning(f"[Lifecycle] Agent {task_id} failed: {error}")
    
    def stop_agent(self, task_id: str):
        """Stop a running agent."""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task.status = AgentStatus.STOPPED
            task.end_time = time.time()
            log.info(f"[Lifecycle] Agent {task_id} stopped")
    
    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Get task by ID."""
        return self._tasks.get(task_id)
    
    def get_active_tasks(self) -> List[AgentTask]:
        """Get all currently running tasks."""
        return [t for t in self._tasks.values() 
                if t.status in (AgentStatus.PENDING, AgentStatus.RUNNING)]
    
    def get_all_results(self) -> List[Dict[str, Any]]:
        """Get structured results from all completed tasks."""
        return [t.to_result_dict() for t in self._tasks.values()
                if t.status in (AgentStatus.COMPLETED, AgentStatus.FAILED)]
    
    def cleanup(self):
        """Clean up completed and failed tasks."""
        to_remove = [
            tid for tid, task in self._tasks.items()
            if task.status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.STOPPED)
        ]
        for tid in to_remove:
            del self._tasks[tid]
        log.info(f"[Lifecycle] Cleaned up {len(to_remove)} tasks")


# ==================== WORKER PROMPT BUILDER ====================

def build_worker_system_prompt(agent_type: AgentType, project_path: str = None) -> str:
    """Build system prompt for a worker agent based on type.
    
    Each agent type gets a specialized system prompt that defines
    its role, capabilities, and constraints.
    """
    base = f"You are a {agent_type.value} worker agent in the Cortex IDE multi-agent system.\n\n"
    
    prompts = {
        AgentType.VISION_WORKER: (
            base +
            "Your role: Analyze images using vision capabilities.\n"
            "- Extract ALL text visible in the image (OCR)\n"
            "- Identify UI elements, buttons, dialogs, error messages\n"
            "- Describe code visible in screenshots\n"
            "- Detect diagrams, flowcharts, and their relationships\n"
            "- Report structural layout and visual hierarchy\n\n"
            "Output a structured analysis with sections:\n"
            "1. **Summary**: One-line description of the image\n"
            "2. **OCR Text**: All extracted text, preserving layout\n"
            "3. **Visual Elements**: UI components, icons, colors\n"
            "4. **Code/Errors**: Any code or error messages visible\n"
            "5. **Context**: What this image likely represents\n"
        ),
        AgentType.CODE_WORKER: (
            base +
            "Your role: Read, write, and modify code files.\n"
            "- Follow existing code style and conventions\n"
            "- Make minimal, targeted changes\n"
            "- Run tests after modifications\n"
            "- Commit with clear, descriptive messages\n"
            "- Report file paths and line numbers for all changes\n"
        ),
        AgentType.CONTEXT_WORKER: (
            base +
            "Your role: Extract project context and structure.\n"
            "- Map directory structure and key files\n"
            "- Identify frameworks, libraries, and patterns\n"
            "- Find configuration files and settings\n"
            "- Report findings concisely — do NOT modify files\n"
        ),
        AgentType.RESEARCH_WORKER: (
            base +
            "Your role: Investigate the codebase to understand a problem.\n"
            "- Search for relevant files, functions, and patterns\n"
            "- Trace code flow and dependencies\n"
            "- Identify root causes and affected areas\n"
            "- Report findings with specific file paths and line numbers\n"
            "- Do NOT modify any files — research only\n"
        ),
        AgentType.IMPLEMENTATION_WORKER: (
            base +
            "Your role: Implement code changes according to a specification.\n"
            "- Follow the spec exactly — don't improvise\n"
            "- Make targeted changes in specified files\n"
            "- Run relevant tests and typecheck\n"
            "- Commit changes and report the commit hash\n"
            "- Fix the root cause, not the symptom\n"
        ),
        AgentType.VERIFICATION_WORKER: (
            base +
            "Your role: Verify that code changes work correctly.\n"
            "- Prove the code works, don't just confirm it exists\n"
            "- Run tests with the feature enabled\n"
            "- Try edge cases and error paths\n"
            "- Investigate failures — don't dismiss as unrelated\n"
            "- Test independently with fresh eyes\n"
        ),
    }
    
    prompt = prompts.get(agent_type, base + "Follow your task instructions carefully.\n")
    
    if project_path:
        prompt += f"\nProject root: {project_path}\n"
    
    return prompt


# ==================== COORDINATOR DECISION HELPERS ====================

def should_use_parallel(
    has_images: bool,
    task_description: str,
    mode: str = "performance"
) -> bool:
    """Decide whether to run agents in parallel or sequential.
    
    Based on Claude Code's coordinator concurrency rules:
    - Vision tasks: ALWAYS sequential first (vision -> main)
    - Read-only research: Parallel freely
    - Write-heavy implementation: Sequential per file set
    
    Args:
        has_images: Whether images are involved
        task_description: Description of the task
        mode: Performance mode
    
    Returns:
        True for parallel execution, False for sequential
    """
    # Images ALWAYS require sequential (vision first)
    if has_images:
        return False
    
    # Ultimate mode defaults to parallel for non-vision
    if mode == "ultimate":
        return True
    
    # Performance mode: sequential for safety
    return False


def classify_task_type(text: str, has_images: bool) -> AgentType:
    """Classify the user's request into an agent type.
    
    Simple heuristic classifier to determine what kind of agent
    should handle this request.
    """
    text_lower = text.lower()
    
    if has_images:
        return AgentType.VISION_WORKER
    
    # Implementation keywords
    impl_keywords = ["fix", "implement", "add", "create", "write", "change", "modify", "update", "refactor"]
    if any(kw in text_lower for kw in impl_keywords):
        return AgentType.IMPLEMENTATION_WORKER
    
    # Research keywords
    research_keywords = ["find", "search", "where", "how", "why", "investigate", "trace", "debug"]
    if any(kw in text_lower for kw in research_keywords):
        return AgentType.RESEARCH_WORKER
    
    # Verification keywords
    verify_keywords = ["test", "verify", "check", "validate", "confirm"]
    if any(kw in text_lower for kw in verify_keywords):
        return AgentType.VERIFICATION_WORKER
    
    # Default to research
    return AgentType.RESEARCH_WORKER
