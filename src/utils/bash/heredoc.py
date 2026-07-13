# utils/bash/heredoc.py
# Stub file - heredoc extraction utilities

from typing import List, Optional


def extract_heredocs(command: str) -> List[Dict[str, Any]]:
    """
    Extract heredoc content from a command string.
    
    Returns a list of heredoc blocks with their delimiters and content.
    """
    import re
    
    heredocs: List[Dict[str, Any]] = []
    
    # Match heredoc patterns like <<EOF ... EOF
    pattern = re.compile(r'<<[~-]?\s*([\'"]?)(\w+)\1([\s\S]*?)\2')
    
    for match in pattern.finditer(command):
        heredocs.append({
            'delimiter': match.group(2),
            'content': match.group(3),
            'start': match.start(),
            'end': match.end(),
        })
    
    return heredocs


__all__ = ["extract_heredocs"]
