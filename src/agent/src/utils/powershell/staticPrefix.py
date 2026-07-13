"""
PowerShell static command prefix extraction.

Mirrors bash's getCommandPrefixStatic / getCompoundCommandPrefixesStatic
(src/utils/bash/prefix.py) but uses the PowerShell AST parser instead of
tree-sitter. The AST gives us cmd.name and cmd.args already split; for
external commands we feed those into the same fig-spec walker bash uses
(src/utils/shell/specPrefix.py) — git/npm/kubectl CLIs are shell-agnostic.

Feeds the "Yes, and don't ask again for: ___" editable input in the
permission dialog — static extractor provides a best-guess prefix, user
edits it down if needed.
"""

from typing import List, Optional

# Defensive imports - dependencies not yet converted
try:
    from ..bash.registry import getCommandSpec
except ImportError:
    async def getCommandSpec(name: str):
        return None

try:
    from ..shell.specPrefix import buildPrefix, DEPTH_RULES
except ImportError:
    async def buildPrefix(name: str, args: List[str], spec=None) -> str:
        return name
    DEPTH_RULES = {}

try:
    from ..stringUtils import countCharInString
except ImportError:
    def countCharInString(s: str, char: str) -> int:
        return s.count(char)

try:
    from .dangerousCmdlets import NEVER_SUGGEST
except ImportError:
    NEVER_SUGGEST = frozenset()

try:
    from .parser import getAllCommands, ParsedCommandElement, parsePowerShellCommand
except ImportError:
    async def parsePowerShellCommand(command: str):
        return {'valid': False}
    
    def getAllCommands(parsed):
        return []
    
    class ParsedCommandElement(dict):
        pass


async def extractPrefixFromElement(cmd: ParsedCommandElement) -> Optional[str]:
    """Extract a static prefix from a single parsed command element.
    
    Returns None for commands we won't suggest (shells, eval cmdlets, path-like
    invocations) or can't extract a meaningful prefix from.
    """
    # nameType === 'application' means the raw name had path chars (./x, x\y,
    # x.exe) — PowerShell will run a file, not a named cmdlet. Don't suggest.
    # Same reasoning as the permission engine's nameType gate (PR #20096).
    if cmd.get('nameType') == 'application':
        return None

    name = cmd.get('name')
    if not name:
        return None

    if name.lower() in NEVER_SUGGEST:
        return None

    # Cmdlets (Verb-Noun): the name alone is the right prefix granularity.
    # Get-Process -Name pwsh → Get-Process. There's no subcommand concept.
    if cmd.get('nameType') == 'cmdlet':
        return name

    # External command. Guard the argv before feeding it to buildPrefix.
    #
    # elementTypes[0] (command name) must be a literal. `& $cmd status` has
    # elementTypes[0]='Variable', name='$cmd' — classifies as 'unknown' (no path
    # chars), passes NEVER_SUGGEST, getCommandSpec('$cmd')=None → returns bare
    # '$cmd' → dead rule. Cheap to gate here.
    #
    # elementTypes[1..] (args) must all be StringConstant or Parameter. Anything
    # dynamic (Variable/SubExpression/ScriptBlock/ExpandableString) would embed
    # `$foo`/`$(...)` in the prefix → dead rule.
    element_types = cmd.get('elementTypes', [])
    if len(element_types) == 0 or element_types[0] != 'StringConstant':
        return None
    
    args = cmd.get('args', [])
    for i in range(len(args)):
        t = element_types[i + 1] if i + 1 < len(element_types) else None
        if t != 'StringConstant' and t != 'Parameter':
            return None

    # Consult the fig spec — same oracle bash uses. If git's spec says -C takes
    # a value, buildPrefix skips -C /repo and finds `status` as a subcommand.
    # Lowercase for lookup: fig specs are filesystem paths (git.js), case-
    # sensitive on Linux. PowerShell is case-insensitive (Git === git) so `Git`
    # must resolve to the git spec. macOS hides this bug (case-insensitive fs).
    # Call buildPrefix unconditionally — calculateDepth consults DEPTH_RULES
    # before its own `if (!spec) return 2` fallback, so gcloud/aws/kubectl/az
    # get depth-aware prefixes even without a loaded spec. The old
    # `if (!spec) return name` short-circuit produced bare `gcloud:*` which
    # auto-allows every gcloud subcommand.
    name_lower = name.lower()
    spec = await getCommandSpec(name_lower)
    prefix = await buildPrefix(name, args, spec)

    # Post-buildPrefix word integrity: buildPrefix space-joins consumed args
    # into the prefix string. parser.ts:685 stores .value (quote-stripped) for
    # single-quoted literals: git 'push origin' → args=['push origin']. If
    # that arg is consumed, buildPrefix emits 'git push origin' — silently
    # promoting 1 argv element to 3 prefix words. Rule PowerShell(git push
    # origin:*) then matches `git push origin --force` (3-element argv) — not
    # what the user approved.
    #
    # The old set-membership check (`!cmd.args.includes(word)`) was defeated
    # by decoy args: `git 'push origin' push origin` → args=['push origin',
    # 'push', 'origin'], prefix='git push origin'. Each word ∈ args (decoys at
    # indices 1,2 satisfy .includes()) → passed. Now POSITIONAL: walk args in
    # order; each prefix word must exactly match the next non-flag arg. A
    # positional that doesn't match means buildPrefix split it. Flags and
    # their values are skipped (buildPrefix skips them too) so
    # `git -C '/my repo' status` and `git commit -m 'fix typo'` still pass.
    # Backslash (C:\repo) rejected: dead over-specific rule.
    arg_idx = 0
    for word in prefix.split(' ')[1:]:
        if '\\' in word:
            return None
        while arg_idx < len(args):
            a = args[arg_idx]
            if a == word:
                break
            if a.startswith('-'):
                arg_idx += 1
                # Only skip the flag's value if the spec says this flag takes a
                # value argument. Without spec info, treat as a switch (no value)
                # — fail-safe avoids over-skipping positional args. (bug #16)
                if (
                    spec and spec.get('options') and
                    arg_idx < len(args) and
                    args[arg_idx] != word and
                    not args[arg_idx].startswith('-')
                ):
                    flag_lower = a.lower()
                    opt = None
                    for o in spec['options']:
                        names = o.get('name', [])
                        if isinstance(names, list):
                            if flag_lower in names:
                                opt = o
                                break
                        elif names == flag_lower:
                            opt = o
                            break
                    
                    if opt and opt.get('args'):
                        arg_idx += 1
                continue
            # Positional arg that isn't the expected word → arg was split.
            return None
        if arg_idx >= len(args):
            return None
        arg_idx += 1

    # Bare-root guard: buildPrefix returns 'git' for `git` with no subcommand
    # found (empty args, or only global flags). That's too broad — would
    # auto-allow `git push --force` forever. Bash's extractor doesn't gate this
    # (bash/prefix.py:363, separate fix). Reject single-word results for
    # commands whose spec declares subcommands OR that have DEPTH_RULES entries
    # (gcloud, aws, kubectl, etc.) which implies subcommand structure even
    # without a loaded spec. (bug #17)
    if ' ' not in prefix and (
        (spec and spec.get('subcommands') and len(spec['subcommands']) > 0) or
        name_lower in DEPTH_RULES
    ):
        return None
    
    return prefix


async def getCommandPrefixStatic(command: str) -> Optional[dict]:
    """Extract a prefix suggestion for a PowerShell command.
    
    Parses the command, takes the first CommandAst, returns a prefix suitable
    for the permission dialog's "don't ask again for: ___" editable input.
    Returns None when no safe prefix can be extracted (parse failure, shell
    invocation, path-like name, bare subcommand-aware command).
    """
    parsed = await parsePowerShellCommand(command)
    if not parsed.get('valid'):
        return None

    # Find the first actual command (CommandAst). getAllCommands iterates
    # both statement.commands and statement.nestedCommands (for &&/||/if/for).
    # Skip synthetic CommandExpressionAst entries (expression pipeline sources,
    # non-PipelineAst statement placeholders).
    commands = getAllCommands(parsed)
    first_command = None
    for cmd in commands:
        if cmd.get('elementType') == 'CommandAst':
            first_command = cmd
            break
    
    if not first_command:
        return {'commandPrefix': None}

    return {'commandPrefix': await extractPrefixFromElement(first_command)}


async def getCompoundCommandPrefixesStatic(
    command: str,
    excludeSubcommand=None
) -> List[str]:
    """Extract prefixes for all subcommands in a compound PowerShell command.
    
    For `Get-Process; git status && npm test`, returns per-subcommand prefixes.
    Subcommands for which `excludeSubcommand` returns true (e.g. already
    read-only/auto-allowed) are skipped — no point suggesting a rule for them.
    Prefixes sharing a root are collapsed via word-aligned LCP:
    `npm run test && npm run lint` → `npm run`.
    
    The filter receives the ParsedCommandElement (not cmd.text) because
    PowerShell's read-only check (isAllowlistedCommand) needs the element's
    structured fields (nameType, args). Passing text would require reparsing,
    which spawns pwsh.exe per subcommand — expensive and wasteful since we
    already have the parsed elements here. Bash's equivalent passes text
    because BashTool.isReadOnly works from regex/patterns, not parsed AST.
    """
    parsed = await parsePowerShellCommand(command)
    if not parsed.get('valid'):
        return []

    commands = [
        cmd for cmd in getAllCommands(parsed)
        if cmd.get('elementType') == 'CommandAst'
    ]

    # Single command — no compound collapse needed.
    if len(commands) <= 1:
        prefix = await extractPrefixFromElement(commands[0]) if commands else None
        return [prefix] if prefix else []

    prefixes = []
    for cmd in commands:
        if excludeSubcommand and excludeSubcommand(cmd):
            continue
        prefix = await extractPrefixFromElement(cmd)
        if prefix:
            prefixes.append(prefix)

    if len(prefixes) == 0:
        return []

    # Group by root command (first word) and collapse each group via
    # word-aligned longest common prefix. `npm run test` + `npm run lint`
    # → `npm run`. But NEVER collapse down to a bare subcommand-aware root:
    # `git add` + `git commit` would LCP to `git`, which extractPrefixFromElement
    # explicitly refuses as too broad (line ~119). Collapsing through that gate
    # would suggest PowerShell(git:*) → auto-allows git push --force forever.
    # When LCP yields a bare subcommand-aware root, drop the group entirely
    # rather than suggest either the too-broad root or N un-collapsed rules.
    #
    # Bash's getCompoundCommandPrefixesStatic has this same collapse without
    # the guard (src/utils/bash/prefix.py:360-365) — that's a separate fix.
    #
    # Grouping and word-comparison are case-insensitive (PowerShell is
    # case-insensitive: Git === git, Get-Process === get-process). The Map key
    # is lowercased; the emitted prefix keeps the first-seen casing.
    groups = {}
    for prefix in prefixes:
        root = prefix.split(' ')[0]
        key = root.lower()
        if key in groups:
            groups[key].append(prefix)
        else:
            groups[key] = [prefix]

    collapsed = []
    for root_lower, group in groups.items():
        lcp = wordAlignedLCP(group)
        lcp_word_count = 0 if lcp == '' else countCharInString(lcp, ' ') + 1
        if lcp_word_count <= 1:
            # LCP collapsed to a single word. If that root's fig spec declares
            # subcommands, this is the same too-broad case extractPrefixFromElement
            # rejects (bare `git` → allows `git push --force`). Drop the group.
            # getCommandSpec is LRU-memoized; one lookup per distinct root.
            root_spec = await getCommandSpec(root_lower)
            if (
                (root_spec and root_spec.get('subcommands') and len(root_spec['subcommands']) > 0) or
                root_lower in DEPTH_RULES
            ):
                continue
        collapsed.append(lcp)
    
    return collapsed


def wordAlignedLCP(strings: List[str]) -> str:
    """Word-aligned longest common prefix. Doesn't chop mid-word.
    
    Case-insensitive comparison (PowerShell: Git === git), emits first
    string's casing.
    ["npm run test", "npm run lint"] → "npm run"
    ["Git status", "git log"] → "Git" (first-seen casing)
    ["Get-Process"] → "Get-Process"
    """
    if len(strings) == 0:
        return ''
    if len(strings) == 1:
        return strings[0]

    first_words = strings[0].split(' ')
    common_word_count = len(first_words)

    for i in range(1, len(strings)):
        words = strings[i].split(' ')
        match_count = 0
        while (
            match_count < common_word_count and
            match_count < len(words) and
            words[match_count].lower() == first_words[match_count].lower()
        ):
            match_count += 1
        common_word_count = match_count
        if common_word_count == 0:
            break

    return ' '.join(first_words[:common_word_count])
