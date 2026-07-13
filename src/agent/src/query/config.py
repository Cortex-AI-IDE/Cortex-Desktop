"""
Auto-converted from config.ts
TODO: Review and refine type annotations
"""



class QueryConfig:
    """Query configuration."""
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 0.9
    

def buildQueryConfig() -> QueryConfig:
    """Build query configuration."""
    return QueryConfig()



__all__ = ['buildQueryConfig']