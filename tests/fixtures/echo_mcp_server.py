"""Minimal MCP server used by the release suite to test MCPManager
end-to-end over real stdio: connect → list_tools → call_tool → shutdown."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("cortex-test-echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input text back."""
    return f"echo:{text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


if __name__ == "__main__":
    mcp.run(transport="stdio")
