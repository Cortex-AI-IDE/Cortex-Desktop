# ------------------------------------------------------------
# prompt.py
# Python conversion of SendMessageTool/prompt.ts
# 
# Description and prompt for SendMessageTool.
# Enables AI agents to communicate with each other.
# ------------------------------------------------------------

import os


def _is_uds_inbox_enabled() -> bool:
    """Check if UDS inbox feature is enabled."""
    # Check build-time feature flag (simulate with env var)
    return os.environ.get('FEATURE_UDS_INBOX', '0') == '1'


DESCRIPTION = 'Send a message to another agent'


def getPrompt() -> str:
    """
    Get the prompt for SendMessageTool.
    
    Includes UDS cross-session messaging if FEATURE_UDS_INBOX is enabled.
    """
    uds_inbox_enabled = _is_uds_inbox_enabled()
    
    uds_row = (
        '\n| `"uds:/path/to.sock"` | Local Claude session\'s socket (same machine; use `ListPeers`) |\n'
        '| `"bridge:session_..."` | Remote Control peer session (cross-machine; use `ListPeers`) |'
        if uds_inbox_enabled
        else ''
    )
    
    uds_section = (
        """

## Cross-session

Use `ListPeers` to discover targets, then:

```json
{"to": "uds:/tmp/cc-socks/1234.sock", "message": "check if tests pass over there"}
{"to": "bridge:session_01AbCd...", "message": "what branch are you on?"}
```

A listed peer is alive and will process your message — no "busy" state; messages enqueue and drain at the receiver's next tool round. Your message arrives wrapped as `<cross-session-message from="...">`. **To reply to an incoming message, copy its `from` attribute as your `to`.**"""
        if uds_inbox_enabled
        else ''
    )
    
    # Build the prompt with proper brace escaping
    # Use format with a placeholder to avoid f-string issues with JSON braces
    template = """
# SendMessage

Send a message to another agent.

```json
{"to": "researcher", "summary": "assign task 1", "message": "start on task #1"}
```

| `to` | |
|---|---|
| `"researcher"` | Teammate by name |
| `"*"` | Broadcast to all teammates — expensive (linear in team size), use only when everyone genuinely needs it |{uds_row}

Your plain text output is NOT visible to other agents — to communicate, you MUST call this tool. Messages from teammates are delivered automatically; you don't check an inbox. Refer to teammates by name, never by UUID. When relaying, don't quote the original — it's already rendered to the user.{uds_section}

## Protocol responses (legacy)

If you receive a JSON message with `type: "shutdown_request"` or `type: "plan_approval_request"`, respond with the matching `_response` type — echo the `request_id`, set `approve` true/false:

```json
{"to": "team-lead", "message": {"type": "shutdown_response", "request_id": "...", "approve": true}}
{"to": "researcher", "message": {"type": "plan_approval_response", "request_id": "...", "approve": false, "feedback": "add error handling"}}
```

Approving shutdown terminates your process. Rejecting plan sends the teammate back to revise. Don't originate `shutdown_request` unless asked. Don't send structured JSON status messages — use TaskUpdate.
""".strip()
    
    return template
