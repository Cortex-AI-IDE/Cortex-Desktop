"""
Diff Renderer with Semantic Analysis
OpenCode-style diff display for Cortex IDE
"""
import re
import difflib
from typing import List, Dict, Any, Optional, Tuple
from src.ai.changes.types import (
    StructuredDiff, DiffLine, DiffHunk, LineType, LineIndicator,
    SemanticChange, SemanticChangeType, ChangeType, LANGUAGE_EXTENSIONS
)


class DiffRenderer:
    """Renders diffs in multiple view modes"""
    
    def __init__(self, view_mode: str = "unified", theme: str = "dark"):
        self.view_mode = view_mode
        self.theme = theme
        self.collapse_threshold = 50  # lines
    
    def render_diff(self, original: str, modified: str, file_path: str,
                   semantic_changes: List[SemanticChange] = None) -> StructuredDiff:
        """Render a diff between original and modified content"""
        
        # Detect language
        language = self._detect_language(file_path)
        
        # Generate unified diff
        diff_text = self._generate_unified_diff(original, modified, file_path)
        
        # Parse diff into structured format
        lines, hunks = self._parse_unified_diff(diff_text)
        
        # Add line indicators
        lines = self._add_indicators(lines, language, semantic_changes)
        
        # Calculate confidence
        confidence = self._calculate_confidence(lines, semantic_changes)
        
        # Generate summary
        summary = self._generate_summary(lines)
        
        return StructuredDiff(
            file_path=file_path,
            language=language,
            change_type=ChangeType.MODIFIED if original else ChangeType.CREATED,
            lines=lines,
            hunks=hunks,
            semantic_changes=semantic_changes or [],
            confidence=confidence,
            summary=summary
        )
    
    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension"""
        import os
        _, ext = os.path.splitext(file_path.lower())
        return LANGUAGE_EXTENSIONS.get(ext, "text")
    
    def _generate_unified_diff(self, original: str, modified: str, 
                               file_path: str) -> str:
        """Generate unified diff text"""
        original_lines = original.splitlines(keepends=True) if original else []
        modified_lines = modified.splitlines(keepends=True) if modified else []
        
        # Ensure lines end with newline for proper diff
        if original_lines and not original_lines[-1].endswith('\n'):
            original_lines[-1] += '\n'
        if modified_lines and not modified_lines[-1].endswith('\n'):
            modified_lines[-1] += '\n'
        
        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm='\n'
        )
        
        return ''.join(diff)
    
    def _parse_unified_diff(self, diff_text: str) -> Tuple[List[DiffLine], List[DiffHunk]]:
        """Parse unified diff text into structured lines and hunks"""
        lines = []
        hunks = []
        current_hunk = None
        
        original_line_num = 0
        new_line_num = 0
        
        for line in diff_text.split('\n'):
            if not line:
                continue
            
            # Hunk header
            hunk_match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if hunk_match:
                # Save previous hunk
                if current_hunk:
                    hunks.append(current_hunk)
                
                orig_start = int(hunk_match.group(1))
                orig_len = int(hunk_match.group(2) or 1)
                new_start = int(hunk_match.group(3))
                new_len = int(hunk_match.group(4) or 1)
                
                original_line_num = orig_start
                new_line_num = new_start
                
                current_hunk = DiffHunk(
                    id=f"hunk_{len(hunks)}",
                    original_start=orig_start,
                    original_length=orig_len,
                    new_start=new_start,
                    new_length=new_len,
                    lines=[]
                )
                
                diff_line = DiffLine(
                    type=LineType.HUNK_HEADER,
                    content=line,
                    line_number={"original": None, "new": None},
                    is_collapsible=True
                )
                lines.append(diff_line)
                if current_hunk:
                    current_hunk.lines.append(diff_line)
                continue
            
            # Skip file headers
            if line.startswith('---') or line.startswith('+++'):
                continue
            
            # Determine line type
            if line.startswith('+'):
                line_type = LineType.ADDED
                line_content = line[1:]
                line_nums = {"original": None, "new": new_line_num}
                new_line_num += 1
            elif line.startswith('-'):
                line_type = LineType.REMOVED
                line_content = line[1:]
                line_nums = {"original": original_line_num, "new": None}
                original_line_num += 1
            else:
                line_type = LineType.CONTEXT
                line_content = line[1:] if line.startswith(' ') else line
                line_nums = {"original": original_line_num, "new": new_line_num}
                original_line_num += 1
                new_line_num += 1
            
            diff_line = DiffLine(
                type=line_type,
                content=line_content,
                line_number=line_nums
            )
            lines.append(diff_line)
            if current_hunk:
                current_hunk.lines.append(diff_line)
        
        # Add last hunk
        if current_hunk:
            hunks.append(current_hunk)
        
        return lines, hunks
    
    def _add_indicators(self, lines: List[DiffLine], language: str,
                       semantic_changes: List[SemanticChange] = None) -> List[DiffLine]:
        """Add visual indicators to diff lines"""
        for line in lines:
            if line.type in [LineType.ADDED, LineType.REMOVED]:
                indicators = []
                
                # Language indicator
                indicators.append(LineIndicator(
                    type="language",
                    value=language,
                    tooltip=f"Language: {language}"
                ))
                
                # Change type indicator
                indicators.append(LineIndicator(
                    type="change-type",
                    value=line.type.value,
                    tooltip="Added" if line.type == LineType.ADDED else "Removed"
                ))
                
                line.indicators = indicators
        
        return lines
    
    def _calculate_confidence(self, lines: List[DiffLine], 
                             semantic_changes: List[SemanticChange] = None) -> float:
        """Calculate confidence score for the diff"""
        if not lines:
            return 1.0
        
        # Base confidence
        confidence = 0.9
        
        # Reduce confidence for large changes
        change_lines = [l for l in lines if l.type in [LineType.ADDED, LineType.REMOVED]]
        if len(change_lines) > 100:
            confidence -= 0.1
        if len(change_lines) > 500:
            confidence -= 0.2
        
        # Adjust based on semantic analysis
        if semantic_changes:
            high_risk = sum(1 for sc in semantic_changes if sc.risk == "high")
            if high_risk > 0:
                confidence -= 0.1 * min(high_risk, 3)
        
        return max(0.5, min(1.0, confidence))
    
    def _generate_summary(self, lines: List[DiffLine]) -> Dict[str, Any]:
        """Generate summary statistics for the diff"""
        added = sum(1 for l in lines if l.type == LineType.ADDED)
        removed = sum(1 for l in lines if l.type == LineType.REMOVED)
        context = sum(1 for l in lines if l.type == LineType.CONTEXT)
        
        return {
            "additions": added,
            "deletions": removed,
            "context_lines": context,
            "total_changes": added + removed,
            "net_change": added - removed
        }
    
    def render_inline_diff(self, original: str, modified: str) -> str:
        """Render a simplified inline diff for display"""
        diff = self.render_diff(original, modified, "inline.txt")
        
        html_parts = []
        for line in diff.lines:
            if line.type == LineType.HUNK_HEADER:
                continue
            
            css_class = line.type.value
            prefix = " "
            if line.type == LineType.ADDED:
                prefix = "+"
            elif line.type == LineType.REMOVED:
                prefix = "-"
            
            escaped_content = self._escape_html(line.content)
            html_parts.append(
                f'<div class="diff-line {css_class}">'
                f'<span class="diff-prefix">{prefix}</span>'
                f'<span class="diff-content">{escaped_content}</span>'
                f'</div>'
            )
        
        return '\n'.join(html_parts)
    
    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters"""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))


class ChangeAnalyzer:
    """Analyzes changes for semantic meaning"""
    
    def analyze_semantic_changes(self, original: str, modified: str, 
                                 language: str) -> List[SemanticChange]:
        """Analyze changes for semantic meaning"""
        changes = []
        
        # Pattern-based semantic detection
        patterns = self._get_semantic_patterns(language)
        
        for pattern in patterns:
            if pattern["check"](original, modified):
                changes.append(SemanticChange(
                    type=pattern["type"],
                    description=pattern["description"],
                    impact=pattern["impact"],
                    risk=pattern["risk"],
                    confidence=pattern["confidence"]
                ))
        
        return changes
    
    def _get_semantic_patterns(self, language: str) -> List[Dict[str, Any]]:
        """Get semantic detection patterns for a language"""
        common_patterns = [
            {
                "type": SemanticChangeType.BUG_FIX,
                "check": lambda o, m: any(kw in m.lower() for kw in ["fix", "bug", "error", "exception"]),
                "description": "Bug fix detected",
                "impact": "high",
                "risk": "low",
                "confidence": 0.8
            },
            {
                "type": SemanticChangeType.FEATURE_ADD,
                "check": lambda o, m: any(kw in m.lower() for kw in ["add", "implement", "new", "feature"]),
                "description": "New feature added",
                "impact": "high",
                "risk": "medium",
                "confidence": 0.7
            },
            {
                "type": SemanticChangeType.REFACTOR,
                "check": lambda o, m: any(kw in m.lower() for kw in ["refactor", "rename", "extract", "move"]),
                "description": "Code refactoring",
                "impact": "medium",
                "risk": "low",
                "confidence": 0.75
            },
            {
                "type": SemanticChangeType.OPTIMIZATION,
                "check": lambda o, m: any(kw in m.lower() for kw in ["optimize", "performance", "speed", "cache"]),
                "description": "Performance optimization",
                "impact": "medium",
                "risk": "low",
                "confidence": 0.7
            },
            {
                "type": SemanticChangeType.SECURITY,
                "check": lambda o, m: any(kw in m.lower() for kw in ["security", "sanitize", "validate", "auth", "encrypt"]),
                "description": "Security-related change",
                "impact": "high",
                "risk": "medium",
                "confidence": 0.85
            },
            {
                "type": SemanticChangeType.TEST,
                "check": lambda o, m: any(kw in m.lower() for kw in ["test", "spec", "assert", "mock"]),
                "description": "Test-related change",
                "impact": "low",
                "risk": "low",
                "confidence": 0.9
            },
            {
                "type": SemanticChangeType.DOCUMENTATION,
                "check": lambda o, m: any(kw in m.lower() for kw in ["doc", "comment", "readme", "example"]),
                "description": "Documentation update",
                "impact": "low",
                "risk": "low",
                "confidence": 0.85
            },
        ]
        
        return common_patterns
