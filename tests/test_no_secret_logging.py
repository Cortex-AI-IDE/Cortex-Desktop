# W14 regression guard. A log line must never carry secret material or a whole
# response body: cortex.log is the most-copied artifact in the app, and every bug
# report drags it along.
#
# Whole-file substring checks rather than a per-line scan, because the fixed log
# calls are multi-line f-strings and a line regex cannot see them whole.

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BRIDGE = SRC / "ai" / "agent_bridge.py"

# pattern -> what logging it would leak
FORBIDDEN_ANYWHERE = {
    "license_key[:": "a slice of the subscription licence key",
    "{proxy_result}": "the model's whole answer, i.e. the user's own content",
}

FORBIDDEN_IN_BRIDGE = {
    "Subscription info: {sub_info}": "the subscription payload, which carries license_key",
}


def test_no_secret_or_body_logged_across_src():
    findings = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, leak in FORBIDDEN_ANYWHERE.items():
            if pattern in text:
                findings.append(f"{path.relative_to(ROOT)}: {pattern!r} leaks {leak}")
    assert not findings, "\n".join(findings)


def test_no_subscription_payload_logged_in_bridge():
    text = BRIDGE.read_text(encoding="utf-8")
    leaked = [why for pattern, why in FORBIDDEN_IN_BRIDGE.items() if pattern in text]
    assert not leaked, "agent_bridge.py logs " + "; ".join(leaked)


def test_proxy_diagnostics_survive_the_redaction():
    # Redaction must not become deletion. If the log stops saying whether a key was
    # present and how large the reply was, the next proxy failure is undiagnosable.
    text = BRIDGE.read_text(encoding="utf-8")
    assert "license_key_present={bool(license_key)}" in text
    assert "license_key_length={len(license_key)}" in text
    assert "status={_proxy_status}" in text
    assert "bytes={_proxy_bytes}" in text
