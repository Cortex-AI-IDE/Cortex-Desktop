"""
Token estimation utilities for LLM API calls.

Provides rough token counting based on character/byte ratios.
Actual token counts vary by model and tokenizer.
"""


def roughTokenCountEstimation(content: str, bytesPerToken: int = 4) -> int:
    """
    Rough token count estimation based on character length.
    
    Default assumption: 1 token ≈ 4 bytes/characters for English text.
    This is a very rough estimate, actual tokens depend on the model's tokenizer.
    
    Args:
        content: Text content to estimate tokens for
        bytesPerToken: Average bytes per token (default 4 for English text)
        
    Returns:
        Estimated token count
    """
    if not content:
        return 0
    return round(len(content) / bytesPerToken)
