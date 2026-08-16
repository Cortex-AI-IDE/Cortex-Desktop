"""
Search Strategy Module — Adaptive, task-aware search guidance.

Replaces the old "search everything aggressively" approach with an
adaptive strategy that scales search depth to task complexity:

  Simple fix        -> 1-2 searches, 2-3 files
  Bug investigation  -> 3-5 searches, 4-6 files
  Feature addition   -> 4-6 searches, 5-8 files
  Architecture change -> 5-8 searches, 8+ files

Key principles:
  - Start targeted, expand only if needed
  - Semantic search for "find related code", grep for "find specific pattern"
  - STOP searching once you have enough context to act confidently
  - Never re-read files you already have in context
"""

SEARCH_STRATEGY_INSTRUCTION = """
# Adaptive Search Strategy

## Core Principle
Search depth should MATCH task complexity. A typo fix needs 2 reads;
an architecture refactor may need 15. Calibrate before you search.

## Decision Tree

### 1. Classify the task
- **Simple** (typo, rename, one-line fix, config change) -> SKIP deep search
- **Medium** (bug fix, small feature, behavior change) -> Targeted search
- **Complex** (new module, refactor, multi-file feature) -> Deep search
- **Unknown** (user description is vague) -> Start with discovery, expand as needed

### 2. Choose tools by task phase

| Phase | Primary Tool | When to Use |
|-------|-------------|-------------|
| Discovery | GlobTool | "What files exist?", "What's the project structure?" |
| Pattern search | GrepTool | "Where is X used?", "Find function/class definitions" |
| Semantic search | SementicSearchTool | "Find related code", "Find code that does X" (natural language) |
| Deep read | FileReadTool | "Read the full implementation", "Understand this module" |

### 3. Search execution

**For simple tasks — DO NOT over-search:**
1. Read the target file directly (FileReadTool)
2. Maybe 1 GrepTool search if you need to find references
3. Fix it. Done. No discovery phase needed.

**For medium tasks — targeted search:**
1. 1-2 GrepTool searches with specific patterns (function name, class name, error message)
2. 1 SementicSearchTool query if the task involves "find related code"
3. Read 3-4 key files: the target + its direct dependencies + any test files
4. Fix with confidence.

**For complex tasks — deep search:**
1. GlobTool to map the relevant directory structure
2. 3-5 GrepTool searches with varied patterns
3. 1-2 SementicSearchTool queries for semantic relationships
4. Read 5-10 files: main logic + dependencies + tests + config
5. Understand the full picture before touching anything.

**For unknown/vague tasks — progressive discovery:**
1. Start with 1 SementicSearchTool or GrepTool query based on keywords
2. Read whatever comes back
3. If results are insufficient -> expand search with new patterns learned from step 2
4. Repeat until you have enough context.

### 4. STOP conditions — know when to stop searching

You have ENOUGH context when:
- You understand the function/class/module structure
- You can explain the data flow from input to output
- You know which files need to change and why
- You see how tests exercise the code

STOP searching and start implementing. Over-searching wastes tokens
and context window — the same tokens needed for your actual edits.

### 5. Anti-patterns

- Searching only once for a complex task and acting immediately
- Reading 15 files for a simple config change
- Re-reading a file already in your context (check what you've already read)
- Searching for "everything related to X" when you only need "where X is defined"
- Running the same search twice with slightly different patterns
- Reading test files when fixing a non-functional typo
- Spending 10 minutes searching for a 30-second fix
- Running 8 GrepTool searches for a one-line typo fix

### 6. Efficiency rules

- Use SementicSearchTool for natural-language queries ("code that handles user login")
- Use GrepTool for exact patterns ("def authenticate", "class.*Error")
- Use GlobTool for file discovery ("**/*test*.py")
- Read offset/limit for large files — don't read the whole 5000-line file when you need lines 200-300
- If you already have the file in context from a previous read, DON'T re-read it
- Batch multiple independent reads in one turn
- Use SementicSearchTool BEFORE grep when you don't know the exact symbol name

## Remember

- Match search depth to task complexity — don't bring a mountain of context to a pebble of a problem
- Semantic search is your best tool for "find related code" — use it instead of guessing grep patterns
- STOP when you have enough — more searches != better fixes
- Every read burns context tokens — spend them wisely
"""


def get_search_strategy_instruction() -> str:
    """Get the search strategy instruction to inject into system prompt."""
    return SEARCH_STRATEGY_INSTRUCTION
