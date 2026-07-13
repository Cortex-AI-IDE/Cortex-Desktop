"""
Auto-converted from oauth.ts
TODO: Review and refine type annotations
"""

from typing import List, Optional, TypedDict


class OauthConfig(TypedDict):
    """OAuth configuration."""
    client_id: str
    client_secret: str
    redirect_uri: Optional[str]
    scopes: List[str]


def fileSuffixForOauthConfig() -> str:
    """Get file suffix for OAuth config."""
    return ".oauth"


def getOauthConfig() -> OauthConfig:
    """Get OAuth configuration."""
    return OauthConfig(
        client_id="",
        client_secret="",
        redirect_uri=None,
        scopes=[]
    )



__all__ = ['fileSuffixForOauthConfig', 'getOauthConfig']