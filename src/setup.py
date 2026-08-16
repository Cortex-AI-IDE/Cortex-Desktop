# ------------------------------------------------------------
# setup.py
# Python conversion of setup.ts (lines 1-478)
# 
# Application initialization and setup routine. Handles:
# - Node.js version checking
# - Session ID management
# - UDS messaging server startup
# - Terminal backup restoration (iTerm2, Terminal.app)
# - Worktree creation with tmux support
# - Background job initialization
# - Plugin prefetching
# - Permission mode validation
# - Previous session analytics logging
# ------------------------------------------------------------

import asyncio
import logging
import os
import sys
from typing import Optional


# ============================================================
# DEFENSIVE IMPORTS
# ============================================================

try:
    from bun.bundle import feature
except ImportError:
    def feature(feature_name: str) -> bool:
        """Stub: Check if a feature flag is enabled."""
        return False

try:
    import chalk
except ImportError:
    class chalk:
        """Stub for chalk library."""
        @staticmethod
        def red(text: str) -> str:
            return f"\033[91m{text}\033[0m"
        
        @staticmethod
        def yellow(text: str) -> str:
            return f"\033[93m{text}\033[0m"
        
        @staticmethod
        def green(text: str) -> str:
            return f"\033[92m{text}\033[0m"
        
        @staticmethod
        def bold(text: str) -> str:
            return f"\033[1m{text}\033[0m"

try:
    from .services.analytics.index import log_event
except ImportError:
    def log_event(event_name: str, metadata: dict) -> None:
        """Stub for analytics logging."""
        pass

try:
    from .utils.cwd import get_cwd
except ImportError:
    def get_cwd() -> str:
        return os.getcwd()

try:
    from .utils.release_notes import check_for_release_notes
except ImportError:
    async def check_for_release_notes(last_seen: Optional[str]) -> dict:
        return {"hasReleaseNotes": False}

try:
    from .utils.Shell import set_cwd
except ImportError:
    def set_cwd(cwd: str) -> None:
        os.chdir(cwd)

try:
    from .utils.sinks import init_sinks
except ImportError:
    def init_sinks() -> None:
        pass

try:
    from .bootstrap.state import (
        get_is_non_interactive_session,
        get_project_root,
        get_session_id,
        set_original_cwd,
        set_project_root,
        switch_session,
    )
except ImportError:
    def get_is_non_interactive_session() -> bool:
        return False
    
    def get_project_root() -> str:
        return os.getcwd()
    
    def get_session_id() -> str:
        return "default-session"
    
    def set_original_cwd(cwd: str) -> None:
        pass
    
    def set_project_root(root: str) -> None:
        pass
    
    def switch_session(session_id: str) -> None:
        pass

try:
    from .commands import get_commands
except ImportError:
    async def get_commands(project_root: str) -> list:
        return []

try:
    from .services.SessionMemory.session_memory import init_session_memory
except ImportError:
    def init_session_memory() -> None:
        pass

try:
    from .agent_types.ids import as_session_id
except ImportError:
    def as_session_id(session_id: str) -> str:
        return session_id

try:
    from .utils.agent_swarms_enabled import is_agent_swarms_enabled
except ImportError:
    def is_agent_swarms_enabled() -> bool:
        return False

try:
    from .utils.apple_terminal_backup import check_and_restore_terminal_backup
except ImportError:
    async def check_and_restore_terminal_backup() -> dict:
        return {"status": "not_applicable"}

try:
    from .utils.auth import prefetch_api_key_from_api_key_helper_if_safe
except ImportError:
    async def prefetch_api_key_from_api_key_helper_if_safe(is_non_interactive: bool) -> None:
        pass

try:
    from .utils.cortexmd import clear_memory_file_caches
except ImportError:
    def clear_memory_file_caches() -> None:
        pass

try:
    from .utils.config import get_current_project_config, get_global_config
except ImportError:
    def get_current_project_config() -> dict:
        return {}
    
    def get_global_config() -> dict:
        return {}

try:
    from .utils.diag_logs import log_for_diagnostics_no_pii
except ImportError:
    def log_for_diagnostics_no_pii(level: str, message: str, data: Optional[dict] = None) -> None:
        pass

try:
    from .utils.env import env
except ImportError:
    class env:
        @staticmethod
        async def has_internet_access() -> bool:
            return True

try:
    from .utils.env_dynamic import env_dynamic
except ImportError:
    class env_dynamic:
        @staticmethod
        async def get_is_docker() -> bool:
            return False
        
        @staticmethod
        def get_is_bubblewrap_sandbox() -> bool:
            return False

try:
    from .utils.env_utils import is_bare_mode, is_env_truthy
except ImportError:
    def is_bare_mode() -> bool:
        return False
    
    def is_env_truthy(value: Optional[str]) -> bool:
        return value and value.lower() in ["true", "1", "yes"]

try:
    from .utils.errors import error_message
except ImportError:
    def error_message(error: Exception) -> str:
        return str(error)

try:
    from .utils.git import find_canonical_git_root, find_git_root, get_is_git
except ImportError:
    async def get_is_git() -> bool:
        return False
    
    def find_canonical_git_root(cwd: str) -> Optional[str]:
        return None
    
    def find_git_root(cwd: str) -> Optional[str]:
        return None

try:
    from .utils.hooks.file_changed_watcher import initialize_file_changed_watcher
except ImportError:
    def initialize_file_changed_watcher(cwd: str) -> None:
        pass

try:
    from .utils.hooks.hooks_config_snapshot import (
        capture_hooks_config_snapshot,
        update_hooks_config_snapshot,
    )
except ImportError:
    def capture_hooks_config_snapshot() -> None:
        pass
    
    def update_hooks_config_snapshot() -> None:
        pass

try:
    from .utils.hooks import has_worktree_create_hook
except ImportError:
    def has_worktree_create_hook() -> bool:
        return False

try:
    from .utils.i_term_backup import check_and_restore_i_term2_backup
except ImportError:
    async def check_and_restore_i_term2_backup() -> dict:
        return {"status": "not_applicable"}

try:
    from .utils.log import log_error
except ImportError:
    def log_error(error: Exception) -> None:
        log.error(f"{error}")

try:
    from .utils.logo_v2_utils import get_recent_activity
except ImportError:
    async def get_recent_activity() -> None:
        pass

try:
    from .utils.native_installer.index import lock_current_version
except ImportError:
    async def lock_current_version() -> None:
        pass

try:
    from .utils.permissions.PermissionMode import PermissionMode
except ImportError:
    PermissionMode = str

try:
    from .utils.plans import get_plan_slug
except ImportError:
    def get_plan_slug() -> str:
        return "default"

try:
    from .utils.session_storage import save_worktree_state
except ImportError:
    def save_worktree_state(state: dict) -> None:
        pass

try:
    from .utils.startup_profiler import profile_checkpoint
except ImportError:
    def profile_checkpoint(name: str) -> None:
        pass

try:
    from .utils.worktree import (
        create_tmux_session_for_worktree,
        create_worktree_for_session,
        generate_tmux_session_name,
        worktree_branch_name,
    )
except ImportError:
    async def create_worktree_for_session(
        session_id: str,
        slug: str,
        tmux_session_name: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> dict:
        return {"worktreePath": os.getcwd()}
    
    async def create_tmux_session_for_worktree(
        session_name: str,
        worktree_path: str,
    ) -> dict:
        return {"created": False, "error": "Not implemented"}
    
    def generate_tmux_session_name(repo_root: str, branch_name: str) -> str:
        return f"{repo_root}-{branch_name}"
    
    def worktree_branch_name(slug: str) -> str:
        return slug


# ============================================================
# LOGGER
# ============================================================

log = logging.getLogger("cortex.agent")


# ============================================================
# MAIN SETUP FUNCTION
# ============================================================

async def setup(
    cwd: str,
    permission_mode: PermissionMode,
    allow_dangerously_skip_permissions: bool,
    worktree_enabled: bool,
    worktree_name: Optional[str],
    tmux_enabled: bool,
    custom_session_id: Optional[str] = None,
    worktree_pr_number: Optional[int] = None,
    messaging_socket_path: Optional[str] = None,
) -> None:
    """
    Initialize the application environment.
    
    Args:
        cwd: Current working directory
        permission_mode: Permission mode (default, acceptEdits, bypassPermissions, dontAsk)
        allow_dangerously_skip_permissions: Whether to skip permission checks
        worktree_enabled: Whether to create a git worktree
        worktree_name: Name for the worktree
        tmux_enabled: Whether to create a tmux session
        custom_session_id: Custom session ID override
        worktree_pr_number: PR number for worktree naming
        messaging_socket_path: Path for UDS messaging socket
    """
    log_for_diagnostics_no_pii('info', 'setup_started')
    
    # Check for Python version >= 3.8 (equivalent to Node.js 18 requirement)
    python_version = sys.version_info
    if python_version.major < 3 or (python_version.major == 3 and python_version.minor < 8):
        log.error('Cortex IDE requires Python version 3.8 or higher.')
        sys.exit(1)
    
    # Set custom session ID if provided
    if custom_session_id:
        switch_session(as_session_id(custom_session_id))
    
    # --bare / SIMPLE: skip UDS messaging server and teammate snapshot.
    # Scripted calls don't receive injected messages and don't use swarm teammates.
    # Explicit --messaging-socket-path is the escape hatch (per #23222 gate pattern).
    if not is_bare_mode() or messaging_socket_path is not None:
        # Start UDS messaging server (Mac/Linux only).
        # Enabled by default for ants — creates a socket in tmpdir if no
        # --messaging-socket-path is passed. Awaited so the server is bound
        # and $CORTEX_CODE_MESSAGING_SOCKET is exported before any hook
        # (SessionStart in particular) can spawn and snapshot process.env.
        if feature('UDS_INBOX'):
            try:
                from .utils.uds_messaging import start_uds_messaging, get_default_uds_socket_path
                await start_uds_messaging(
                    messaging_socket_path if messaging_socket_path else get_default_uds_socket_path(),
                    {'isExplicit': messaging_socket_path is not None},
                )
            except ImportError:
                pass
    
    # Teammate snapshot — SIMPLE-only gate (no escape hatch, swarm not used in bare)
    if not is_bare_mode() and is_agent_swarms_enabled():
        try:
            from .utils.swarm.backends.teammate_mode_snapshot import capture_teammate_mode_snapshot
            capture_teammate_mode_snapshot()
        except ImportError:
            pass
    
    # Terminal backup restoration — interactive only. Print mode doesn't
    # interact with terminal settings; the next interactive session will
    # detect and restore any interrupted setup.
    if not get_is_non_interactive_session():
        # iTerm2 backup check only when swarms enabled
        if is_agent_swarms_enabled():
            restored_iterm2_backup = await check_and_restore_i_term2_backup()
            if restored_iterm2_backup.get('status') == 'restored':
                log.warning('Detected an interrupted iTerm2 setup. Your original settings have been restored. You may need to restart iTerm2 for the changes to take effect.')
            elif restored_iterm2_backup.get('status') == 'failed':
                backup_path = restored_iterm2_backup.get('backupPath', 'unknown')
                log.error(f'Failed to restore iTerm2 settings. Please manually restore your original settings with: defaults import com.googlecode.iterm2 {backup_path}.')
        
        # Check and restore Terminal.app backup if setup was interrupted
        try:
            restored_terminal_backup = await check_and_restore_terminal_backup()
            if restored_terminal_backup.get('status') == 'restored':
                log.warning('Detected an interrupted Terminal.app setup. Your original settings have been restored. You may need to restart Terminal.app for the changes to take effect.')
            elif restored_terminal_backup.get('status') == 'failed':
                backup_path = restored_terminal_backup.get('backupPath', 'unknown')
                log.error(f'Failed to restore Terminal.app settings. Please manually restore your original settings with: defaults import com.apple.Terminal {backup_path}.')
        except Exception as error:
            # Log but don't crash if Terminal.app backup restoration fails
            log_error(error)
    
    # IMPORTANT: set_cwd() must be called before any other code that depends on the cwd
    set_cwd(cwd)
    
    # Capture hooks configuration snapshot to avoid hidden hook modifications.
    # IMPORTANT: Must be called AFTER set_cwd() so hooks are loaded from the correct directory
    # Use get_running_loop() instead of deprecated get_event_loop()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    hooks_start = loop.time() * 1000
    capture_hooks_config_snapshot()
    log_for_diagnostics_no_pii('info', 'setup_hooks_captured', {
        'duration_ms': (loop.time() * 1000) - hooks_start,
    })
    
    # Initialize FileChanged hook watcher — sync, reads hook config snapshot
    initialize_file_changed_watcher(cwd)
    
    # Handle worktree creation if requested
    # IMPORTANT: this must be called before get_commands(), otherwise /eject won't be available.
    if worktree_enabled:
        # Mirrors bridgeMain.ts: hook-configured sessions can proceed without git
        # so create_worktree_for_session() can delegate to the hook (non-git VCS).
        has_hook = has_worktree_create_hook()
        in_git = await get_is_git()
        
        if not has_hook and not in_git:
            sys.stderr.write(
                chalk.red(
                    f'Error: Can only use --worktree in a git repository, but {chalk.bold(cwd)} is not a git repository. '
                    f'Configure a WorktreeCreate hook in settings.json to use --worktree with other VCS systems.\n'
                )
            )
            sys.exit(1)
        
        slug = f'pr-{worktree_pr_number}' if worktree_pr_number else (worktree_name or get_plan_slug())
        
        # Git preamble runs whenever we're in a git repo — even if a hook is
        # configured — so --tmux keeps working for git users who also have a
        # WorktreeCreate hook. Only hook-only (non-git) mode skips it.
        tmux_session_name = None
        if in_git:
            # Resolve to main repo root (handles being invoked from within a worktree).
            # find_canonical_git_root is sync/filesystem-only/memoized; the underlying
            # find_git_root cache was already warmed by get_is_git() above, so this is ~free.
            main_repo_root = find_canonical_git_root(get_cwd())
            if not main_repo_root:
                sys.stderr.write(
                    chalk.red('Error: Could not determine the main git repository root.\n')
                )
                sys.exit(1)
            
            # If we're inside a worktree, switch to the main repo for worktree creation
            if main_repo_root != (find_git_root(get_cwd()) or get_cwd()):
                log_for_diagnostics_no_pii('info', 'worktree_resolved_to_main_repo')
                os.chdir(main_repo_root)
                set_cwd(main_repo_root)
            
            tmux_session_name = generate_tmux_session_name(main_repo_root, worktree_branch_name(slug)) if tmux_enabled else None
        else:
            # Non-git hook mode: no canonical root to resolve, so name the tmux
            # session from cwd — generate_tmux_session_name only basenames the path.
            tmux_session_name = generate_tmux_session_name(get_cwd(), worktree_branch_name(slug)) if tmux_enabled else None
        
        try:
            worktree_session = await create_worktree_for_session(
                get_session_id(),
                slug,
                tmux_session_name,
                {'prNumber': worktree_pr_number} if worktree_pr_number else None,
            )
        except Exception as error:
            sys.stderr.write(
                chalk.red(f'Error creating worktree: {error_message(error)}\n')
            )
            sys.exit(1)
        
        log_event('tengu_worktree_created', {'tmux_enabled': tmux_enabled})
        
        # Create tmux session for the worktree if enabled
        if tmux_enabled and tmux_session_name:
            tmux_result = await create_tmux_session_for_worktree(
                tmux_session_name,
                worktree_session['worktreePath'],
            )
            if tmux_result.get('created'):
                log.info(f'Created tmux session: {tmux_session_name}. To attach: tmux attach -t {tmux_session_name}')
            else:
                log.warning(f'Failed to create tmux session: {tmux_result.get("error")}')
        
        os.chdir(worktree_session['worktreePath'])
        set_cwd(worktree_session['worktreePath'])
        set_original_cwd(get_cwd())
        # --worktree means the worktree IS the session's project, so skills/hooks/
        # cron/etc. should resolve here. (EnterWorktreeTool mid-session does NOT
        # touch projectRoot — that's a throwaway worktree, project stays stable.)
        set_project_root(get_cwd())
        save_worktree_state(worktree_session)
        # Clear memory files cache since originalCwd has changed
        clear_memory_file_caches()
        # Settings cache was populated in init() (via apply_safe_config_environment_variables)
        # and again at capture_hooks_config_snapshot() above, both from the original dir's
        # .cortex/settings.json. Re-read from the worktree and re-capture hooks.
        update_hooks_config_snapshot()
    
    # Background jobs - only critical registrations that must happen before first query
    log_for_diagnostics_no_pii('info', 'setup_background_jobs_starting')
    # Bundled skills/plugins are registered in main.tsx before the parallel
    # get_commands() kick — see comment there. Moved out of setup() because
    # the await points above (start_uds_messaging, ~20ms) meant get_commands()
    # raced ahead and memoized an empty bundled_skills list.
    if not is_bare_mode():
        init_session_memory()  # Synchronous - registers hook, gate check happens lazily
        if feature('CONTEXT_COLLAPSE'):
            try:
                from .services.context_collapse.index import init_context_collapse
                init_context_collapse()
            except ImportError:
                pass
    
    asyncio.ensure_future(lock_current_version())  # Lock current version to prevent deletion by other processes
    log_for_diagnostics_no_pii('info', 'setup_background_jobs_launched')
    
    profile_checkpoint('setup_before_prefetch')
    
    # Pre-fetch promises - only items needed before render
    log_for_diagnostics_no_pii('info', 'setup_prefetch_starting')
    
    # When CORTEX_CODE_SYNC_PLUGIN_INSTALL is set, skip all plugin prefetch.
    # The sync install path in print.ts calls refresh_plugin_state() after
    # installing, which reloads commands, hooks, and agents. Prefetching here
    # races with the install (concurrent copy_plugin_to_versioned_cache / cache_plugin
    # on the same directories), and the hot-reload handler fires clear_plugin_cache()
    # mid-install when policy_settings arrives.
    skip_plugin_prefetch = (
        (get_is_non_interactive_session() and
         is_env_truthy(os.environ.get('CORTEX_CODE_SYNC_PLUGIN_INSTALL'))) or
        # --bare: load_plugin_hooks → load_all_plugins is filesystem work that's
        # wasted when execute_hooks early-returns under --bare anyway.
        is_bare_mode()
    )
    
    if not skip_plugin_prefetch:
        asyncio.ensure_future(get_commands(get_project_root()))
    
    async def prefetch_plugins():
        try:
            from .utils.plugins.load_plugin_hooks import load_plugin_hooks, setup_plugin_hook_hot_reload
            if not skip_plugin_prefetch:
                asyncio.ensure_future(load_plugin_hooks())  # Pre-load plugin hooks (consumed by process_session_start_hooks before render)
                setup_plugin_hook_hot_reload()  # Set up hot reload for plugin hooks when settings change
        except ImportError:
            pass
    
    asyncio.ensure_future(prefetch_plugins())
    
    # --bare: skip attribution hook install + repo classification +
    # session-file-access analytics + team memory watcher. These are background
    # bookkeeping for commit attribution + usage metrics — scripted calls don't
    # commit code, and the 49ms attribution hook stat check (measured) is pure
    # overhead. NOT an early-return: the --dangerously-skip-permissions safety
    # gate, tengu_started beacon, and api_key_helper prefetch below must still run.
    if not is_bare_mode():
        if os.environ.get('USER_TYPE') == 'ant':
            # Prime repo classification cache for auto-undercover mode. Default is
            # undercover ON until proven internal; if this resolves to internal, clear
            # the prompt cache so the next turn picks up the OFF state.
            async def prime_repo_classification():
                try:
                    from .utils.commit_attribution import is_internal_model_repo
                    if await is_internal_model_repo():
                        from .constants.system_prompt_sections import clear_system_prompt_sections
                        clear_system_prompt_sections()
                except ImportError:
                    pass
            
            asyncio.ensure_future(prime_repo_classification())
        
        if feature('COMMIT_ATTRIBUTION'):
            # Dynamic import to enable dead code elimination (module contains excluded strings).
            # Defer to next tick so the git subprocess spawn runs after first render
            # rather than during the setup() microtask window.
            async def register_attribution_hooks():
                try:
                    from .utils.attribution_hooks import register_attribution_hooks
                    register_attribution_hooks()  # Register attribution tracking hooks (ant-only feature)
                except ImportError:
                    pass
            
            asyncio.ensure_future(register_attribution_hooks())
        
        async def register_session_file_access_hooks():
            try:
                from .utils.session_file_access_hooks import register_session_file_access_hooks
                register_session_file_access_hooks()  # Register session file access analytics hooks
            except ImportError:
                pass
        
        asyncio.ensure_future(register_session_file_access_hooks())
        
        if feature('TEAMMEM'):
            async def start_team_memory_watcher():
                try:
                    from .services.team_memory_sync.watcher import start_team_memory_watcher
                    start_team_memory_watcher()  # Start team memory sync watcher
                except ImportError:
                    pass
            
            asyncio.ensure_future(start_team_memory_watcher())
    
    init_sinks()  # Attach error log + analytics sinks and drain queued events
    
    # Session-success-rate denominator. Emit immediately after the analytics
    # sink is attached — before any parsing, fetching, or I/O that could throw.
    # inc-3694 (P0 CHANGELOG crash) threw at check_for_release_notes below; every
    # event after this point was dead. This beacon is the earliest reliable
    # "process started" signal for release health monitoring.
    log_event('tengu_started', {})
    
    asyncio.ensure_future(prefetch_api_key_from_api_key_helper_if_safe(get_is_non_interactive_session()))  # Prefetch safely - only executes if trust already confirmed
    profile_checkpoint('setup_after_prefetch')
    
    # Pre-fetch data for Logo v2 - await to ensure it's ready before logo renders.
    # --bare / SIMPLE: skip — release notes are interactive-UI display data,
    # and get_recent_activity() reads up to 10 session JSONL files.
    if not is_bare_mode():
        global_config = get_global_config()
        last_release_notes_seen = global_config.get('lastReleaseNotesSeen')
        release_check = await check_for_release_notes(last_release_notes_seen)
        if release_check.get('hasReleaseNotes'):
            await get_recent_activity()
    
    # If permission mode is set to bypass, verify we're in a safe environment
    if permission_mode == 'bypassPermissions' or allow_dangerously_skip_permissions:
        # Check if running as root/sudo on Unix-like systems
        # Allow root if in a sandbox (e.g., TPU devspaces that require root)
        if sys.platform != 'win32' and hasattr(os, 'getuid') and os.getuid() == 0:
            if (os.environ.get('IS_SANDBOX') != '1' and
                not is_env_truthy(os.environ.get('CORTEX_CODE_BUBBLEWRAP'))):
                log.error('--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons')
                sys.exit(1)
        
        if (os.environ.get('USER_TYPE') == 'ant' and
            # Skip for Desktop's local agent mode — same trust model as CCR/BYOC
            # (trusted Anthropic-managed launcher intentionally pre-approving everything).
            # Precedent: permissionSetup.ts:861, applySettingsChange.ts:55 (PR #19116)
            os.environ.get('CORTEX_CODE_ENTRYPOINT') != 'local-agent' and
            # Same for CCD (Cortex Code in Desktop) — apps#29127 passes the flag
            # unconditionally to unlock mid-session bypass switching
            os.environ.get('CORTEX_CODE_ENTRYPOINT') != 'cortex-desktop'):
            # Only await if permission mode is set to bypass
            is_docker, has_internet = await asyncio.gather(
                env_dynamic.get_is_docker(),
                env.has_internet_access(),
            )
            is_bubblewrap = env_dynamic.get_is_bubblewrap_sandbox()
            is_sandbox = os.environ.get('IS_SANDBOX') == '1'
            is_sandboxed = is_docker or is_bubblewrap or is_sandbox
            
            if not is_sandboxed or has_internet:
                log.error(f'--dangerously-skip-permissions can only be used in Docker/sandbox containers with no internet access but got Docker: {is_docker}, Bubblewrap: {is_bubblewrap}, IS_SANDBOX: {is_sandbox}, hasInternet: {has_internet}')
                sys.exit(1)
    
    if os.environ.get('NODE_ENV') == 'test':
        return
    
    # Log tengu_exit event from the last session?
    project_config = get_current_project_config()
    if 'lastCost' in project_config and 'lastDuration' in project_config:
        log_event('tengu_exit', {
            'last_session_cost': project_config['lastCost'],
            'last_session_api_duration': project_config['lastAPIDuration'],
            'last_session_tool_duration': project_config['lastToolDuration'],
            'last_session_duration': project_config['lastDuration'],
            'last_session_lines_added': project_config['lastLinesAdded'],
            'last_session_lines_removed': project_config['lastLinesRemoved'],
            'last_session_total_input_tokens': project_config['lastTotalInputTokens'],
            'last_session_total_output_tokens': project_config['lastTotalOutputTokens'],
            'last_session_total_cache_creation_input_tokens': project_config['lastTotalCacheCreationInputTokens'],
            'last_session_total_cache_read_input_tokens': project_config['lastTotalCacheReadInputTokens'],
            'last_session_fps_average': project_config['lastFpsAverage'],
            'last_session_fps_low_1_pct': project_config['lastFpsLow1Pct'],
            'last_session_id': project_config.get('lastSessionId'),
            **project_config.get('lastSessionMetrics', {}),
        })
        # Note: We intentionally don't clear these values after logging.
        # They're needed for cost restoration when resuming sessions.
        # The values will be overwritten when the next session exits.


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "setup",
]
