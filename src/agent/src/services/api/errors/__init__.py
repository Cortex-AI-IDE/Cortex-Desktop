from typing import Any

def categorize_retryable_api_error(error: Any) -> str:
    return "retryable"