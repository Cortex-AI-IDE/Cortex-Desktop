"""Hierarchical Task Graph — Phase 3 of Autonomous Enhancement.

Replaces flat task lists with a proper DAG:
- Parent tasks with subtask trees
- Explicit dependency edges (task B depends on task A)
- Status rollup from children to parent
- Critical path identification
- Circular dependency detection
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any, cast
from enum import Enum

import logging
log = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Standard task statuses matching the existing task_tools convention."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"

    @classmethod
    def from_str(cls, s: str) -> "TaskStatus":
        """Parse from string, case-insensitive."""
        for member in cls:
            if member.value == s.lower():
                return member
        return cls.PENDING


@dataclass
class TaskNode:
    """A single node in the task DAG."""
    id: str
    subject: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    active_form: Optional[str] = None
    owner: Optional[str] = None
    parent_id: Optional[str] = None
    depends_on: List[str] = field(default_factory=lambda: cast(List[str], []))  # Task IDs this task depends on
    estimated_effort: Optional[str] = None  # e.g. "30min", "2h", "3d"
    tags: List[str] = field(default_factory=lambda: cast(List[str], []))
    metadata: Dict[str, Any] = field(default_factory=lambda: cast(Dict[str, Any], {}))
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    # Subtask ordering (the order children were added)
    child_order: List[str] = field(default_factory=lambda: cast(List[str], []))

    @property
    def is_completed(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)

    @property
    def is_blocked(self) -> bool:
        return self.status == TaskStatus.BLOCKED

    @property
    def is_pending(self) -> bool:
        return self.status == TaskStatus.PENDING

    @property
    def is_in_progress(self) -> bool:
        return self.status == TaskStatus.IN_PROGRESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status.value,
            "activeForm": self.active_form or f"Working on: {self.subject}",
            "owner": self.owner,
            "parentId": self.parent_id,
            "dependsOn": list(self.depends_on),
            "estimatedEffort": self.estimated_effort,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "childOrder": list(self.child_order),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskNode":
        return cls(
            id=d["id"],
            subject=d["subject"],
            description=d.get("description", ""),
            status=TaskStatus.from_str(d.get("status", "pending")),
            active_form=d.get("activeForm"),
            owner=d.get("owner"),
            parent_id=d.get("parentId"),
            depends_on=list(d.get("dependsOn", [])),
            estimated_effort=d.get("estimatedEffort"),
            tags=list(d.get("tags", [])),
            metadata=dict(d.get("metadata", {})),
            created_at=d.get("createdAt", time.time()),
            updated_at=d.get("updatedAt", time.time()),
            completed_at=d.get("completedAt"),
            child_order=list(d.get("childOrder", [])),
        )


class TaskGraph:
    """Directed acyclic graph of tasks with hierarchical parent/child relationships."""

    def __init__(self) -> None:
        self._nodes: Dict[str, TaskNode] = {}

    # ── Node Management ──────────────────────────────────────────────

    def add_node(self, node: TaskNode) -> None:
        """Add a task node to the graph.

        If the node has a parent_id and the parent already exists, registers
        this node as a child. If other existing nodes reference this new
        node as their parent, they are registered as children.
        """
        self._nodes[node.id] = node
        # Register child with parent (if parent exists)
        if node.parent_id and node.parent_id in self._nodes:
            parent = self._nodes[node.parent_id]
            if node.id not in parent.child_order:
                parent.child_order.append(node.id)
        # Check if any existing nodes are children of this new node
        for existing in self._nodes.values():
            if existing.parent_id == node.id and existing.id != node.id:
                if existing.id not in node.child_order:
                    node.child_order.append(existing.id)
        log.debug(f"[TASK_GRAPH] Added node {node.id}: {node.subject}")

    def get_node(self, task_id: str) -> Optional[TaskNode]:
        """Get a task node by ID."""
        return self._nodes.get(task_id)

    def has_node(self, task_id: str) -> bool:
        return task_id in self._nodes

    def remove_node(self, task_id: str) -> bool:
        """Remove a task node and its children. Returns True if removed."""
        node = self._nodes.get(task_id)
        if node is None:
            return False

        # Remove children recursively
        children = self.get_direct_children(task_id)
        for child in children:
            self.remove_node(child.id)

        # Detach from parent
        if node.parent_id and node.parent_id in self._nodes:
            parent = self._nodes[node.parent_id]
            if task_id in parent.child_order:
                parent.child_order.remove(task_id)

        del self._nodes[task_id]
        log.debug(f"[TASK_GRAPH] Removed node {task_id}")
        return True

    def update_node(self, task_id: str, **updates: Any) -> Optional["TaskNode"]:
        """Update fields on a task node. Returns the node or None."""
        node = self._nodes.get(task_id)
        if node is None:
            return None

        for key, value in updates.items():
            if hasattr(node, key):
                setattr(node, key, value)

        node.updated_at = time.time()
        if updates.get("status") == TaskStatus.COMPLETED:
            node.completed_at = time.time()

        return node

    # ── Hierarchy Queries ────────────────────────────────────────────

    def get_direct_children(self, parent_id: str) -> List[TaskNode]:
        """Get direct children of a task, in insertion order."""
        parent = self._nodes.get(parent_id)
        if parent is None:
            return []
        children: List[TaskNode] = []
        for cid in parent.child_order:
            child = self._nodes.get(cid)
            if child:
                children.append(child)
        # Also include children added via depends_on parent_id that may not
        # be in child_order (backwards compat with flat task lists)
        for node in self._nodes.values():
            if node.parent_id == parent_id and node.id not in parent.child_order:
                children.append(node)
        return children

    def get_all_descendants(self, task_id: str) -> List[TaskNode]:
        """Get all descendants (children + grandchildren etc.) of a task."""
        result: List[TaskNode] = []
        for child in self.get_direct_children(task_id):
            result.append(child)
            result.extend(self.get_all_descendants(child.id))
        return result

    def get_ancestors(self, task_id: str) -> List[TaskNode]:
        """Get all ancestors (parent, grandparent, etc.) of a task."""
        result: List[TaskNode] = []
        node = self._nodes.get(task_id)
        while node and node.parent_id:
            parent = self._nodes.get(node.parent_id)
            if parent:
                result.append(parent)
                node = parent
            else:
                break
        return result

    def get_root_tasks(self) -> List[TaskNode]:
        """Get all tasks with no parent."""
        return [n for n in self._nodes.values() if n.parent_id is None]

    def get_task_depth(self, task_id: str) -> int:
        """How many levels deep this task is (0 = root)."""
        depth = 0
        node = self._nodes.get(task_id)
        while node and node.parent_id:
            depth += 1
            node = self._nodes.get(node.parent_id)
        return depth

    # ── Dependency Queries ───────────────────────────────────────────

    def get_blocked_tasks(self) -> List[TaskNode]:
        """Get all tasks that are currently blocked by incomplete dependencies."""
        blocked: List[TaskNode] = []
        for node in self._nodes.values():
            if node.is_completed:
                continue
            for dep_id in node.depends_on:
                dep = self._nodes.get(dep_id)
                if dep is None or not dep.is_completed:
                    blocked.append(node)
                    break
        return blocked

    def get_ready_tasks(self) -> List[TaskNode]:
        """Get all pending/in-progress tasks whose dependencies are met."""
        ready: List[TaskNode] = []
        for node in self._nodes.values():
            if node.is_completed or node.is_blocked:
                continue
            deps_met = all(
                (dep := self._nodes.get(did)) and dep.is_completed
                for did in node.depends_on
            )
            if deps_met:
                ready.append(node)
        return ready

    def get_dependency_chain(self, task_id: str) -> List[TaskNode]:
        """Get the full dependency chain for a task (dependencies, their deps, etc.)."""
        result: List[TaskNode] = []
        seen: Set[str] = set()

        def _walk(tid: str) -> None:
            if tid in seen:
                return
            seen.add(tid)
            node = self._nodes.get(tid)
            if node is None:
                return
            for dep_id in node.depends_on:
                dep = self._nodes.get(dep_id)
                if dep and dep.id not in seen:
                    result.append(dep)
                    _walk(dep.id)

        _walk(task_id)
        return result

    # ── Status Rollup ────────────────────────────────────────────────

    def get_rollup_status(self, task_id: str) -> Dict[str, int]:
        """Aggregate child statuses into counts for a parent task."""
        descendants = self.get_all_descendants(task_id)
        counts: Dict[str, int] = {
            "total": len(descendants),
            "pending": 0,
            "in_progress": 0,
            "completed": 0,
            "cancelled": 0,
            "blocked": 0,
        }
        for child in descendants:
            key = child.status.value
            if key in counts:
                counts[key] += 1
        return counts

    def is_completed(self, task_id: str) -> bool:
        """A task is completed if all its descendants are completed/cancelled (or it has none)."""
        node = self._nodes.get(task_id)
        if node is None:
            return False
        children = self.get_direct_children(task_id)
        if not children:
            return node.is_completed
        # Parent is done if all children are completed/cancelled
        # AND the parent itself is not pending/in_progress (unless it has no self-status)
        if node.is_completed:
            return True
        # Roll up: check if all descendants are done
        descendants = self.get_all_descendants(task_id)
        return all(d.is_completed for d in descendants) if descendants else node.is_completed

    # ── Critical Path ────────────────────────────────────────────────

    def get_critical_path(self) -> List[TaskNode]:
        """Find the longest chain of dependent pending tasks (critical path)."""
        pending = [n for n in self._nodes.values() if not n.is_completed]
        if not pending:
            return []

        # Build a simple adjacency: task -> its blockers (reverse of depends_on)
        blockers: Dict[str, List[str]] = {}
        for node in pending:
            for dep_id in node.depends_on:
                if dep_id not in blockers:
                    blockers[dep_id] = []
                blockers[dep_id].append(node.id)

        # Find root tasks (no deps, or all deps completed)
        roots = [n for n in pending if all(
            (dep := self._nodes.get(did)) and dep.is_completed
            for did in n.depends_on
        )]

        if not roots:
            return []

        # DFS to find longest chain
        longest: List[str] = []

        def _dfs(current_id: str, path: List[str]) -> None:
            nonlocal longest
            current_path = path + [current_id]
            children = blockers.get(current_id, [])
            if not children:
                if len(current_path) > len(longest):
                    longest = list(current_path)
                return
            for child_id in children:
                if child_id not in current_path:  # avoid cycles
                    _dfs(child_id, current_path)

        for root in roots:
            _dfs(root.id, [])

        return [self._nodes[nid] for nid in longest if nid in self._nodes]

    # ── Circular Dependency Detection ────────────────────────────────

    def detect_cycles(self) -> List[List[str]]:
        """Detect all cycles in the dependency graph using DFS.

        Returns a list of cycles, where each cycle is a list of task IDs.
        Each cycle is ordered (e.g. ['A', 'B', 'C'] means A→B→C→A).
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {nid: WHITE for nid in self._nodes}
        cycles: List[List[str]] = []
        path: List[str] = []

        def _dfs(tid: str) -> None:
            color[tid] = GRAY
            path.append(tid)

            node = self._nodes.get(tid)
            if node:
                for dep_id in node.depends_on:
                    if dep_id not in color:
                        continue
                    if color[dep_id] == GRAY:
                        # Found a cycle — extract it
                        cycle_start = path.index(dep_id)
                        cycles.append(list(path[cycle_start:]))
                    elif color[dep_id] == WHITE:
                        _dfs(dep_id)

            path.pop()
            color[tid] = BLACK

        for nid in list(self._nodes.keys()):
            if color[nid] == WHITE:
                _dfs(nid)

        return cycles

    def has_cycles(self) -> bool:
        """Check if the graph has any cycles."""
        return len(self.detect_cycles()) > 0

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": {nid: node.to_dict() for nid, node in self._nodes.items()},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskGraph":
        graph = cls()
        for nid, node_data in d.get("nodes", {}).items():
            graph._nodes[nid] = TaskNode.from_dict(node_data)
        return graph

    def get_all_tasks(self) -> List[TaskNode]:
        """Get all tasks in no particular order."""
        return list(self._nodes.values())

    def get_task_count(self) -> int:
        return len(self._nodes)

    # ── Summary for Prompt Injection ─────────────────────────────────

    def build_prompt_section(self) -> str:
        """Build a Markdown summary of the task graph for LLM prompt injection.

        Shows: root tasks with children indented, status, blocked info, critical path.
        """
        if not self._nodes:
            return ""

        lines = ["## Task Graph", ""]

        # Critical path
        critical_path = self.get_critical_path()
        if critical_path:
            cp_names = " → ".join(
                f"{n.subject[:40]} ({n.status.value})"
                for n in critical_path
            )
            lines.append(f"**Critical path:** {cp_names}")
            lines.append("")

        # Blocked tasks
        blocked = self.get_blocked_tasks()
        if blocked:
            lines.append(f"**Blocked tasks ({len(blocked)}):**")
            for b in blocked[:5]:
                deps = ", ".join(b.depends_on)
                lines.append(f"- `{b.id}` {b.subject[:60]} (waiting on: {deps})")
            if len(blocked) > 5:
                lines.append(f"  ... and {len(blocked) - 5} more")
            lines.append("")

        # Root tasks with hierarchy
        roots = self.get_root_tasks()
        if roots:
            lines.append("**Tasks:**")
            for root in roots:
                self._format_task_tree(root, lines, indent=0)

        lines.append("")
        return "\n".join(lines)

    def _format_task_tree(self, node: TaskNode, lines: List[str], indent: int = 0) -> None:
        """Recursively format a task and its children for prompt display."""
        prefix = "  " * indent
        status_icon = {
            TaskStatus.PENDING: "[ ]",
            TaskStatus.IN_PROGRESS: "[~]",
            TaskStatus.COMPLETED: "[✓]",
            TaskStatus.CANCELLED: "[✗]",
            TaskStatus.BLOCKED: "[!]",
        }.get(node.status, "[?]")

        effort = f" ({node.estimated_effort})" if node.estimated_effort else ""
        line = f"{prefix}{status_icon} `{node.id}` {node.subject[:60]}{effort}"
        lines.append(line)

        children = self.get_direct_children(node.id)
        for child in children:
            self._format_task_tree(child, lines, indent + 1)
