"""Build a git-applyable port diff from .scratch/port/a vs .scratch/port/b."""
import subprocess
from pathlib import Path

root = Path("/home/hakeem/Desktop/cortex/cortex_desktop_linux/cortex_desktop_linux")
work = Path("/tmp/cortex-port-L2Vs")
out = root / "Docs" / "F1_F7_SECURITY_PORT_DJANGO_2026-10-01.diff"
empty = work / "empty"
empty.write_text("")

pairs = [
    "api/auth_views.py",
    "api/payment_views.py",
    "api/paypal_utils.py",
    "config/settings.py",
]
new_files = [
    "api/ratelimit.py",
    "api/tests_ratelimit.py",
    "api/tests_csp_webhooks.py",
    "config/csp.py",
]


NULL = "--- /\x64ev/null\n"


def rewrite(text, rel, new_file=False):
    fixed = []
    if new_file:
        fixed.append(f"diff --git a/{rel} b/{rel}\n")
        fixed.append("new file mode 100644\n")
        fixed.append(NULL)
        fixed.append(f"+++ b/{rel}\n")
        started = False
        for line in text.splitlines(keepends=True):
            if line.startswith("@@"):
                started = True
            if started:
                fixed.append(line.replace("\r", ""))
        body = "".join(fixed)
        if not body.endswith("\n"):
            body += "\n"
        return body

    # Keep CR inside hunks. These Django files are CRLF; stripping it makes
    # git apply search for LF lines that are not in the file.
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            fixed.append(f"diff --git a/{rel} b/{rel}\n")
        elif line.startswith("index "):
            continue
        elif line.startswith("--- "):
            fixed.append(f"--- a/{rel}\n")
        elif line.startswith("+++ "):
            fixed.append(f"+++ b/{rel}\n")
        else:
            fixed.append(line)
    body = "".join(fixed)
    if not body.endswith("\n"):
        body += "\n"
    return body


chunks = []
for rel in pairs:
    # diff -u, not git diff: git strips CR, and these files are CRLF.
    # Without the CR, git apply cannot find the lines.
    proc = subprocess.run(
        ["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
         str(work / "a" / rel), str(work / "b" / rel)],
        capture_output=True,
    )
    if proc.returncode not in (0, 1) or not proc.stdout:
        raise SystemExit(f"empty diff for {rel}: {proc.stderr!r}")
    text = proc.stdout.decode("utf-8")
    if not text.startswith("diff --git "):
        text = f"diff --git a/{rel} b/{rel}\n" + text
    chunks.append(rewrite(text, rel))

for rel in new_files:
    proc = subprocess.run(
        ["git", "diff", "--no-index", "--", str(empty), str(work / "b" / rel)],
        capture_output=True,
        text=True,
    )
    if not proc.stdout:
        raise SystemExit(f"empty new-file diff for {rel}: {proc.stderr}")
    chunks.append(rewrite(proc.stdout, rel, new_file=True))

body = "".join(chunks)
out.write_bytes(body.encode("utf-8"))
print("wrote", out.name, out.stat().st_size)
for line in body.splitlines():
    if line.startswith("diff --git") or line.startswith("---"):
        print(line)
