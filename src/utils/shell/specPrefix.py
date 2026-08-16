"""
Fig-spec-driven command prefix extraction.

Given a command name + args array + its @withfig/autocomplete spec, walks
the spec to find how deep into the args a meaningful prefix extends.
`git -C /repo status --short` → `git status` (spec says -C takes a value,
skip it, find `status` as a known subcommand).

Pure over (string, string[], CommandSpec) — no parser dependency. Extracted
from src/utils/bash/prefix.py so PowerShell's extractor can reuse it;
external CLIs (git, npm, kubectl) are shell-agnostic.
"""

from typing import List, Optional

# Defensive import for CommandSpec
try:
    from ..bash.registry import CommandSpec
except ImportError:
    class CommandSpec(dict):
        pass


URL_PROTOCOLS = ['http://', 'https://', 'ftp://']

# Overrides for commands whose fig specs aren't available at runtime
# (dynamic imports don't work in native/node builds). Without these,
# calculateDepth falls back to 2, producing overly broad prefixes.
DEPTH_RULES = {
    'rg': 2,  # pattern argument is required despite variadic paths
    'pre-commit': 2,
    # AI agent tools with deep subcommand trees (e.g. gcloud scheduler jobs list)
    'gcloud': 4,
    'gcloud compute': 6,
    'gcloud beta': 6,
    'aws': 4,
    'az': 4,
    'kubectl': 3,
    'docker': 3,
    'dotnet': 3,
    'git push': 2,
}


def _to_array(val):
    """Convert value to list if not already a list."""
    return val if isinstance(val, list) else [val]


def _is_known_subcommand(arg: str, spec: Optional[CommandSpec]) -> bool:
    """Check if an argument matches a known subcommand (case-insensitive).
    
    PS callers pass original-cased args; fig spec names are lowercase.
    """
    if not spec or not spec.get('subcommands'):
        return False
    
    arg_lower = arg.lower()
    for sub in spec['subcommands']:
        name = sub.get('name', '')
        if isinstance(name, list):
            if any(n.lower() == arg_lower for n in name):
                return True
        elif name.lower() == arg_lower:
            return True
    
    return False


def _flag_takes_arg(flag: str, next_arg: Optional[str], spec: Optional[CommandSpec]) -> bool:
    """Check if a flag takes an argument based on spec, or use heuristic."""
    # Check if flag is in spec.options
    if spec and spec.get('options'):
        for opt in spec['options']:
            opt_name = opt.get('name', '')
            if isinstance(opt_name, list):
                if flag in opt_name:
                    return bool(opt.get('args'))
            elif opt_name == flag:
                return bool(opt.get('args'))
    
    # Heuristic: if next arg isn't a flag and isn't a known subcommand, assume it's a flag value
    if spec and spec.get('subcommands') and next_arg and not next_arg.startswith('-'):
        return not _is_known_subcommand(next_arg, spec)
    
    return False


def _find_first_subcommand(args: List[str], spec: Optional[CommandSpec]) -> Optional[str]:
    """Find the first subcommand by skipping flags and their values."""
    for i in range(len(args)):
        arg = args[i]
        if not arg:
            continue
        if arg.startswith('-'):
            if _flag_takes_arg(arg, args[i + 1] if i + 1 < len(args) else None, spec):
                i += 1
            continue
        if not spec or not spec.get('subcommands'):
            return arg
        if _is_known_subcommand(arg, spec):
            return arg
    
    return None


async def buildPrefix(command: str, args: List[str], spec: Optional[CommandSpec]) -> str:
    """Build a command prefix from command name, args, and spec.
    
    Args:
        command: The command name (e.g., 'git')
        args: List of arguments
        spec: Command specification from fig autocomplete
    
    Returns:
        The constructed prefix string
    """
    max_depth = await _calculateDepth(command, args, spec)
    parts = [command]
    has_subcommands = bool(spec and spec.get('subcommands'))
    found_subcommand = False

    for i in range(len(args)):
        arg = args[i]
        if not arg or len(parts) >= max_depth:
            break

        if arg.startswith('-'):
            # Special case: python -c should stop after -c
            if arg == '-c' and command.lower() in ['python', 'python3']:
                break

            # Check for isCommand/isModule flags that should be included in prefix
            if spec and spec.get('options'):
                option = None
                for opt in spec['options']:
                    opt_name = opt.get('name', '')
                    if isinstance(opt_name, list):
                        if arg in opt_name:
                            option = opt
                            break
                    elif opt_name == arg:
                        option = opt
                        break
                
                if option and option.get('args'):
                    opt_args = _to_array(option['args'])
                    if any(a and (a.get('isCommand') or a.get('isModule')) for a in opt_args):
                        parts.append(arg)
                        continue

            # For commands with subcommands, skip global flags to find the subcommand
            if has_subcommands and not found_subcommand:
                if _flag_takes_arg(arg, args[i + 1] if i + 1 < len(args) else None, spec):
                    i += 1
                continue
            
            break  # Stop at flags (original behavior)

        if await _shouldStopAtArg(arg, args[:i], spec):
            break
        
        if has_subcommands and not found_subcommand:
            found_subcommand = _is_known_subcommand(arg, spec)
        
        parts.append(arg)

    return ' '.join(parts)


async def _calculateDepth(command: str, args: List[str], spec: Optional[CommandSpec]) -> int:
    """Calculate the depth of the command prefix."""
    # Find first subcommand by skipping flags and their values
    first_subcommand = _find_first_subcommand(args, spec)
    command_lower = command.lower()
    key = f"{command_lower} {first_subcommand.lower()}" if first_subcommand else command_lower
    
    if key in DEPTH_RULES:
        return DEPTH_RULES[key]
    if command_lower in DEPTH_RULES:
        return DEPTH_RULES[command_lower]
    if not spec:
        return 2

    if spec.get('options') and any(arg and arg.startswith('-') for arg in args):
        for arg in args:
            if not arg or not arg.startswith('-'):
                continue
            
            option = None
            for opt in spec['options']:
                opt_name = opt.get('name', '')
                if isinstance(opt_name, list):
                    if arg in opt_name:
                        option = opt
                        break
                elif opt_name == arg:
                    option = opt
                    break
            
            if option and option.get('args'):
                opt_args = _to_array(option['args'])
                if any(a and (a.get('isCommand') or a.get('isModule')) for a in opt_args):
                    return 3

    # Find subcommand spec using the already-found firstSubcommand
    if first_subcommand and spec.get('subcommands'):
        first_sub_lower = first_subcommand.lower()
        subcommand = None
        for sub in spec['subcommands']:
            sub_name = sub.get('name', '')
            if isinstance(sub_name, list):
                if any(n.lower() == first_sub_lower for n in sub_name):
                    subcommand = sub
                    break
            elif sub_name.lower() == first_sub_lower:
                subcommand = sub
                break
        
        if subcommand:
            if subcommand.get('args'):
                sub_args = _to_array(subcommand['args'])
                if any(a and a.get('isCommand') for a in sub_args):
                    return 3
                if any(a and a.get('isVariadic') for a in sub_args):
                    return 2
            
            if subcommand.get('subcommands'):
                return 4
            
            # Leaf subcommand with NO args declared (git show, git log, git tag):
            # the 3rd word is transient (SHA, ref, tag name) → dead over-specific
            # rule like PowerShell(git show 81210f8:*). NOT the isOptional case —
            # `git fetch` declares optional remote/branch and `git fetch origin`
            # is tested (bash/prefix.test.ts:912) as intentional remote scoping.
            if not subcommand.get('args'):
                return 2
            
            return 3

    if spec.get('args'):
        args_array = _to_array(spec['args'])

        if any(a and a.get('isCommand') for a in args_array):
            if not isinstance(spec['args'], list) and spec['args'].get('isCommand'):
                return 2
            else:
                idx = next((i for i, a in enumerate(args_array) if a and a.get('isCommand')), -1)
                return min(2 + idx, 3) if idx != -1 else 2

        if not spec.get('subcommands'):
            if any(a and a.get('isVariadic') for a in args_array):
                return 1
            if args_array and args_array[0] and not args_array[0].get('isOptional'):
                return 2

    if spec.get('args') and any(a and a.get('isDangerous') for a in _to_array(spec['args'])):
        return 3
    
    return 2


async def _shouldStopAtArg(arg: str, args: List[str], spec: Optional[CommandSpec]) -> bool:
    """Determine if we should stop building the prefix at this argument."""
    if arg.startswith('-'):
        return True

    dot_index = arg.rfind('.')
    has_extension = (
        dot_index > 0 and
        dot_index < len(arg) - 1 and
        ':' not in arg[dot_index + 1:]
    )

    has_file = '/' in arg or has_extension
    has_url = any(arg.startswith(proto) for proto in URL_PROTOCOLS)

    if not has_file and not has_url:
        return False

    # Check if we're after a -m flag for python modules
    if spec and spec.get('options') and len(args) > 0 and args[-1] == '-m':
        option = None
        for opt in spec['options']:
            opt_name = opt.get('name', '')
            if isinstance(opt_name, list):
                if '-m' in opt_name:
                    option = opt
                    break
            elif opt_name == '-m':
                option = opt
                break
        
        if option and option.get('args'):
            opt_args = _to_array(option['args'])
            if any(a and a.get('isModule') for a in opt_args):
                return False  # Don't stop at module names

    # For actual files/URLs, always stop regardless of context
    return True
