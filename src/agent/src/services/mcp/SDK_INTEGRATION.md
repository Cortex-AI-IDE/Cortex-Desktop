# MCP Client Python SDK Integration Guide

This document describes how to integrate the MCP Python SDK into `client.py` and test the implementation.

## Current Status

The `client.py` file contains **placeholder implementations** for SDK calls. These are commented out and simulate responses until the MCP Python SDK is integrated.

### Files Converted

| TypeScript File | Python File | Lines | Status |
|-----------------|-------------|-------|--------|
| `client.ts` | `client.py` | 3,349 → 3,408 | ✅ Complete |

---

## Step 1: Install MCP Python SDK

When the official MCP Python SDK is released, install it:

```bash
pip install mcp-sdk
# or if using poetry
poetry add mcp-sdk
```

### Required SDK Components

The following imports will be needed in `client.py`:

```python
# MCP SDK Imports (uncomment when SDK is available)
from mcp import Client
from mcp.client.sse import SSEClientTransport
from mcp.client.stdio import StdioClientTransport
from mcp.client.streamable_http import StreamableHTTPClientTransport
from mcp.types import (
    CallToolResultSchema,
    ElicitRequestSchema,
    ErrorCode,
    ListPromptsResultSchema,
    ListResourcesResultSchema,
    ListToolsResultSchema,
    McpError,
)
```

---

## Step 2: Enable Real SDK Calls

### 2.1 Update `call_mcp_tool` Function

**Location:** `client.py` lines 3084-3101

**Before (placeholder):**
```python
# Placeholder: simulate tool call
await asyncio.sleep(0.01)
result = {
    'content': [{'type': 'text', 'text': f'Tool {tool} executed successfully'}],
}
```

**After (real SDK call):**
```python
result = await asyncio.wait_for(
    sdk_client.call_tool(
        name=tool,
        arguments=args,
        _meta=meta,
        timeout=timeout_ms,
        onprogress=on_progress,
    ),
    timeout=timeout_seconds
)
```

### 2.2 Update `connect_to_server` Function

**Location:** `client.py` lines 686-850

**Before (placeholder):**
```python
# Placeholder transport creation
transport = None
```

**After (real transport):**
```python
if server_type == 'stdio':
    transport = StdioClientTransport(
        command=config.get('command'),
        args=config.get('args', []),
        env=config.get('env', {}),
    )
elif server_type == 'sse':
    transport = SSEClientTransport(
        url=config.get('url'),
        headers=get_mcp_server_headers(config),
    )
elif server_type in ('http', 'streamable-http'):
    transport = StreamableHTTPClientTransport(
        url=config.get('url'),
        headers=get_mcp_server_headers(config),
    )
```

### 2.3 Update `fetch_tools_for_client` Function

**Location:** `client.py` lines 1731-1850

**Before (placeholder):**
```python
# Placeholder: return empty tools list
return []
```

**After (real SDK call):**
```python
result = await client.client.request(
    {"method": "tools/list"},
    ListToolsResultSchema,
)
return recursively_sanitize_unicode(result.tools)
```

### 2.4 Update `fetch_resources_for_client` Function

**Location:** `client.py` lines 1864-1940

**Before (placeholder):**
```python
# Placeholder: return empty resources list
return []
```

**After (real SDK call):**
```python
result = await client.client.request(
    {"method": "resources/list"},
    ListResourcesResultSchema,
)
return result.resources
```

### 2.5 Update `fetch_commands_for_client` Function

**Location:** `client.py` lines 1940-1990

**Before (placeholder):**
```python
# Placeholder: return empty commands list
return []
```

**After (real SDK call):**
```python
result = await client.client.request(
    {"method": "prompts/list"},
    ListPromptsResultSchema,
)
prompts = recursively_sanitize_unicode(result.prompts)
# Transform prompts to commands...
```

---

## Step 3: Testing After SDK Integration

### 3.1 Unit Tests

Run existing phase tests:

```bash
cd src/services/mcp

# Run all phase tests
python -m pytest test_client_phase*.py -v

# Run specific phase
python -m pytest test_client_phase15.py -v
```

### 3.2 Integration Test Script

Create and run this test script:

```python
# test_sdk_integration.py
import asyncio
import sys
sys.path.insert(0, '.')

import client

async def test_sdk_integration():
    """Test real SDK integration"""
    
    # Test 1: Connect to a local MCP server
    print("Test 1: Connect to stdio server...")
    server_config = {
        'type': 'stdio',
        'command': 'python',
        'args': ['-m', 'mcp_server_example'],
        'scope': 'user',
    }
    
    connected = await client.connect_to_server('test-server', server_config)
    print(f"  Connected: {connected.get('type')}")
    assert connected.get('type') == 'connected', "Failed to connect"
    
    # Test 2: Fetch tools
    print("Test 2: Fetch tools...")
    tools = await client.fetch_tools_for_client(connected)
    print(f"  Tools count: {len(tools)}")
    
    # Test 3: Call a tool
    print("Test 3: Call tool...")
    result = await client.call_mcp_tool(
        client=connected,
        tool='example_tool',
        args={'input': 'test'},
    )
    print(f"  Result: {result}")
    
    # Test 4: Cleanup
    print("Test 4: Cleanup...")
    await client.cleanup_mcp_connection(connected)
    print("  Cleaned up successfully")
    
    print("\n✅ All SDK integration tests passed!")
    return True

if __name__ == '__main__':
    success = asyncio.run(test_sdk_integration())
    sys.exit(0 if success else 1)
```

### 3.3 Test Error Handling

```python
# test_error_handling.py
import asyncio
import client

async def test_401_error():
    """Test 401 Unauthorized error handling"""
    # Connect to a server that requires auth
    server_config = {
        'type': 'http',
        'url': 'https://api.example.com/mcp',
        'scope': 'user',
    }
    
    connected = await client.connect_to_server('auth-server', server_config)
    
    try:
        await client.call_mcp_tool(connected, 'protected_tool', {})
    except client.McpAuthError as e:
        print(f"✅ McpAuthError raised: {e.server_name}")
        return True
    
    return False

async def test_session_expired():
    """Test session expiry handling"""
    # Simulate expired session
    server_config = {
        'type': 'http',
        'url': 'https://api.example.com/mcp',
        'scope': 'user',
    }
    
    connected = await client.connect_to_server('expired-server', server_config)
    
    try:
        await client.call_mcp_tool(connected, 'test_tool', {})
    except client.McpSessionExpiredError as e:
        print(f"✅ McpSessionExpiredError raised: {e.server_name}")
        return True
    
    return False

async def test_timeout():
    """Test tool timeout handling"""
    server_config = {
        'type': 'stdio',
        'command': 'sleep',
        'args': ['999'],
        'scope': 'user',
    }
    
    # Set short timeout
    import os
    os.environ['MCP_TOOL_TIMEOUT'] = '1000'  # 1 second
    
    connected = await client.connect_to_server('slow-server', server_config)
    
    try:
        await client.call_mcp_tool(connected, 'slow_tool', {})
    except TimeoutError as e:
        print(f"✅ TimeoutError raised: {e}")
        return True
    
    return False

async def run_all_tests():
    results = []
    results.append(await test_401_error())
    results.append(await test_session_expired())
    results.append(await test_timeout())
    return all(results)

if __name__ == '__main__':
    success = asyncio.run(run_all_tests())
    print(f"\n{'✅' if success else '❌'} All error handling tests {'passed' if success else 'failed'}")
```

### 3.4 Test with Real MCP Server

```bash
# Install example MCP server
pip install mcp-server-filesystem

# Run integration test
python test_sdk_integration.py
```

---

## Step 4: Verification Checklist

After SDK integration, verify:

### Functionality
- [ ] `connect_to_server` connects to stdio servers
- [ ] `connect_to_server` connects to SSE servers
- [ ] `connect_to_server` connects to HTTP servers
- [ ] `fetch_tools_for_client` returns tool definitions
- [ ] `fetch_resources_for_client` returns resource definitions
- [ ] `fetch_commands_for_client` returns command definitions
- [ ] `call_mcp_tool` executes tools and returns results
- [ ] `call_mcp_tool` handles progress callbacks
- [ ] `call_mcp_tool` respects timeout settings

### Error Handling
- [ ] 401 errors raise `McpAuthError`
- [ ] Session expiry raises `McpSessionExpiredError`
- [ ] Tool errors raise `McpToolCallError`
- [ ] Timeouts raise `TimeoutError`
- [ ] Abort signals cancel operations

### Caching
- [ ] `connect_to_server` memoization works
- [ ] `fetch_tools_for_client` LRU cache works
- [ ] `clear_server_cache` clears cache
- [ ] `clear_mcp_connection_cache` clears all connections

---

## Directory Structure

```
src/services/mcp/
├── client.py              # Main client (3,408 lines)
├── client.ts              # Original TypeScript (3,349 lines)
├── SDK_INTEGRATION.md     # This file
├── test_client_phase1.py  # Phase 1 tests
├── test_client_phase2.py  # Phase 2 tests
├── ...                    # Phase 3-14 tests
├── test_client_phase15.py # Phase 15 tests
├── test_sdk_integration.py    # SDK integration tests
└── test_error_handling.py     # Error handling tests
```

---

## Placeholder Locations Summary

| Function | Line Range | Placeholder Type |
|----------|------------|------------------|
| `connect_to_server` | 686-850 | Transport creation |
| `fetch_tools_for_client` | 1731-1850 | SDK request |
| `fetch_resources_for_client` | 1864-1940 | SDK request |
| `fetch_commands_for_client` | 1940-1990 | SDK request |
| `call_mcp_tool` | 3084-3101 | SDK tool call |
| `setup_sdk_mcp_clients` | 3239-3322 | SDK client setup |
| `transform_result_content` | 2467-2598 | Image/audio processing |
| `persist_blob_to_text_block` | 2601-2662 | Binary persistence |
| `process_mcp_result` | 2788-2884 | Content truncation |

---

## Support

If tests fail after SDK integration:
1. Check SDK version compatibility
2. Verify import paths match SDK structure
3. Check async/await patterns
4. Review error type mappings
