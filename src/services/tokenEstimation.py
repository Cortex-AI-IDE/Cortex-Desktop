"""
Token estimation utilities for LLM API calls.

Provides rough token counting based on character/byte ratios.
Actual token counts vary by model and tokenizer.
"""

from typing import Any, Dict, List, Optional


def roughTokenCountEstimation(content: str, bytesPerToken: int = 4) -> int:
    """
    Rough token count estimation based on character length.
    
    Default assumption: 1 token ≈ 4 bytes/characters for English text.
    This is a very rough estimate — actual tokens depend on the model's tokenizer.
    
    Args:
        content: Text content to estimate tokens for
        bytesPerToken: Average bytes per token (default 4 for English text)
        
    Returns:
        Estimated token count
    """
    if not content:
        return 0
    return round(len(content) / bytesPerToken)


def bytesPerTokenForFileType(fileExtension: str) -> int:
    """
    Returns an estimated bytes-per-token ratio for a given file extension.
    
    Dense JSON has many single-character tokens which makes the real ratio
    closer to 2 rather than the default 4.
    
    Args:
        fileExtension: File extension without dot (e.g., 'json', 'py')
        
    Returns:
        Bytes per token estimate
    """
    extension = fileExtension.lower()
    if extension in ('json', 'jsonl'):
        return 2
    elif extension in ('xml', 'html'):
        return 3
    elif extension in ('py', 'js', 'ts', 'java', 'cpp', 'c', 'go', 'rs'):
        return 4
    else:
        return 4  # Default


def roughTokenCountEstimationForFileType(content: str, fileExtension: str) -> int:
    """
    Estimate tokens for content based on file type.
    
    Args:
        content: Text content
        fileExtension: File extension without dot
        
    Returns:
        Estimated token count
    """
    return roughTokenCountEstimation(content, bytesPerTokenForFileType(fileExtension))
