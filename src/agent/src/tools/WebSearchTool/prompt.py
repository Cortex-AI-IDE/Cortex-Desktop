"""WebSearch Tool - Prompt definitions."""
from datetime import datetime


WEB_SEARCH_TOOL_NAME = 'WebSearch'


def get_local_month_year() -> str:
    """Get the current month and year in local timezone."""
    now = datetime.now()
    return now.strftime('%B %Y')  # e.g., "April 2026"


def get_web_search_prompt() -> str:
    """Generate the WebSearch tool system prompt with current date."""
    current_month_year = get_local_month_year()
    
    return f"""
- Allows Cortex to search the web and use the results to inform responses
- Provides up-to-date information for current events and recent data
- Returns search result information formatted as search result blocks, including links as markdown hyperlinks
- Use this tool for accessing information beyond the AI model's knowledge cutoff
- Searches are performed automatically within a single API call

CRITICAL REQUIREMENT - You MUST follow this:
  - After answering the user's question, you MUST include a "Sources:" section at the end of your response
  - In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)
  - This is MANDATORY - never skip including sources in your response
  - Example format:

    [Your answer here]

    Sources:
    - [Source Title 1](https://example.com/1)
    - [Source Title 2](https://example.com/2)

Usage notes:
  - Domain filtering is supported to include or block specific websites
  - Primary: SerpAPI (Google results, requires SERPAPI_API_KEY, 100 free/month)
  - Fallback 1: DuckDuckGo HTML search (free, global, no API key required)
  - Fallback 2: DuckDuckGo Instant Answer API (free, limited results)
  - Fallback 3: Brave Search API if BRAVE_API_KEY env var is set

IMPORTANT - Use the correct year in search queries:
  - The current month is {current_month_year}. You MUST use this year when searching for recent information, documentation, or current events.
  - Example: If the user asks for "latest React docs", search for "React documentation" with the current year, NOT last year
"""
