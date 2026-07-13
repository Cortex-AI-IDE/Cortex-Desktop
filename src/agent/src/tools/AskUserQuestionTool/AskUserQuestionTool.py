# AskUserQuestionTool.py
"""
AskUserQuestionTool for Cortex IDE.

Provides a tool for asking users multiple-choice questions to gather
preferences, clarify requirements, and make decisions during execution.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Any, TypedDict, Literal
from dataclasses import dataclass

from .prompt import (
    ASK_USER_QUESTION_TOOL_NAME,
    ASK_USER_QUESTION_TOOL_CHIP_WIDTH,
    ASK_USER_QUESTION_TOOL_PROMPT,
    DESCRIPTION,
    PREVIEW_FEATURE_PROMPT,
)


# ---------------------------------------------------------------------------
# Stub build_tool — matches the pattern used by WebFetchTool, WebSearchTool,
# etc.  The real TypeScript SDK's "buildTool" returns a typed tool descriptor;
# here we just store the kwargs on a plain object so the registry can import
# AskUserQuestionTool without a hard dependency on the full agent SDK.
# ---------------------------------------------------------------------------
class _ToolDescriptor:
    """Lightweight container returned by build_tool()."""
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


# Type alias used in create_ask_user_question_tool return annotation
Tool = _ToolDescriptor


def build_tool(**kwargs: Any) -> _ToolDescriptor:
    """Stub that mirrors the TypeScript buildTool() factory."""
    return _ToolDescriptor(**kwargs)


# ============================================================================
# TYPE DEFINITIONS
# ============================================================================

class QuestionOption(TypedDict, total=False):
    """Option for a multiple-choice question."""
    label: str  # Display text (1-5 words)
    description: str  # Explanation of what this option means
    preview: Optional[str]  # Optional preview content


class Question(TypedDict):
    """Multiple-choice question definition."""
    question: str  # The complete question to ask
    header: str  # Short label for chip/tag (max 12 chars)
    options: List[QuestionOption]  # 2-4 options
    multiSelect: bool  # Allow multiple selections


class Annotations(TypedDict, total=False):
    """Per-question annotations from user."""
    preview: Optional[str]  # Selected preview content
    notes: Optional[str]  # Free-text notes


@dataclass
class AskUserQuestionInput:
    """Tool input schema."""
    questions: List[Question]
    answers: Optional[Dict[str, str]] = None
    annotations: Optional[Dict[str, Annotations]] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class AskUserQuestionOutput:
    """Tool output schema."""
    questions: List[Question]
    answers: Dict[str, str]
    annotations: Optional[Dict[str, Annotations]]


# ============================================================================
# SCHEMA VALIDATION
# ============================================================================

def validate_unique_questions_and_labels(data: Dict[str, Any]) -> bool:
    """Validate that question texts and option labels are unique."""
    questions = [q['question'] for q in data.get('questions', [])]
    
    # Check unique questions
    if len(questions) != len(set(questions)):
        return False
    
    # Check unique labels within each question
    for question in data.get('questions', []):
        labels = [opt['label'] for opt in question.get('options', [])]
        if len(labels) != len(set(labels)):
            return False
    
    return True


def validate_html_preview(preview: Optional[str]) -> Optional[str]:
    """
    Lightweight HTML fragment validation.
    
    Checks model intent (did it emit HTML?) and catches specific issues.
    
    Returns:
        Error message if invalid, None if valid
    """
    if preview is None:
        return None
    
    # Check for full document wrappers
    if re.search(r'<\s*(html|body|!doctype)\b', preview, re.IGNORECASE):
        return (
            'preview must be an HTML fragment, not a full document '
            '(no <html>, <body>, or <!DOCTYPE>)'
        )
    
    # Disallow executable/style tags
    if re.search(r'<\s*(script|style)\b', preview, re.IGNORECASE):
        return (
            'preview must not contain <script> or <style> tags. '
            'Use inline styles via the style attribute if needed.'
        )
    
    # Must contain some HTML
    if not re.search(r'<[a-z][^>]*>', preview, re.IGNORECASE):
        return (
            'preview must contain HTML (previewFormat is set to "html"). '
            'Wrap content in a tag like <div> or <pre>.'
        )
    
    return None


# ============================================================================
# TOOL DEFINITION
# ============================================================================

def create_ask_user_question_tool() -> _ToolDescriptor:
    """Create and return the AskUserQuestion tool."""
    
    def get_description() -> str:
        return DESCRIPTION
    
    def get_prompt() -> str:
        # In real implementation, would check get_question_preview_format()
        # For now, return base prompt
        return ASK_USER_QUESTION_TOOL_PROMPT
    
    def is_enabled() -> bool:
        # Would check feature flags and channel restrictions
        # For now, always enabled
        return True
    
    def validate_input(input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate tool input."""
        # Check HTML preview format (would check get_question_preview_format())
        preview_format = None  # Placeholder
        
        if preview_format != 'html':
            return {'result': True}
        
        # Validate all previews
        for question in input_data.get('questions', []):
            for option in question.get('options', []):
                error = validate_html_preview(option.get('preview'))
                if error:
                    return {
                        'result': False,
                        'message': f'Option "{option["label"]}" in question "{question["question"]}": {error}',
                        'error_code': 1,
                    }
        
        return {'result': True}
    
    async def check_permissions(input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Check permissions for tool use."""
        return {
            'behavior': 'ask',
            'message': 'Answer questions?',
            'updated_input': input_data,
        }
    
    def to_auto_classifier_input(input_data: Dict[str, Any]) -> str:
        """Convert to classifier input string."""
        questions = [q['question'] for q in input_data.get('questions', [])]
        return ' | '.join(questions)
    
    async def call_tool(
        input_data: Dict[str, Any],
        context: Any,
    ) -> Dict[str, Any]:
        """Execute the tool."""
        questions = input_data.get('questions', [])
        answers = input_data.get('answers', {})
        annotations = input_data.get('annotations')
        
        result = {
            'data': {
                'questions': questions,
                'answers': answers,
            }
        }
        
        if annotations:
            result['data']['annotations'] = annotations
        
        return result
    
    def map_tool_result_to_tool_result_block_param(
        output_data: Dict[str, Any],
        tool_use_id: str,
    ) -> Dict[str, Any]:
        """Convert tool result to tool_result block param."""
        answers = output_data.get('answers', {})
        annotations = output_data.get('annotations')
        
        answer_parts = []
        for question_text, answer in answers.items():
            parts = [f'"{question_text}"="{answer}"']
            
            annotation = annotations.get(question_text) if annotations else None
            if annotation:
                if annotation.get('preview'):
                    parts.append(f"selected preview:\n{annotation['preview']}")
                if annotation.get('notes'):
                    parts.append(f"user notes: {annotation['notes']}")
            
            answer_parts.append(' '.join(parts))
        
        answers_text = ', '.join(answer_parts)
        
        return {
            'type': 'tool_result',
            'content': (
                f"User has answered your questions: {answers_text}. "
                f"You can now continue with the user's answers in mind."
            ),
            'tool_use_id': tool_use_id,
        }
    
    # Build and return tool
    return build_tool(
        name=ASK_USER_QUESTION_TOOL_NAME,
        search_hint='prompt the user with a multiple-choice question',
        max_result_size_chars=100_000,
        should_defer=True,
        description=get_description,
        prompt=get_prompt,
        is_enabled=is_enabled,
        is_concurrency_safe=True,
        is_read_only=True,
        requires_user_interaction=True,
        to_auto_classifier_input=to_auto_classifier_input,
        validate_input=validate_input,
        check_permissions=check_permissions,
        call=call_tool,
        map_tool_result_to_tool_result_block_param=map_tool_result_to_tool_result_block_param,
    )


# Create tool instance
AskUserQuestionTool = create_ask_user_question_tool()
