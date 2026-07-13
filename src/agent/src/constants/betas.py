"""
constants/betas.py
Python conversion of constants/betas.ts (53 lines)

Anthropic API beta feature header constants.

Note: The TypeScript version uses `feature()` from 'bun:bundle' for feature
flags and `process.env.USER_TYPE` for environment checks. The Python version
uses plain constants — add feature flag checks when the feature system is
available.
"""

# Core beta headers
CORTEX_CODE_20250219_BETA_HEADER: str = "cortex-code-20250219"
INTERLEAVED_THINKING_BETA_HEADER: str = "interleaved-thinking-2025-05-14"
CONTEXT_1M_BETA_HEADER: str = "context-1m-2025-08-07"
CONTEXT_MANAGEMENT_BETA_HEADER: str = "context-management-2025-06-27"
STRUCTURED_OUTPUTS_BETA_HEADER: str = "structured-outputs-2025-12-15"
WEB_SEARCH_BETA_HEADER: str = "web-search-2025-03-05"

# Tool search beta headers differ by provider:
# - First-party API / Foundry: advanced-tool-use-2025-11-20
# - Vertex AI / Bedrock: tool-search-tool-2025-10-19
TOOL_SEARCH_BETA_HEADER_1P: str = "advanced-tool-use-2025-11-20"
TOOL_SEARCH_BETA_HEADER_3P: str = "tool-search-tool-2025-10-19"

EFFORT_BETA_HEADER: str = "effort-2025-11-24"
TASK_BUDGETS_BETA_HEADER: str = "task-budgets-2026-03-13"
PROMPT_CACHING_SCOPE_BETA_HEADER: str = "prompt-caching-scope-2026-01-05"
FAST_MODE_BETA_HEADER: str = "fast-mode-2026-02-01"
REDACT_THINKING_BETA_HEADER: str = "redact-thinking-2026-02-12"
TOKEN_EFFICIENT_TOOLS_BETA_HEADER: str = "token-efficient-tools-2026-03-28"

# Feature-flag gated betas (TS uses feature() from bun:bundle)
# TODO: Wire up feature flag checks when Python feature system is available
SUMMARIZE_CONNECTOR_TEXT_BETA_HEADER: str = "summarize-connector-text-2026-03-13"
AFK_MODE_BETA_HEADER: str = "afk-mode-2026-01-31"

# User-type gated beta (TS checks process.env.USER_TYPE === 'ant')
CLI_INTERNAL_BETA_HEADER: str = "cli-internal-2026-02-09"

ADVISOR_BETA_HEADER: str = "advisor-tool-2026-03-01"

# Bedrock only supports a limited number of beta headers and only through
# extraBodyParams. This set maintains the beta strings that should be in
# Bedrock extraBodyParams *and not* in Bedrock headers.
BEDROCK_EXTRA_PARAMS_HEADERS: frozenset[str] = frozenset({
    INTERLEAVED_THINKING_BETA_HEADER,
    CONTEXT_1M_BETA_HEADER,
    TOOL_SEARCH_BETA_HEADER_3P,
})

# Betas allowed on Vertex countTokens API.
# Other betas will cause 400 errors.
VERTEX_COUNT_TOKENS_ALLOWED_BETAS: frozenset[str] = frozenset({
    CORTEX_CODE_20250219_BETA_HEADER,
    INTERLEAVED_THINKING_BETA_HEADER,
    CONTEXT_MANAGEMENT_BETA_HEADER,
})
