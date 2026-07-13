# ------------------------------------------------------------
# UI.py
# UI rendering functions for WebFetchTool.
# ------------------------------------------------------------

from typing import Any, Dict
from urllib.parse import urlparse


def get_tool_use_summary(input_data: Dict[str, Any]) -> str:
    """Return a short human-readable summary of the fetch target."""
    url: str = input_data.get('url', '')
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname:
            return hostname
    except Exception:
        pass
    return url


def render_tool_use_message(input_data: Dict[str, Any], *args: Any, **kwargs: Any) -> str:
    """Render a message shown when the tool is invoked."""
    summary = get_tool_use_summary(input_data)
    return f"Fetching {summary}" if summary else "Fetching web page"


def render_tool_use_progress_message(input_data: Dict[str, Any], *args: Any, **kwargs: Any) -> str:
    """Render a progress message shown while the tool is running."""
    summary = get_tool_use_summary(input_data)
    return f"Fetching {summary}…" if summary else "Fetching…"


def render_tool_result_message(output: Any, *args: Any, **kwargs: Any) -> str:
    """Render a message shown when the tool completes."""
    if isinstance(output, dict):
        data: Dict[str, Any] = output.get('data', output)
        code = data.get('code')
        url: str = data.get('url', '')
        bytes_count: int = data.get('bytes', 0)

        try:
            hostname = urlparse(url).hostname or url
        except Exception:
            hostname = url

        if code is not None:
            size_str = _format_size(bytes_count)
            return f"Fetched {hostname} ({code}, {size_str})"

    return "Fetched web page"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
