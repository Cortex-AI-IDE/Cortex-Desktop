"""
Git can be weaponized for sandbox escape via two vectors:
1. Bare-repo attack: if cwd contains HEAD + objects/ + refs/ but no valid
   .git/HEAD, Git treats cwd as a bare repository and runs hooks from cwd.
2. Git-internal write + git: a compound command creates HEAD/objects/refs/
   hooks/ then runs git — the git subcommand executes the freshly-created
   malicious hooks.
"""

import os
import re
from pathlib import Path
from typing import Optional

# Defensive imports
try:
    from ...utils.cwd import getCwd
except ImportError:
    def getCwd():
        return os.getcwd()

try:
    from ...utils.powershell.parser import PS_TOKENIZER_DASH_CHARS
except ImportError:
    PS_TOKENIZER_DASH_CHARS = set('–—―')  # Unicode dash characters


def resolveCwdReentry(normalized: str) -> str:
    """
    If a normalized path starts with `../<cwd-basename>/`, it re-enters cwd
    via the parent — resolve it to the cwd-relative form. posix.normalize
    preserves leading `..` (no cwd context), so `../project/hooks` with
    cwd=/x/project stays `../project/hooks` and misses the `hooks/` prefix
    match even though it resolves to the same directory at runtime.
    Check/use divergence: validator sees `../project/hooks`, PowerShell
    resolves against cwd to `hooks`.
    """
    if not normalized.startswith('../'):
        return normalized
    
    cwd_base = Path(getCwd()).name.lower()
    if not cwd_base:
        return normalized
    
    # Iteratively strip `../<cwd-basename>/` pairs (handles `../../p/p/hooks`
    # when cwd has repeated basename segments is unlikely, but one-level is
    # the common attack).
    prefix = '../' + cwd_base + '/'
    s = normalized
    while s.startswith(prefix):
        s = s[len(prefix):]
    
    # Also handle exact `../<cwd-basename>` (no trailing slash)
    if s == '../' + cwd_base:
        return '.'
    
    return s


def normalizeGitPathArg(arg: str) -> str:
    """
    Normalize PS arg text → canonical path for git-internal matching.
    Order matters: structural strips first (colon-bound param, quotes,
    backtick escapes, provider prefix, drive-relative prefix), then NTFS
    per-component trailing-strip (spaces always; dots only if not `./..`
    after space-strip), then posix.normalize (resolves `..`, `.`, `//`),
    then case-fold.
    """
    s = arg
    
    # Normalize parameter prefixes: dash chars (–, —, ―) and forward-slash
    # (PS 5.1). /Path:hooks/pre-commit → extract colon-bound value. (bug #28)
    if len(s) > 0 and (s[0] in PS_TOKENIZER_DASH_CHARS or s[0] == '/'):
        c = s.find(':', 1)
        if c > 0:
            s = s[c + 1:]
    
    s = re.sub(r"^['\"]|['\"]$", '', s)  # Strip surrounding quotes
    s = s.replace('`', '')  # Strip backtick escapes
    
    # PS provider-qualified path: FileSystem::hooks/pre-commit → hooks/pre-commit
    # Also handles fully-qualified form: Microsoft.PowerShell.Core\FileSystem::path
    s = re.sub(r'^(?:[A-Za-z0-9_.]+\\){0,3}FileSystem::', '', s, flags=re.IGNORECASE)
    
    # Drive-relative C:foo (no separator after colon) is cwd-relative on that
    # drive. C:\foo (WITH separator) is absolute and must NOT match — the
    # negative lookahead preserves it.
    s = re.sub(r'^[A-Za-z]:(?![/\\])', '', s)
    
    s = s.replace('\\', '/')  # Convert backslashes to forward slashes
    
    # Win32 CreateFileW per-component: iteratively strip trailing spaces,
    # then trailing dots, stopping if the result is `.` or `..` (special).
    # `.. ` → `..`, `.. .` → `..`, `...` → '' → `.`, `hooks .` → `hooks`.
    # Originally-'' (leading slash split) stays '' (absolute-path marker).
    components = s.split('/')
    normalized_components = []
    for c in components:
        if c == '':
            normalized_components.append(c)
            continue
        
        prev = None
        while c != prev:
            prev = c
            c = re.sub(r' +$', '', c)  # Strip trailing spaces
            if c == '.' or c == '..':
                break
            c = re.sub(r'\.+$', '', c)  # Strip trailing dots
        
        normalized_components.append(c if c else '.')
    
    s = '/'.join(normalized_components)
    
    # Use os.path.normpath for posix-style normalization
    s = os.path.normpath(s).replace('\\', '/')
    
    if s.startswith('./'):
        s = s[2:]
    
    return s.lower()


GIT_INTERNAL_PREFIXES = ['head', 'objects', 'refs', 'hooks']


def resolveEscapingPathToCwdRelative(n: str) -> Optional[str]:
    """
    SECURITY: Resolve a normalized path that escapes cwd (leading `../` or
    absolute) against the actual cwd, then check if it lands back INSIDE cwd.
    If so, strip cwd and return the cwd-relative remainder for prefix matching.
    If it lands outside cwd, return None (genuinely external — path-validation's
    concern). Covers `..\\<cwd-basename>\\HEAD` and `C:\\<full-cwd>\\HEAD` which
    posix.normalize alone cannot resolve (it leaves leading `..` as-is).

    This is the SOLE guard for the bare-repo HEAD attack. path-validation's
    DANGEROUS_FILES deliberately excludes bare `HEAD` (false-positive risk
    on legitimate non-git files named HEAD) and DANGEROUS_DIRECTORIES
    matches per-segment `.git` only — so `<cwd>/HEAD` passes that layer.
    The cwd-resolution here is load-bearing; do not remove without adding
    an alternative guard.
    """
    cwd = getCwd()
    
    # Reconstruct a platform-resolvable path from the posix-normalized form.
    # `n` has forward slashes (normalizeGitPathArg converted \\ → /); resolve()
    # handles forward slashes on Windows.
    abs_path = os.path.normpath(os.path.join(cwd, n))
    
    cwd_with_sep = cwd if cwd.endswith(os.sep) else cwd + os.sep
    
    # Case-insensitive comparison: normalizeGitPathArg lowercased `n`, so
    # resolve() output has lowercase components from `n` but cwd may be
    # mixed-case (e.g. C:\Users\...). Windows paths are case-insensitive.
    abs_lower = abs_path.lower()
    cwd_lower = cwd.lower()
    cwd_with_sep_lower = cwd_with_sep.lower()
    
    if abs_lower == cwd_lower:
        return '.'
    
    if not abs_lower.startswith(cwd_with_sep_lower):
        return None
    
    # Extract relative portion
    rel_part = abs_path[len(cwd_with_sep):].replace('\\', '/').lower()
    return rel_part


def matchesGitInternalPrefix(n: str) -> bool:
    """Check if path matches git-internal prefixes."""
    if n == 'head' or n == '.git':
        return True
    
    if n.startswith('.git/') or re.match(r'^git~\d+($|/)', n):
        return True
    
    for p in GIT_INTERNAL_PREFIXES:
        if p == 'head':
            continue
        if n == p or n.startswith(p + '/'):
            return True
    
    return False


def isGitInternalPathPS(arg: str) -> bool:
    """
    True if arg (raw PS arg text) resolves to a git-internal path in cwd.
    Covers both bare-repo paths (hooks/, refs/) and standard-repo paths
    (.git/hooks/, .git/config).
    """
    n = resolveCwdReentry(normalizeGitPathArg(arg))
    
    if matchesGitInternalPrefix(n):
        return True
    
    # SECURITY: leading `../` or absolute paths that resolveCwdReentry and
    # posix.normalize couldn't fully resolve. Resolve against actual cwd — if
    # the result lands back in cwd at a git-internal location, the guard must
    # still fire.
    if n.startswith('../') or n.startswith('/') or re.match(r'^[a-z]:', n):
        rel = resolveEscapingPathToCwdRelative(n)
        if rel is not None and matchesGitInternalPrefix(rel):
            return True
    
    return False


def matchesDotGitPrefix(n: str) -> bool:
    """Check if path matches .git directory prefix."""
    if n == '.git' or n.startswith('.git/'):
        return True
    
    # NTFS 8.3 short names: .git becomes GIT~1 (or GIT~2, etc. if multiple
    # dotfiles start with "git"). normalizeGitPathArg lowercases, so check
    # for git~N as the first component.
    return bool(re.match(r'^git~\d+($|/)', n))


def isDotGitPathPS(arg: str) -> bool:
    """
    True if arg resolves to a path inside .git/ (standard-repo metadata dir).
    Unlike isGitInternalPathPS, does NOT match bare-repo-style root-level
    `hooks/`, `refs/` etc. — those are common project directory names.
    """
    n = resolveCwdReentry(normalizeGitPathArg(arg))
    
    if matchesDotGitPrefix(n):
        return True
    
    # SECURITY: same cwd-resolution as isGitInternalPathPS — catch
    # `..\<cwd-basename>\.git\hooks\pre-commit` that lands back in cwd.
    if n.startswith('../') or n.startswith('/') or re.match(r'^[a-z]:', n):
        rel = resolveEscapingPathToCwdRelative(n)
        if rel is not None and matchesDotGitPrefix(rel):
            return True
    
    return False
