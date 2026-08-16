"""
services/oauth/authCodeListener.py
Python conversion of services/oauth/auth-code-listener.ts (212 lines)

Temporary localhost HTTP server that listens for OAuth authorization code redirects.
When the user authorizes in their browser, the OAuth provider redirects to:
http://localhost:[port]/callback?code=AUTH_CODE&state=STATE

This server captures that redirect and extracts the auth code.
Note: This is NOT an OAuth server - it's just a redirect capture mechanism.
"""

import asyncio
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, parse_qs

try:
    from ...services.analytics.index import log_event
except ImportError:
    def log_event(event_name: str, metadata: dict = None):
        pass

try:
    from ...constants.oauth import get_oauth_config
except ImportError:
    def get_oauth_config():
        return {}

try:
    from ...utils.log import log_error
except ImportError:
    def log_error(error: Exception):
        logger.error(f"{error}")

try:
    from .oauthClient import should_use_cloud_ai_auth
except ImportError:
    def should_use_cloud_ai_auth(scopes):
        return False

logger = logging.getLogger(__name__)


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callback"""
    
    def __init__(self, *args, listener: 'AuthCodeListener' = None, **kwargs):
        self.listener = listener
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests"""
        if self.listener:
            self.listener.handle_request(self)
    
    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


class AuthCodeListener:
    """
    Temporary localhost HTTP server that listens for OAuth authorization code redirects.
    """
    
    def __init__(self, callback_path: str = '/callback'):
        """
        Initialize auth code listener.
        
        Args:
            callback_path: URL path to listen for callbacks (default: /callback)
        """
        self.callback_path = callback_path
        self.port: int = 0
        self.server: Optional[HTTPServer] = None
        self.promise_resolver: Optional[Callable[[str], None]] = None
        self.promise_rejecter: Optional[Callable[[Exception], None]] = None
        self.expected_state: Optional[str] = None
        self.pending_response: Optional[_OAuthCallbackHandler] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_task: Optional[asyncio.Task] = None
    
    async def start(self, port: Optional[int] = None) -> int:
        """
        Starts listening on an OS-assigned port and returns the port number.
        This avoids race conditions by keeping the server open until it's used.
        
        Args:
            port: Optional specific port to use. If not provided, uses OS-assigned port.
            
        Returns:
            Port number the server is listening on
        """
        self._loop = asyncio.get_event_loop()
        
        def run_server():
            """Run HTTP server in thread"""
            self.server = HTTPServer(('localhost', port or 0), lambda *args, **kwargs: _OAuthCallbackHandler(*args, listener=self, **kwargs))
            self.port = self.server.server_address[1]
            self.server.serve_forever()
        
        import threading
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        
        # Wait briefly for server to start
        await asyncio.sleep(0.1)
        
        if self.server is None:
            raise Exception('Failed to start OAuth callback server')
        
        return self.port
    
    def get_port(self) -> int:
        """Get the port number the server is listening on"""
        return self.port
    
    def has_pending_response(self) -> bool:
        """Check if there's a pending response waiting"""
        return self.pending_response is not None
    
    async def wait_for_authorization(
        self,
        state: str,
        on_ready: Callable[[], Any],
    ) -> str:
        """
        Wait for OAuth authorization code.
        
        Args:
            state: Expected state parameter for CSRF protection
            on_ready: Callback to call when server is ready
            
        Returns:
            Authorization code from OAuth redirect
        """
        self.expected_state = state
        
        # Call on_ready immediately (server is already running)
        result = on_ready()
        if asyncio.iscoroutine(result):
            await result
        
        # Wait for authorization code
        return await asyncio.get_event_loop().run_in_executor(
            None,
            self._wait_for_code,
        )
    
    def _wait_for_code(self) -> str:
        """Wait for authorization code (blocking)"""
        import queue
        code_queue = queue.Queue()
        
        def resolve(code: str):
            code_queue.put(('success', code))
        
        def reject(error: Exception):
            code_queue.put(('error', error))
        
        self.promise_resolver = resolve
        self.promise_rejecter = reject
        
        # Wait for result
        status, value = code_queue.get(timeout=300)  # 5 minute timeout
        
        self.promise_resolver = None
        self.promise_rejecter = None
        
        if status == 'error':
            raise value
        return value
    
    def handle_request(self, handler: _OAuthCallbackHandler):
        """
        Handle incoming HTTP request.
        
        Args:
            handler: HTTP request handler
        """
        parsed_url = urlparse(handler.path)
        
        if parsed_url.path != self.callback_path:
            handler.send_response(404)
            handler.end_headers()
            return
        
        query_params = parse_qs(parsed_url.query)
        auth_code = query_params.get('code', [None])[0]
        state = query_params.get('state', [None])[0]
        
        self.validate_and_respond(auth_code, state, handler)
    
    def validate_and_respond(
        self,
        auth_code: Optional[str],
        state: Optional[str],
        handler: _OAuthCallbackHandler,
    ):
        """
        Validate authorization code and state, then respond.
        
        Args:
            auth_code: Authorization code from URL
            state: State parameter from URL
            handler: HTTP request handler
        """
        if not auth_code:
            error_msg = b'Authorization code not found'
            handler.send_response(400)
            handler.send_header('Content-Type', 'text/plain')
            handler.send_header('Content-Length', len(error_msg))
            handler.end_headers()
            handler.wfile.write(error_msg)
            self._reject(Exception('No authorization code received'))
            return
        
        if state != self.expected_state:
            error_msg = b'Invalid state parameter'
            handler.send_response(400)
            handler.send_header('Content-Type', 'text/plain')
            handler.send_header('Content-Length', len(error_msg))
            handler.end_headers()
            handler.wfile.write(error_msg)
            self._reject(Exception('Invalid state parameter'))
            return
        
        # Store the handler for later redirect
        self.pending_response = handler
        
        # Resolve with auth code
        self._resolve(auth_code)
    
    def handle_error(self, error: Exception):
        """
        Handle server error.
        
        Args:
            error: The error that occurred
        """
        log_error(error)
        self.close()
        self._reject(error)
    
    def _resolve(self, authorization_code: str):
        """Resolve the authorization code promise"""
        if self.promise_resolver:
            self.promise_resolver(authorization_code)
            self.promise_resolver = None
            self.promise_rejecter = None
    
    def _reject(self, error: Exception):
        """Reject the authorization code promise"""
        if self.promise_rejecter:
            self.promise_rejecter(error)
            self.promise_resolver = None
            self.promise_rejecter = None
    
    def handle_success_redirect(
        self,
        scopes: list,
        custom_handler: Optional[Callable[[Any, list], None]] = None,
    ):
        """
        Complete the OAuth flow by redirecting the user's browser to a success page.
        
        Args:
            scopes: The OAuth scopes that were granted
            custom_handler: Optional custom handler to serve response instead of redirecting
        """
        if not self.pending_response:
            return
        
        # If custom handler provided, use it instead of default redirect
        if custom_handler:
            custom_handler(self.pending_response, scopes)
            self.pending_response = None
            log_event('tengu_oauth_automatic_redirect', {'custom_handler': True})
            return
        
        # Default behavior: Choose success page based on granted permissions
        config = get_oauth_config()
        success_url = (
            config.get('CLAUDEAI_SUCCESS_URL')
            if should_use_cloud_ai_auth(scopes)
            else config.get('CONSOLE_SUCCESS_URL')
        )
        
        # Send browser to success page
        self.pending_response.send_response(302)
        self.pending_response.send_header('Location', success_url)
        self.pending_response.end_headers()
        self.pending_response = None
        
        log_event('tengu_oauth_automatic_redirect', {})
    
    def handle_error_redirect(self):
        """
        Handle error case by sending a redirect to the appropriate success page with an error indicator.
        """
        if not self.pending_response:
            return
        
        # TODO: swap to a different url once we have an error page
        config = get_oauth_config()
        error_url = config.get('CLAUDEAI_SUCCESS_URL')
        
        # Send browser to error page
        self.pending_response.send_response(302)
        self.pending_response.send_header('Location', error_url)
        self.pending_response.end_headers()
        self.pending_response = None
        
        log_event('tengu_oauth_automatic_redirect_error', {})
    
    def close(self):
        """Close the server and clean up resources"""
        # If we have a pending response, send a redirect before closing
        if self.pending_response:
            self.handle_error_redirect()
        
        if self.server:
            self.server.shutdown()
            self.server = None


__all__ = [
    'AuthCodeListener',
]
