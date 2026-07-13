"""
Memory type definitions for multi-agent vision/OCR collaboration.

Defines memory scopes, vision context structures, and data models
for sharing vision analysis results between agents.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime


class MemoryScope(Enum):
    """Memory scope determines persistence and sharing level."""
    USER = "user"       # Cross-project, persistent (~/.cortex/agent-memory/)
    PROJECT = "project" # Project-specific, version controlled (.cortex/agent-memory/)
    SESSION = "session" # Session-specific, temporary (current conversation)
    LOCAL = "local"     # Machine-specific, not in VCS (.cortex/agent-memory-local/)


@dataclass
class VisionContext:
    """Structured vision analysis result for agent memory.
    
    This dataclass stores the output from Vision Agent analysis
    so that other agents (Main Agent, Code Agent, etc.) can access
    the vision data without re-processing the image.
    """
    image_path: str = ""                    # File path or identifier
    ocr_text: str = ""                      # Extracted text from OCR
    image_description: str = ""             # Detailed scene description
    detected_objects: List[str] = field(default_factory=list)  # Key elements detected
    analysis_timestamp: str = ""            # ISO format timestamp
    vision_model_used: str = ""             # e.g., "mistral", "siliconflow"
    confidence_score: float = 0.0           # Overall confidence (0.0 - 1.0)
    analysis_type: str = "full"             # ocr, description, object_detection, full
    session_id: str = ""                    # Session where analysis occurred
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "image_path": self.image_path,
            "ocr_text": self.ocr_text,
            "image_description": self.image_description,
            "detected_objects": self.detected_objects,
            "analysis_timestamp": self.analysis_timestamp,
            "vision_model_used": self.vision_model_used,
            "confidence_score": self.confidence_score,
            "analysis_type": self.analysis_type,
            "session_id": self.session_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'VisionContext':
        """Create VisionContext from dictionary."""
        return cls(
            image_path=data.get("image_path", ""),
            ocr_text=data.get("ocr_text", ""),
            image_description=data.get("image_description", ""),
            detected_objects=data.get("detected_objects", []),
            analysis_timestamp=data.get("analysis_timestamp", ""),
            vision_model_used=data.get("vision_model_used", ""),
            confidence_score=data.get("confidence_score", 0.0),
            analysis_type=data.get("analysis_type", "full"),
            session_id=data.get("session_id", "")
        )
    
    def is_empty(self) -> bool:
        """Check if context has no meaningful data."""
        return not any([
            self.ocr_text,
            self.image_description,
            self.detected_objects
        ])
    
    def format_for_prompt(self) -> str:
        """Format vision context for injection into agent system prompt."""
        if self.is_empty():
            return ""
        
        sections = ["## Vision Context from Image Analysis"]
        
        if self.image_path:
            sections.append(f"Image: {self.image_path}")
        
        if self.ocr_text:
            sections.append(f"\nOCR Extracted Text:\n{self.ocr_text}")
        
        if self.image_description:
            sections.append(f"\nImage Description:\n{self.image_description}")
        
        if self.detected_objects:
            objects_str = ", ".join(self.detected_objects)
            sections.append(f"\nDetected Objects: {objects_str}")
        
        sections.append(f"\nAnalysis Details:")
        sections.append(f"- Model: {self.vision_model_used}")
        sections.append(f"- Confidence: {self.confidence_score:.2f}")
        sections.append(f"- Analyzed at: {self.analysis_timestamp}")
        
        sections.append("\n**Use this vision context when answering questions about the image.**")
        
        return "\n".join(sections)


@dataclass
class AgentMemoryEntry:
    """Generic memory entry for agent context sharing."""
    key: str
    value: str
    scope: MemoryScope
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "key": self.key,
            "value": self.value,
            "scope": self.scope.value,
            "created_at": self.created_at,
            "metadata": self.metadata
        }
