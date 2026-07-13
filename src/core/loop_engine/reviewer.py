"""
reviewer.py — Milestone 3: maker/checker separation (agent_loop.md §5).

The rule: the model that wrote the change never approves the change.
Enforced structurally here, not by prompt:

  1. Hard-coded auto-rejects run in CODE, before any model call (§5.1):
     - a changed file outside scope.allow_paths (when allow_paths is set)
     - a deny_paths violation
     - a deleted test file, or a test file whose assertion count dropped
       (the classic cheat: "fix" tests by deleting them — the verifier
       can't catch this, since deleted assertions can't fail)
     - a secret-looking string added in the diff
  2. Only if none of those trip does a review request go to a model that
     is, wherever possible, a DIFFERENT provider/model than whatever did
     the editing (actor_model) — see pick_reviewer_model().
  3. The model must return strict JSON (ReviewResult). If it can't be
     parsed, that FAILS CLOSED (approved=False) — a broken reviewer must
     never be silently treated as "no objections".

This step is what verify()'s green path was missing: without it, a passing
test suite was the end of the loop, even if the agent got there by
deleting the failing test.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.loop_engine.loop_spec import Failure, LoopSpec

import logging
log = logging.getLogger("loop_engine.reviewer")

_TEST_FILE_PATTERNS = (
    "test_*.py", "*_test.py", "*.test.js", "*.test.ts", "*.test.jsx", "*.test.tsx",
    "*.spec.js", "*.spec.ts", "*_spec.rb", "*Test.java", "*_test.go",
)

_ASSERTION_PATTERNS = re.compile(
    r"\b(assert\b|assert_equal|assertEqual|assertTrue|assertFalse|expect\(|it\(|test\(|should\.)"
)

_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                              # AWS access key
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                           # OpenAI-style secret key
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9/+_\-\.]{12,}['\"]"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),                           # GitHub PAT
]

# Strongest-first. (provider_type_value, model_id) — matches src/ai/model_registry.py
# ids so a chosen model can be handed straight to BaseProvider.chat(model=...).
REVIEWER_MODEL_PRIORITY: List[Tuple[str, str]] = [
    ("openrouter", "anthropic/claude-opus-4-8"),
    ("openai", "gpt-5.5"),
    ("deepseek", "deepseek-v4-pro"),
    ("openrouter", "anthropic/claude-sonnet-4-5"),
    ("openrouter", "google/gemini-2.5-pro"),
    ("alibaba", "qwen3.7-plus"),
    ("mimo", "mimo-v2.5-pro"),
    ("openrouter", "z-ai/glm-5.2"),
    ("openrouter", "x-ai/grok-4.3"),
    ("openai", "gpt-5.4"),
    ("deepseek", "deepseek-v4-flash"),
]


@dataclass
class ReviewResult:
    approved: bool
    blocking_issues: List[Failure] = field(default_factory=list)
    advisories: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)
    reviewer_model: str = ""
    auto_rejected: bool = False   # True if a hard-coded rule rejected before any model call
    input_tokens: int = 0         # real usage from the reviewer's own API call (0 if auto-rejected)
    output_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "blockingIssues": [f.to_dict() for f in self.blocking_issues],
            "advisories": self.advisories,
            "riskFlags": self.risk_flags,
            "reviewerModel": self.reviewer_model,
            "autoRejected": self.auto_rejected,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
        }


def _git(args: List[str], cwd: str, timeout: int = 20) -> str:
    import os
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, **kwargs)
    return proc.stdout if proc.returncode == 0 else ""


def _is_test_file(path: str) -> bool:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in _TEST_FILE_PATTERNS)


def _matches_any(path: str, globs: List[str]) -> bool:
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, g) for g in globs)


def _changed_files(project_root: str, base_commit: str) -> List[str]:
    if not base_commit:
        return []
    out = _git(["diff", "--name-only", f"{base_commit}..HEAD"], project_root)
    return [l.strip() for l in out.splitlines() if l.strip()]


def _full_diff(project_root: str, base_commit: str, max_chars: int = 12000) -> str:
    if not base_commit:
        return ""
    out = _git(["diff", f"{base_commit}..HEAD"], project_root)
    if len(out) > max_chars:
        return out[:max_chars] + f"\n... (truncated, {len(out) - max_chars} more chars)"
    return out


def _count_assertions(project_root: str, ref: str, path: str) -> int:
    content = _git(["show", f"{ref}:{path}"], project_root)
    return len(_ASSERTION_PATTERNS.findall(content))


# ---------------------------------------------------------------------------
# Hard-coded auto-rejects — run before any model call
# ---------------------------------------------------------------------------

def run_auto_rejects(project_root: str, spec: LoopSpec, base_commit: str) -> Optional[ReviewResult]:
    """Returns a ReviewResult if a hard rule rejects the diff, else None."""
    if not base_commit:
        return None

    changed = _changed_files(project_root, base_commit)
    if not changed:
        return None

    risk_flags: List[str] = []
    blocking: List[Failure] = []

    for path in changed:
        # scope.deny_paths — always enforced
        if _matches_any(path, spec.scope.deny_paths):
            risk_flags.append("touched-deny-path")
            blocking.append(Failure(
                id=f"deny:{path}", file=path, kind="review",
                message=f"Changed '{path}', which matches a deny-path rule. Never touch this file.",
                weight=5,
            ))
            continue

        # scope.allow_paths — enforced only when the caller set one
        if spec.scope.allow_paths and not _matches_any(path, spec.scope.allow_paths):
            risk_flags.append("touched-deny-path")
            blocking.append(Failure(
                id=f"scope:{path}", file=path, kind="review",
                message=f"Changed '{path}', which is outside the allowed scope {spec.scope.allow_paths}.",
                weight=5,
            ))
            continue

        if not _is_test_file(path):
            continue

        # Was this test file deleted? (use returncode, not stdout — `git
        # cat-file -e` prints nothing on success)
        exists_before = _cat_file_exists(project_root, base_commit, path)
        exists_now = _cat_file_exists(project_root, "HEAD", path)
        if exists_before and not exists_now:
            risk_flags.append("test-deleted")
            blocking.append(Failure(
                id=f"testdel:{path}", file=path, kind="review",
                message=f"Test file '{path}' was deleted rather than fixed. That's not allowed.",
                weight=5,
            ))
            continue

        if exists_before and exists_now:
            before_n = _count_assertions(project_root, base_commit, path)
            after_n = _count_assertions(project_root, "HEAD", path)
            if after_n < before_n:
                risk_flags.append("test-weakened")
                blocking.append(Failure(
                    id=f"testweak:{path}", file=path, kind="review",
                    message=f"Test file '{path}' has fewer assertions now ({after_n}) than at loop "
                            f"start ({before_n}) — looks like a test was weakened instead of the code fixed.",
                    weight=5,
                ))

    diff_text = _full_diff(project_root, base_commit)
    added_lines = [l for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
    for line in added_lines:
        for pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                risk_flags.append("secret-like-string")
                blocking.append(Failure(
                    id=f"secret:{hash(line) & 0xffffffff:x}", file="(diff)", kind="review",
                    message="A newly added line looks like it contains a secret/credential. Remove it.",
                    weight=5,
                ))
                break

    if not blocking:
        return None
    return ReviewResult(
        approved=False, blocking_issues=blocking,
        risk_flags=sorted(set(risk_flags)), auto_rejected=True,
    )


def _cat_file_exists(project_root: str, ref: str, path: str) -> bool:
    import os
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(["git", "cat-file", "-e", f"{ref}:{path}"], cwd=project_root,
                           capture_output=True, timeout=10, **kwargs)
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Model selection — strongest configured model, distinct from the actor
# ---------------------------------------------------------------------------

def pick_reviewer_model(actor_model: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Returns (provider_type_value, model_id) for the strongest configured
    provider, preferring one that differs from actor_model. Returns None if
    no provider has an API key configured at all.
    """
    try:
        from src.ai.providers import get_provider_registry, ProviderType
    except Exception as exc:
        log.warning(f"[Reviewer] Could not import provider registry: {exc}")
        return None

    registry = get_provider_registry()
    fallback: Optional[Tuple[str, str]] = None

    for provider_value, model_id in REVIEWER_MODEL_PRIORITY:
        try:
            ptype = ProviderType(provider_value)
        except ValueError:
            continue
        provider = registry.get_provider(ptype)
        if provider is None or not getattr(provider, "_api_key", None):
            continue
        if model_id == actor_model:
            # Keep as a last-resort fallback, but keep looking for a distinct one.
            if fallback is None:
                fallback = (provider_value, model_id)
            continue
        return (provider_value, model_id)

    return fallback


REVIEW_SYSTEM_PROMPT = """You are the reviewer in a maker/checker loop. Another model just made \
changes to satisfy a goal, and the verify gate (tests/lint/build) now passes. Your job is to audit \
the diff BEFORE the loop is allowed to stop — you are the check that catches what a passing test \
suite cannot: weakened assertions, scope violations, and changes that technically pass but don't \
actually satisfy the goal.

Respond with ONLY a JSON object, no prose, no markdown fences:
{"approved": bool, "blockingIssues": [{"file": str, "message": str}], "advisories": [str], \
"riskFlags": [str]}

riskFlags may include: "large-diff", "test-deleted", "test-weakened", "touched-deny-path", \
"secret-like-string". Only set approved=true if you see no blocking problems."""


def _call_reviewer_model(provider_value: str, model_id: str, goal: str, diff_text: str,
                          deny_paths: List[str], attempts_summary: str,
                          usage_out: Optional[Dict[str, int]] = None) -> str:
    """Makes the single-shot review completion call. Isolated as its own
    function so it can be monkeypatched in tests without touching the real
    provider registry.

    `usage_out`, if given, is filled in-place with the REAL input/output
    token counts the provider reports on its ChatResponse (agent_loop.md §9
    cost accounting) -- kept as an optional out-param rather than changing
    the return type so run_review()'s call_model_fn injection point (used
    by tests) doesn't need to change shape.
    """
    from src.ai.providers import get_provider_registry, ProviderType, ChatMessage

    registry = get_provider_registry()
    provider = registry.get_provider(ProviderType(provider_value))

    user_prompt = (
        f"GOAL:\n{goal}\n\n"
        f"NEVER-TOUCH PATHS:\n{deny_paths}\n\n"
        f"ITERATION HISTORY (compressed):\n{attempts_summary or '(no prior attempts)'}\n\n"
        f"FULL DIFF (base..HEAD, may be truncated):\n```diff\n{diff_text or '(empty diff)'}\n```"
    )
    messages = [
        ChatMessage(role="system", content=REVIEW_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    ]
    response = provider.chat(messages, model=model_id, temperature=0.0, max_tokens=1500, stream=False)
    if usage_out is not None:
        usage_out["input"] = getattr(response, "input_tokens", 0) or 0
        usage_out["output"] = getattr(response, "output_tokens", 0) or 0
    if response.error:
        raise RuntimeError(f"Reviewer model call failed: {response.error}")
    return response.content or ""


def _parse_review_json(raw: str, reviewer_model: str) -> ReviewResult:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ReviewResult(
            approved=False, reviewer_model=reviewer_model,
            blocking_issues=[Failure(id="reviewer-parse-error", file="(reviewer)", kind="review",
                                      message="Reviewer response contained no JSON object; failing closed.",
                                      weight=5)],
            advisories=[raw[:300]] if raw else [],
        )
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as exc:
        return ReviewResult(
            approved=False, reviewer_model=reviewer_model,
            blocking_issues=[Failure(id="reviewer-parse-error", file="(reviewer)", kind="review",
                                      message=f"Reviewer response was not valid JSON ({exc}); failing closed.",
                                      weight=5)],
            advisories=[raw[:300]],
        )

    blocking = [
        Failure(id=f"review:{i}", file=str(bi.get("file", "(unknown)")), kind="review",
                message=str(bi.get("message", ""))[:400], weight=4)
        for i, bi in enumerate(data.get("blockingIssues", []) or [])
        if isinstance(bi, dict)
    ]
    approved = bool(data.get("approved", False)) and not blocking
    return ReviewResult(
        approved=approved, reviewer_model=reviewer_model,
        blocking_issues=blocking,
        advisories=[str(a) for a in (data.get("advisories") or [])],
        risk_flags=[str(r) for r in (data.get("riskFlags") or [])],
    )


# ---------------------------------------------------------------------------
# Entry point used by LoopOrchestrator
# ---------------------------------------------------------------------------

def run_review(
    project_root: str,
    spec: LoopSpec,
    base_commit: str,
    actor_model: Optional[str],
    attempts_summary: str,
    call_model_fn: Optional[Callable[[str, str, str, str, List[str], str], str]] = None,
) -> ReviewResult:
    """Runs auto-rejects first; only calls a model if none of those trip.

    `call_model_fn` is injectable for testing — defaults to _call_reviewer_model.
    """
    auto = run_auto_rejects(project_root, spec, base_commit)
    if auto is not None:
        return auto

    picked = pick_reviewer_model(actor_model)
    if picked is None:
        # No provider configured at all. Fail closed rather than silently
        # skip review — an unreviewable green result is exactly the hole
        # this milestone exists to close.
        return ReviewResult(
            approved=False,
            blocking_issues=[Failure(
                id="reviewer-unavailable", file="(reviewer)", kind="review",
                message="No configured LLM provider is available to act as reviewer. "
                        "Configure an API key for at least one provider, or call Loop "
                        "with action='stop' to finalize manually.",
                weight=3,
            )],
            advisories=["Reviewer step skipped: no provider configured."],
        )

    provider_value, model_id = picked
    same_as_actor = actor_model is not None and model_id == actor_model
    diff_text = _full_diff(project_root, base_commit)
    # Only the default caller (_call_reviewer_model) can report real token
    # usage -- an injected call_model_fn (tests) has no ChatResponse to read.
    usage: Dict[str, int] = {}
    try:
        if call_model_fn is None:
            raw = _call_reviewer_model(provider_value, model_id, spec.goal, diff_text,
                                        spec.scope.deny_paths, attempts_summary, usage_out=usage)
        else:
            raw = call_model_fn(provider_value, model_id, spec.goal, diff_text,
                                 spec.scope.deny_paths, attempts_summary)
    except Exception as exc:
        log.error(f"[Reviewer] Model call failed: {exc}")
        return ReviewResult(
            approved=False, reviewer_model=f"{provider_value}/{model_id}",
            blocking_issues=[Failure(id="reviewer-call-failed", file="(reviewer)", kind="review",
                                      message=f"Reviewer model call failed: {exc}", weight=3)],
        )

    result = _parse_review_json(raw, f"{provider_value}/{model_id}")
    result.input_tokens = usage.get("input", 0)
    result.output_tokens = usage.get("output", 0)
    if same_as_actor:
        result.risk_flags = sorted(set(result.risk_flags) | {"reviewer-same-as-actor"})
        result.advisories = result.advisories + [
            "No distinct provider/model was configured for review — the actor's own model "
            "reviewed its own diff. Configure a second provider for a real maker/checker split."
        ]
    return result
