"""
Change Detection & Review Types
OpenCode-style change tracking for Cortex IDE
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
from datetime import datetime


class ChangeType(Enum):
    """Type of file change"""
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class SemanticChangeType(Enum):
    """Semantic meaning of code changes"""
    BUG_FIX = "bug_fix"
    FEATURE_ADD = "feature_add"
    REFACTOR = "refactor"
    OPTIMIZATION = "optimization"
    SECURITY = "security"
    TEST = "test"
    DOCUMENTATION = "documentation"
    DEPENDENCY = "dependency"
    STYLE = "style"
    OTHER = "other"


class ReviewStatus(Enum):
    """Review status of a change"""
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"


class LineType(Enum):
    """Type of diff line"""
    CONTEXT = "context"
    ADDED = "added"
    REMOVED = "removed"
    HUNK_HEADER = "hunk_header"
    SEMANTIC_HEADER = "semantic_header"


@dataclass
class LineIndicator:
    """Indicator for a diff line"""
    type: str
    value: Any
    tooltip: str
    severity: str = "info"  # info, warning, error


@dataclass
class DiffLine:
    """Single line in a diff"""
    type: LineType
    content: str
    line_number: Dict[str, Optional[int]]  # original, new
    indicators: List[LineIndicator] = field(default_factory=list)
    is_collapsible: bool = False
    semantic_type: Optional[str] = None


@dataclass
class DiffHunk:
    """A hunk (section) of changes in a diff"""
    id: str
    original_start: int
    original_length: int
    new_start: int
    new_length: int
    lines: List[DiffLine]
    semantic_type: Optional[str] = None


@dataclass
class SemanticChange:
    """Semantic analysis of a change"""
    type: SemanticChangeType
    description: str
    impact: str  # low, medium, high
    risk: str  # low, medium, high
    confidence: float


@dataclass
class StructuredDiff:
    """Complete diff with metadata"""
    file_path: str
    language: str
    change_type: ChangeType
    lines: List[DiffLine]
    hunks: List[DiffHunk]
    semantic_changes: List[SemanticChange]
    confidence: float
    summary: Optional[Dict[str, Any]] = None


@dataclass
class FileChange:
    """Represents a changed file"""
    id: str
    path: str
    name: str
    directory: str
    change_type: ChangeType
    language: str
    original_content: str
    new_content: str
    diff: StructuredDiff
    additions: int
    deletions: int
    changes: int
    review_status: ReviewStatus
    semantic_changes: List[SemanticChange]
    created_at: datetime = field(default_factory=datetime.now)
    accepted_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    modified_content: Optional[str] = None
    rejection_reason: Optional[str] = None
    modification_notes: Optional[str] = None


@dataclass
class ChangeSet:
    """Collection of all changes in a session"""
    session_id: str
    files: List[FileChange]
    summary: Dict[str, Any]
    semantic_categories: List[Dict[str, Any]]
    ai_insights: List[str]
    overall_risk: str
    risk_breakdown: Dict[str, Any]
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ReviewDecision:
    """User decision on a change"""
    change_id: str
    action: str  # accept, reject, modify, defer
    reason: Optional[str] = None
    modification: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ReviewResult:
    """Result of a review session"""
    session_id: str
    total_changes: int
    accepted: int
    rejected: int
    modified: int
    final_diff: str
    commit_hash: Optional[str] = None


@dataclass
class Comment:
    """Review comment"""
    id: str
    session_id: str
    change_id: Optional[str]
    line_number: Optional[int]
    content: str
    author: str
    created_at: datetime
    status: str = "active"  # active, resolved
    resolved_at: Optional[datetime] = None
    resolution: Optional[str] = None
    reactions: List[Dict[str, Any]] = field(default_factory=list)


# View modes for diff display
VIEW_MODES = ["unified", "split", "inline", "semantic"]

# Language detection from file extension
LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
}
