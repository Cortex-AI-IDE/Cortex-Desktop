"""
Permission Card Component for Chat UI
Generates HTML/JSON for permission request cards
"""

import json
from typing import Dict, Any, Optional, Callable
from src.agent.src.ai.permission.types import PermissionCardData, PermissionScope
from src.utils.logger import get_logger

log = get_logger("permission_card")


class PermissionCardRenderer:
    """
    Renders permission cards for the chat UI.
    Generates HTML/JavaScript code for interactive permission cards.
    """
    
    def __init__(self):
        self._callbacks: Dict[str, Callable] = {}
    
    def render(self, card_data: PermissionCardData) -> str:
        """
        Render a permission card as HTML.
        
        Returns:
            HTML string for the permission card
        """
        # Convert to dictionary
        data = card_data.to_dict()
        
        # Generate HTML
        html = f"""
        <div class="permission-card" id="perm-card-{data['request_id']}" data-request-id="{data['request_id']}">
            <div class="permission-header">
                <div class="permission-icon">🔒</div>
                <div class="permission-title-section">
                    <h3 class="permission-title">{data['title']}</h3>
                    <p class="permission-subtitle">{data['tool']} • {data['description']}</p>
                </div>
            </div>
            
            <div class="permission-content">
                <div class="permission-section">
                    <h4>Requested Access:</h4>
                    <div class="permission-tags">
                        {''.join(f'<span class="perm-tag">{access}</span>' for access in data['requested_access'])}
                    </div>
                </div>
                
                <div class="permission-columns">
                    <div class="permission-risks">
                        <h4>⚠️ Potential Risks</h4>
                        <ul>
                            {''.join(f'<li>{risk}</li>' for risk in data['risks'])}
                        </ul>
                    </div>
                    
                    <div class="permission-safeguards">
                        <h4>🛡️ Safety Measures</h4>
                        <ul>
                            {''.join(f'<li>{safeguard}</li>' for safeguard in data['safeguards'])}
                        </ul>
                    </div>
                </div>
                
                <div class="permission-scope-section">
                    <h4>Permission Duration:</h4>
                    <div class="scope-options">
                        <button class="scope-btn scope-btn-session active" data-scope="session">
                            <span class="scope-label">This Session Only</span>
                            <span class="scope-desc">Expires when you close OpenCode</span>
                        </button>
                        <button class="scope-btn scope-btn-workspace" data-scope="workspace">
                            <span class="scope-label">Current Workspace</span>
                            <span class="scope-desc">Valid for this project only</span>
                        </button>
                        <button class="scope-btn scope-btn-global" data-scope="global">
                            <span class="scope-label">Always Allow</span>
                            <span class="scope-desc">Remember for all projects</span>
                        </button>
                    </div>
                </div>
            </div>
            
            <div class="permission-actions">
                <button class="permission-btn permission-btn-deny">Deny</button>
                <button class="permission-btn permission-btn-allow">Allow</button>
                <button class="permission-btn permission-btn-always">Always</button>
            </div>
            
            <div class="permission-actions-secondary">
                <label class="remember-toggle">
                    <input type="checkbox" class="permission-remember-checkbox">
                    <span class="toggle-slider"></span>
                    <span class="remember-text">Remember my choice</span>
                </label>
            </div>
        </div>
        
        <style>
            .permission-card {{
                border: 2px solid #f59e0b;
                border-radius: 12px;
                background: linear-gradient(135deg, #422006 0%, #78350f 100%);
                margin: 16px 0;
                overflow: hidden;
                font-family: system-ui, -apple-system, sans-serif;
                max-width: 600px;
            }}
            
            .permission-header {{
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 16px;
                background: rgba(245, 158, 11, 0.1);
                border-bottom: 1px solid rgba(245, 158, 11, 0.2);
            }}
            
            .permission-icon {{
                font-size: 24px;
                width: 40px;
                height: 40px;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #1e1e1e;
            }}
            
            .permission-title {{
                margin: 0;
                font-size: 16px;
                font-weight: 600;
                color: #fbbf24;
            }}
            
            .permission-subtitle {{
                margin: 4px 0 0 0;
                font-size: 13px;
                color: #f59e0b;
            }}
            
            .permission-content {{
                padding: 16px;
            }}
            
            .permission-section {{
                margin-bottom: 16px;
            }}
            
            .permission-section h4 {{
                margin: 0 0 8px 0;
                font-size: 13px;
                font-weight: 600;
                color: #d4d4d4;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            
            .permission-tags {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
            }}
            
            .perm-tag {{
                background: #2d2d2d;
                border: 1px solid #f59e0b;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 12px;
                color: #fbbf24;
            }}
            
            .permission-columns {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 16px;
                margin-bottom: 16px;
            }}
            
            .permission-risks, .permission-safeguards {{
                background: #1e1e1e;
                padding: 12px;
                border-radius: 8px;
            }}
            
            .permission-risks {{
                border-left: 3px solid #ef4444;
            }}
            
            .permission-safeguards {{
                border-left: 3px solid #10b981;
            }}
            
            .permission-risks h4 {{
                color: #fca5a5;
                margin: 0 0 8px 0;
                font-size: 12px;
            }}
            
            .permission-safeguards h4 {{
                color: #6ee7b7;
                margin: 0 0 8px 0;
                font-size: 12px;
            }}
            
            .permission-risks ul, .permission-safeguards ul {{
                margin: 0;
                padding-left: 16px;
                font-size: 12px;
                color: #d1d5db;
            }}
            
            .permission-risks li, .permission-safeguards li {{
                margin-bottom: 4px;
            }}
            
            .scope-options {{
                display: flex;
                flex-direction: column;
                gap: 8px;
            }}
            
            .scope-btn {{
                display: flex;
                flex-direction: column;
                align-items: flex-start;
                padding: 10px 12px;
                border: 2px solid #3d3d3d;
                border-radius: 8px;
                background: #2d2d2d;
                cursor: pointer;
                transition: all 0.2s;
                text-align: left;
                width: 100%;
            }}
            
            .scope-btn:hover {{
                border-color: #f59e0b;
            }}
            
            .scope-btn.active {{
                border-color: #3b82f6;
                background: #1e3a5f;
            }}
            
            .scope-label {{
                font-weight: 600;
                font-size: 13px;
                color: #d4d4d4;
            }}
            
            .scope-desc {{
                font-size: 11px;
                color: #9ca3af;
                margin-top: 2px;
            }}
            
            .permission-actions {{
                display: grid;
                grid-template-columns: 1fr 1fr 1fr;
                gap: 10px;
                padding: 16px;
                background: rgba(0,0,0,0.02);
                border-top: 1px solid rgba(0,0,0,0.05);
            }}
            
            .permission-btn {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                padding: 11px 18px;
                border-radius: 9px;
                font-size: 13px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
                border: none;
                position: relative;
                overflow: hidden;
            }}
            
            .permission-btn::before {{
                content: '';
                position: absolute;
                top: 0;
                left: -100%;
                width: 100%;
                height: 100%;
                background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent);
                transition: left 0.5s;
            }}
            
            .permission-btn:hover::before {{
                left: 100%;
            }}
            
            .permission-btn-deny {{
                background: rgba(239, 68, 68, 0.1);
                color: #ef4444;
                border: 1px solid rgba(239, 68, 68, 0.3);
            }}
            
            .permission-btn-deny:hover {{
                background: rgba(239, 68, 68, 0.18);
                border-color: rgba(239, 68, 68, 0.5);
                box-shadow: 0 0 16px rgba(239, 68, 68, 0.25);
                transform: translateY(-2px);
            }}
            
            .permission-btn-allow {{
                background: linear-gradient(135deg, #3b82f6, #2563eb);
                color: #fff;
                border: 1px solid rgba(59, 130, 246, 0.5);
                box-shadow: 0 2px 8px rgba(59, 130, 246, 0.3);
            }}
            
            .permission-btn-allow:hover {{
                background: linear-gradient(135deg, #2563eb, #1d4ed8);
                border-color: rgba(59, 130, 246, 0.7);
                box-shadow: 0 4px 16px rgba(59, 130, 246, 0.45);
                transform: translateY(-2px);
            }}
            
            .permission-btn-always {{
                background: linear-gradient(135deg, #a855f7, #9333ea);
                color: #fff;
                border: 1px solid rgba(168, 85, 247, 0.5);
                box-shadow: 0 2px 8px rgba(168, 85, 247, 0.3);
            }}
            
            .permission-btn-always:hover {{
                background: linear-gradient(135deg, #9333ea, #7e22ce);
                border-color: rgba(168, 85, 247, 0.7);
                box-shadow: 0 4px 16px rgba(168, 85, 247, 0.45);
                transform: translateY(-2px);
            }}
            
            .permission-actions-secondary {{
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 0 16px 16px 16px;
                background: rgba(0,0,0,0.02);
            }}
            
            .remember-toggle {{
                display: flex;
                align-items: center;
                gap: 10px;
                cursor: pointer;
                user-select: none;
                padding: 8px 12px;
                border-radius: 8px;
                transition: background 0.2s;
            }}
            
            .remember-toggle:hover {{
                background: rgba(255, 255, 255, 0.05);
            }}
            
            .remember-toggle input {{
                display: none;
            }}
            
            .toggle-slider {{
                width: 46px;
                height: 24px;
                background: rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                position: relative;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                border: 1px solid rgba(255, 255, 255, 0.1);
                flex-shrink: 0;
            }}
            
            .toggle-slider::after {{
                content: '';
                position: absolute;
                top: 2px;
                left: 2px;
                width: 18px;
                height: 18px;
                background: linear-gradient(135deg, #6b7280, #4b5563);
                border-radius: 50%;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
            }}
            
            .remember-toggle input:checked + .toggle-slider {{
                background: linear-gradient(135deg, rgba(34, 197, 94, 0.3), rgba(34, 197, 94, 0.2));
                border-color: rgba(34, 197, 94, 0.5);
            }}
            
            .remember-toggle input:checked + .toggle-slider::after {{
                transform: translateX(22px);
                background: linear-gradient(135deg, #22c55e, #16a34a);
                box-shadow: 0 0 12px rgba(34, 197, 94, 0.5);
            }}
            
            .remember-text {{
                display: flex;
                align-items: center;
                gap: 7px;
                font-size: 12px;
                color: #9ca3af;
                font-weight: 500;
            }}
        </style>
        """
        
        return html
    
    def render_json(self, card_data: PermissionCardData) -> Dict[str, Any]:
        """
        Render permission card data as JSON for API response.
        
        Returns:
            Dictionary with card data
        """
        return {
            "type": "permission_request",
            "card": card_data.to_dict(),
            "actions": {
                "grant": f"/api/permissions/{card_data.request_id}/grant",
                "deny": f"/api/permissions/{card_data.request_id}/deny",
                "limited": f"/api/permissions/{card_data.request_id}/grant?access=limited",
            }
        }
    
    def register_callback(self, request_id: str, callback: Callable):
        """Register a callback for permission resolution."""
        self._callbacks[request_id] = callback
    
    def handle_grant(self, request_id: str, scope: str = "session"):
        """Handle permission grant from UI."""
        if request_id in self._callbacks:
            self._callbacks[request_id]("granted", scope)
    
    def handle_deny(self, request_id: str, reason: str = ""):
        """Handle permission denial from UI."""
        if request_id in self._callbacks:
            self._callbacks[request_id]("denied", reason)


# Singleton
_card_renderer = None


def get_permission_card_renderer() -> PermissionCardRenderer:
    """Get singleton instance."""
    global _card_renderer
    if _card_renderer is None:
        _card_renderer = PermissionCardRenderer()
    return _card_renderer
