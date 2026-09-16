"""
Utility for substituting $ARGUMENTS placeholders in skill/command prompts.

Supports:
- $ARGUMENTS - replaced with the full arguments string
- $ARGUMENTS[0], $ARGUMENTS[1], etc. - replaced with individual indexed arguments
- $0, $1, etc. - shorthand for $ARGUMENTS[0], $ARGUMENTS[1]
- Named arguments (e.g., $foo, $bar) - when argument names are defined in frontmatter
"""

import re
from typing import List, Optional, Union


def parseArguments(args: str) -> List[str]:
    """
    Parse an arguments string into an array of individual arguments.
    Simple whitespace splitting with quote handling.
    
    Examples:
    - "foo bar baz" => ["foo", "bar", "baz"]
    - 'foo "hello world" baz' => ["foo", "hello world", "baz"]
    """
    if not args or not args.strip():
        return []
    
    # Simple split on whitespace for now
    # Full implementation would use shlex for proper shell parsing
    import shlex
    try:
        return shlex.split(args)
    except ValueError:
        # Fallback to simple split
        return args.split()


def parseArgumentNames(argumentNames: Union[str, List[str], None]) -> List[str]:
    """
    Parse argument names from the frontmatter 'arguments' field.
    Accepts either a space-separated string or an array of strings.
    
    Examples:
    - "foo bar baz" => ["foo", "bar", "baz"]
    - ["foo", "bar", "baz"] => ["foo", "bar", "baz"]
    """
    if not argumentNames:
        return []
    
    # Filter out empty strings and numeric-only names
    def is_valid_name(name: str) -> bool:
        return isinstance(name, str) and name.strip() != '' and not name.isdigit()
    
    if isinstance(argumentNames, list):
        return [n for n in argumentNames if is_valid_name(n)]
    if isinstance(argumentNames, str):
        return [n for n in argumentNames.split() if is_valid_name(n)]
    return []


def generateProgressiveArgumentHint(
    argNames: List[str],
    typedArgs: List[str],
) -> Optional[str]:
    """
    Generate argument hint showing remaining unfilled args.
    
    Args:
        argNames: Array of argument names from frontmatter
        typedArgs: Arguments the user has typed so far
        
    Returns:
        Hint string like "[arg2] [arg3]" or None if all filled
    """
    remaining = argNames[len(typedArgs):]
    if not remaining:
        return None
    return ' '.join(f'[{name}]' for name in remaining)


def substituteArguments(
    content: str,
    args: Optional[str],
    appendIfNoPlaceholder: bool = True,
    argumentNames: Optional[List[str]] = None,
) -> str:
    """
    Substitute argument placeholders in content.
    
    Supports:
    - $ARGUMENTS - full arguments string
    - $ARGUMENTS[0], $ARGUMENTS[1] - indexed arguments
    - $0, $1 - shorthand for indexed arguments
    - $name - named arguments (when argumentNames provided)
    
    Args:
        content: Template content with placeholders
        args: Arguments string from user
        appendIfNoPlaceholder: If True, append args if no placeholder found
        argumentNames: List of named arguments from frontmatter
        
    Returns:
        Content with placeholders substituted
    """
    if not args:
        # Remove any $ARGUMENTS placeholders if no args provided
        content = re.sub(r'\$ARGUMENTS(?:\[\d+\])?', '', content)
        content = re.sub(r'\$\d+', '', content)
        if argumentNames:
            for name in argumentNames:
                content = content.replace(f'${name}', '')
        return content
    
    # Parse arguments into array
    parsed_args = parseArguments(args)
    
    # Replace $ARGUMENTS with full string
    content = content.replace('$ARGUMENTS', args)
    
    # Replace $ARGUMENTS[N] with indexed argument
    def replace_indexed(match: re.Match) -> str:
        index = int(match.group(1))
        return parsed_args[index] if index < len(parsed_args) else ''
    
    content = re.sub(r'\$ARGUMENTS\[(\d+)\]', replace_indexed, content)
    
    # Replace $N shorthand
    def replace_shorthand(match: re.Match) -> str:
        index = int(match.group(1))
        return parsed_args[index] if index < len(parsed_args) else ''
    
    content = re.sub(r'\$(\d+)', replace_shorthand, content)
    
    # Replace named arguments
    if argumentNames:
        for i, name in enumerate(argumentNames):
            value = parsed_args[i] if i < len(parsed_args) else ''
            content = content.replace(f'${name}', value)
    
    # Append args if no placeholder was found and appendIfNoPlaceholder is True
    if appendIfNoPlaceholder and '$ARGUMENTS' not in content and '$0' not in content:
        content = content + '\n\n' + args
    
    return content
