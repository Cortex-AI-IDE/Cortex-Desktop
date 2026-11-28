"""
Token estimation utilities for LLM API calls.

Rough token counting from character/byte ratios; real counts vary by model.
"""


def roughTokenCountEstimation(content: str, bytesPerToken: int = 4) -> int:
    """
    Rough token count from character length, assuming ~4 bytes per token for
    English text. Very approximate; actual counts depend on the tokenizer.
    """
    if not content:
        return 0
    return round(len(content) / bytesPerToken)
