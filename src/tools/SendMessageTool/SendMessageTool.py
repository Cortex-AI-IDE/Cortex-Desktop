"""
SendMessageTool - Inter-agent communication system for Cortex IDE

This module provides message passing capabilities between AI agents,
enabling team coordination and task delegation in multi-agent workflows.

Key Features:
- Direct messaging between agents by name
- Broadcast messages to all team members
- Structured protocol messages (shutdown requests, plan approvals)
- Message routing with metadata tracking
- Mailbox-based asynchronous delivery

Note: Simplified conversion focusing on core messaging logic.
Terminal-specific UI rendering and tmux integration removed.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union
import logging
import time
import json
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class MessageRouting:
    """Metadata about message routing for UI display."""
    sender: str
    target: str
    summary: Optional[str] = None
    content: Optional[str] = None
    sender_color: Optional[str] = None
    target_color: Optional[str] = None


@dataclass
class MessageOutput:
    """Output for direct message to single agent."""
    success: bool
    message: str
    routing: Optional[MessageRouting] = None


@dataclass
class BroadcastOutput:
    """Output for broadcast message to all agents."""
    success: bool
    message: str
    recipients: List[str] = field(default_factory=list)
    routing: Optional[MessageRouting] = None


@dataclass
class RequestOutput:
    """Output for structured request messages."""
    success: bool
    message: str
    request_id: str
    target: str


@dataclass
class ResponseOutput:
    """Output for structured response messages."""
    success: bool
    message: str
    request_id: Optional[str] = None


# Union type for all possible outputs
SendMessageOutput = Union[MessageOutput, BroadcastOutput, RequestOutput, ResponseOutput]


@dataclass
class ShutdownRequest:
    """Structured message: request agent shutdown."""
    type: str = 'shutdown_request'
    reason: Optional[str] = None


@dataclass
class ShutdownResponse:
    """Structured message: respond to shutdown request."""
    type: str = 'shutdown_response'
    request_id: str = ''
    approve: bool = False
    reason: Optional[str] = None


@dataclass
class PlanApprovalResponse:
    """Structured message: respond to plan approval request."""
    type: str = 'plan_approval_response'
    request_id: str = ''
    approve: bool = False
    feedback: Optional[str] = None


# Union type for structured messages
StructuredMessage = Union[ShutdownRequest, ShutdownResponse, PlanApprovalResponse]
MessageContent = Union[str, StructuredMessage]


@dataclass
class SendMessageInput:
    """Input schema for SendMessage tool."""
    to: str  # Recipient: agent name, "*" for broadcast
    message: MessageContent
    summary: Optional[str] = None  # 5-10 word preview for UI


@dataclass
class MailboxMessage:
    """Message stored in agent's mailbox."""
    from_agent: str
    text: str
    timestamp: str
    summary: Optional[str] = None
    color: Optional[str] = None


def generate_request_id(prefix: str, target: str) -> str:
    """
    Generate a unique request ID for structured messages.
    
    Args:
        prefix: Type prefix (e.g., 'shutdown', 'plan')
        target: Target agent name
        
    Returns:
        Unique request ID string
    """
    import uuid
    timestamp = int(time.time() * 1000)
    return f"{prefix}_{target}_{timestamp}_{uuid.uuid4().hex[:8]}"


async def write_to_mailbox(
    recipient_name: str,
    message: MailboxMessage,
    team_name: Optional[str] = None
) -> None:
    """
    Write a message to an agent's mailbox.
    
    In a full implementation, this would persist to disk or database.
    For now, uses in-memory storage (will be replaced with proper persistence).
    
    Args:
        recipient_name: Name of recipient agent
        message: Message to deliver
        team_name: Optional team context
    """
    # TODO: Implement proper mailbox persistence
    # For now, log the message (will integrate with Cortex IDE message queue)
    logger.info(
        f"Message to {recipient_name} "
        f"(team: {team_name or 'none'}): {message.text[:100]}..."
    )
    
    # Placeholder: In production, save to:
    # - SQLite database
    # - JSON file per agent
    # - Redis/pub-sub for distributed systems
    pass


async def handle_direct_message(
    recipient_name: str,
    content: str,
    summary: Optional[str],
    sender_name: str,
    sender_color: Optional[str] = None,
    recipient_color: Optional[str] = None,
    team_name: Optional[str] = None
) -> MessageOutput:
    """
    Handle direct message to a single agent.
    
    Args:
        recipient_name: Name of recipient agent
        content: Message content
        summary: Brief summary for UI preview
        sender_name: Name of sending agent
        sender_color: Color for sender (UI styling)
        recipient_color: Color for recipient (UI styling)
        team_name: Team context
        
    Returns:
        MessageOutput with delivery confirmation
    """
    # Create mailbox message
    mailbox_msg = MailboxMessage(
        from_agent=sender_name,
        text=content,
        timestamp=datetime.now().isoformat(),
        summary=summary,
        color=sender_color
    )
    
    # Deliver to mailbox
    await write_to_mailbox(recipient_name, mailbox_msg, team_name)
    
    # Build routing metadata for UI
    routing = MessageRouting(
        sender=sender_name,
        target=f"@{recipient_name}",
        summary=summary,
        content=content,
        sender_color=sender_color,
        target_color=recipient_color
    )
    
    return MessageOutput(
        success=True,
        message=f"Message sent to {recipient_name}'s inbox",
        routing=routing
    )


async def handle_broadcast(
    content: str,
    summary: Optional[str],
    sender_name: str,
    teammate_names: List[str],
    sender_color: Optional[str] = None,
    team_name: Optional[str] = None
) -> BroadcastOutput:
    """
    Broadcast message to all teammates except sender.
    
    Args:
        content: Message content
        summary: Brief summary for UI preview
        sender_name: Name of sending agent
        teammate_names: List of all teammate names
        sender_color: Color for sender (UI styling)
        team_name: Team context
        
    Returns:
        BroadcastOutput with recipient list
        
    Raises:
        ValueError: If not in team context or no teammates
    """
    if not team_name:
        raise ValueError(
            "Not in a team context. Create a team first."
        )
    
    # Filter out sender from recipients
    recipients = [
        name for name in teammate_names
        if name.lower() != sender_name.lower()
    ]
    
    if not recipients:
        return BroadcastOutput(
            success=True,
            message="No teammates to broadcast to (you are the only team member)",
            recipients=[]
        )
    
    # Send to each recipient
    for recipient_name in recipients:
        mailbox_msg = MailboxMessage(
            from_agent=sender_name,
            text=content,
            timestamp=datetime.now().isoformat(),
            summary=summary,
            color=sender_color
        )
        await write_to_mailbox(recipient_name, mailbox_msg, team_name)
    
    # Build routing metadata
    routing = MessageRouting(
        sender=sender_name,
        target="@team",
        summary=summary,
        content=content,
        sender_color=sender_color
    )
    
    return BroadcastOutput(
        success=True,
        message=f"Message broadcast to {len(recipients)} teammate(s): {', '.join(recipients)}",
        recipients=recipients,
        routing=routing
    )


async def handle_shutdown_request(
    target_name: str,
    sender_name: str,
    reason: Optional[str] = None,
    sender_color: Optional[str] = None,
    team_name: Optional[str] = None
) -> RequestOutput:
    """
    Send shutdown request to target agent.
    
    Args:
        target_name: Agent to request shutdown
        sender_name: Requesting agent name
        reason: Optional reason for shutdown
        sender_color: Color for sender
        team_name: Team context
        
    Returns:
        RequestOutput with request ID
    """
    request_id = generate_request_id('shutdown', target_name)
    
    # Create structured message
    shutdown_msg = ShutdownRequest(
        type='shutdown_request',
        reason=reason
    )
    
    # Serialize to JSON
    msg_json = json.dumps(shutdown_msg.__dict__)
    
    # Deliver to mailbox
    mailbox_msg = MailboxMessage(
        from_agent=sender_name,
        text=msg_json,
        timestamp=datetime.now().isoformat(),
        color=sender_color
    )
    await write_to_mailbox(target_name, mailbox_msg, team_name)
    
    return RequestOutput(
        success=True,
        message=f"Shutdown request sent to {target_name}. Request ID: {request_id}",
        request_id=request_id,
        target=target_name
    )


async def handle_shutdown_response(
    request_id: str,
    approve: bool,
    sender_name: str,
    reason: Optional[str] = None,
    sender_color: Optional[str] = None,
    team_name: Optional[str] = None
) -> ResponseOutput:
    """
    Respond to shutdown request (approve or reject).
    
    Args:
        request_id: ID from original request
        approve: Whether to approve shutdown
        sender_name: Responding agent name
        reason: Reason if rejecting
        sender_color: Color for sender
        team_name: Team context
        
    Returns:
        ResponseOutput with confirmation
    """
    # Create response message
    response_msg = ShutdownResponse(
        type='shutdown_response',
        request_id=request_id,
        approve=approve,
        reason=reason
    )
    
    # Serialize to JSON
    msg_json = json.dumps(response_msg.__dict__)
    
    # Deliver to team lead's mailbox
    mailbox_msg = MailboxMessage(
        from_agent=sender_name,
        text=msg_json,
        timestamp=datetime.now().isoformat(),
        color=sender_color
    )
    await write_to_mailbox('team-lead', mailbox_msg, team_name)
    
    if approve:
        message = f"Shutdown approved. Sent confirmation to team-lead. Agent {sender_name} is now exiting."
        # TODO: Trigger actual shutdown sequence
        logger.info(f"Agent {sender_name} approved shutdown - initiating exit")
    else:
        message = f'Shutdown rejected. Reason: "{reason}". Continuing to work.'
    
    return ResponseOutput(
        success=True,
        message=message,
        request_id=request_id
    )


async def handle_plan_approval_response(
    recipient_name: str,
    request_id: str,
    approve: bool,
    sender_name: str = 'team-lead',
    feedback: Optional[str] = None,
    team_name: Optional[str] = None
) -> ResponseOutput:
    """
    Respond to plan approval request (team lead only).
    
    Args:
        recipient_name: Agent who submitted plan
        request_id: ID from original request
        approve: Whether to approve plan
        sender_name: Should be 'team-lead'
        feedback: Feedback if rejecting plan
        team_name: Team context
        
    Returns:
        ResponseOutput with confirmation
        
    Raises:
        ValueError: If not called by team lead
    """
    # TODO: Verify sender is team lead
    # For now, assume caller has verified permissions
    
    # Create response message
    response_msg = PlanApprovalResponse(
        type='plan_approval_response',
        request_id=request_id,
        approve=approve,
        feedback=feedback
    )
    
    # Serialize to JSON
    msg_json = json.dumps(response_msg.__dict__)
    
    # Deliver to recipient's mailbox
    mailbox_msg = MailboxMessage(
        from_agent=sender_name,
        text=msg_json,
        timestamp=datetime.now().isoformat()
    )
    await write_to_mailbox(recipient_name, mailbox_msg, team_name)
    
    if approve:
        message = (
            f"Plan approved for {recipient_name}. "
            f"They will receive the approval and can proceed with implementation."
        )
    else:
        message = (
            f"Plan rejected for {recipient_name}. "
            f"Feedback: {feedback}"
        )
    
    return ResponseOutput(
        success=True,
        message=message,
        request_id=request_id
    )


async def send_message(
    input_data: SendMessageInput,
    sender_name: str,
    teammate_names: Optional[List[str]] = None,
    sender_color: Optional[str] = None,
    team_name: Optional[str] = None
) -> SendMessageOutput:
    """
    Main entry point for sending messages between agents.
    
    Handles three types of messages:
    1. Plain text messages (direct or broadcast)
    2. Structured requests (shutdown, plan approval)
    3. Structured responses (to above requests)
    
    Args:
        input_data: Message input with recipient, content, summary
        sender_name: Name of sending agent
        teammate_names: List of teammate names (for broadcast)
        sender_color: Color for sender (UI styling)
        team_name: Team context
        
    Returns:
        SendMessageOutput (varies by message type)
        
    Raises:
        ValueError: If message format is invalid
    """
    recipient = input_data.to.strip()
    
    # Determine if this is a broadcast
    is_broadcast = recipient == '*'
    
    # Check if message is structured (protocol message)
    is_structured = isinstance(input_data.message, dict)
    
    if is_structured:
        # Handle structured protocol messages
        msg_type = input_data.message.get('type')
        
        if msg_type == 'shutdown_request':
            if is_broadcast:
                raise ValueError("Cannot broadcast shutdown requests")
            
            return await handle_shutdown_request(
                target_name=recipient,
                sender_name=sender_name,
                reason=input_data.message.get('reason'),
                sender_color=sender_color,
                team_name=team_name
            )
        
        elif msg_type == 'shutdown_response':
            return await handle_shutdown_response(
                request_id=input_data.message['request_id'],
                approve=input_data.message['approve'],
                sender_name=sender_name,
                reason=input_data.message.get('reason'),
                sender_color=sender_color,
                team_name=team_name
            )
        
        elif msg_type == 'plan_approval_response':
            if is_broadcast:
                raise ValueError("Cannot broadcast plan approvals")
            
            return await handle_plan_approval_response(
                recipient_name=recipient,
                request_id=input_data.message['request_id'],
                approve=input_data.message['approve'],
                sender_name=sender_name,
                feedback=input_data.message.get('feedback'),
                team_name=team_name
            )
        
        else:
            raise ValueError(f"Unknown message type: {msg_type}")
    
    else:
        # Handle plain text messages
        content = input_data.message if isinstance(input_data.message, str) else str(input_data.message)
        
        if not content.strip():
            raise ValueError("Message content cannot be empty")
        
        # Validate summary for plain text messages
        if not input_data.summary:
            logger.warning("Summary recommended for plain text messages")
        
        if is_broadcast:
            # Broadcast to all teammates
            if not teammate_names:
                raise ValueError("Cannot broadcast: teammate list not provided")
            
            return await handle_broadcast(
                content=content,
                summary=input_data.summary,
                sender_name=sender_name,
                teammate_names=teammate_names,
                sender_color=sender_color,
                team_name=team_name
            )
        else:
            # Direct message to specific agent
            # TODO: Look up recipient color from team context
            recipient_color = None
            
            return await handle_direct_message(
                recipient_name=recipient,
                content=content,
                summary=input_data.summary,
                sender_name=sender_name,
                sender_color=sender_color,
                recipient_color=recipient_color,
                team_name=team_name
            )


def get_send_message_prompt() -> str:
    """
    Generate system prompt for SendMessage tool usage.
    
    Returns:
        Prompt text instructing AI on how to use SendMessage
    """
    return """
# SendMessage

Send a message to another agent.

```json
{"to": "researcher", "summary": "assign task 1", "message": "start on task #1"}
```

| `to` | |
|---|---|
| `"researcher"` | Teammate by name |
| `"*"` | Broadcast to all teammates — expensive (linear in team size), use only when everyone genuinely needs it |

Your plain text output is NOT visible to other agents — to communicate, you MUST call this tool. Messages from teammates are delivered automatically; you don't check an inbox. Refer to teammates by name, never by UUID. When relaying, don't quote the original — it's already rendered to the user.

## Protocol responses

If you receive a JSON message with `type: "shutdown_request"` or `type: "plan_approval_request"`, respond with the matching `_response` type — echo the `request_id`, set `approve` true/false:

```json
{"to": "team-lead", "message": {"type": "shutdown_response", "request_id": "...", "approve": true}}
{"to": "researcher", "message": {"type": "plan_approval_response", "request_id": "...", "approve": false, "feedback": "add error handling"}}
```

Approving shutdown terminates your process. Rejecting plan sends the teammate back to revise. Don't originate `shutdown_request` unless asked. Don't send structured JSON status messages — use TaskUpdate.
""".strip()


# Export public API
__all__ = [
    'SendMessageInput',
    'MessageOutput',
    'BroadcastOutput',
    'RequestOutput',
    'ResponseOutput',
    'SendMessageOutput',
    'MailboxMessage',
    'ShutdownRequest',
    'ShutdownResponse',
    'PlanApprovalResponse',
    'send_message',
    'handle_direct_message',
    'handle_broadcast',
    'handle_shutdown_request',
    'handle_shutdown_response',
    'handle_plan_approval_response',
    'write_to_mailbox',
    'get_send_message_prompt',
    'generate_request_id',
]
