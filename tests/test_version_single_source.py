"""W12: one source of truth for the version.

``src/version.py`` is the only place the version is typed. ``package.json`` is
read by npm and by anyone scanning the repo, and it had drifted four patches
behind (3.0.63 against a 3.0.66 app). That is the same failure this project
already fixed once, when ``cortex_api.py`` sat at 2.8.5 for four releases and
sent that stale number on every API call.

The build stamps ``package.json`` from ``src/version.py``. These tests are the
guard: they fail the moment the two disagree, whether because someone edited
one by hand or because the stamp step was skipped.
"""

import json
import re
from pathlib import Path

import pytest

from src.version import MSIX_VERSION, VERSION

ROOT = Path(__file__).resolve().parent.parent


def _package() -> dict:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))


def test_package_json_matches_version_module() -> None:
    stamped = _package()["version"]
    assert stamped == VERSION, (
        f"package.json says {stamped!r}, src/version.py says {VERSION!r}. "
        "Edit src/version.py only; run the build to stamp package.json."
    )


@pytest.mark.parametrize("part", ["major", "minor", "patch"])
def test_version_is_a_numeric_release_triple(part: str) -> None:
    """to_msix_version() splits on '.', so a stray suffix must not reach it."""
    parts = VERSION.split(".")
    assert len(parts) == 3, f"{VERSION!r} is not MAJOR.MINOR.PATCH"
    index = {"major": 0, "minor": 1, "patch": 2}[part]
    assert re.fullmatch(r"\d+", parts[index]), f"{part} is not numeric in {VERSION!r}"


def test_msix_version_is_four_parts_with_zero_revision() -> None:
    """MSIX rejects any revision other than 0, so a bad bump fails here."""
    parts = MSIX_VERSION.split(".")
    assert len(parts) == 4, MSIX_VERSION
    assert parts[3] == "0", f"MSIX revision must be 0, got {MSIX_VERSION!r}"
    assert MSIX_VERSION.startswith(VERSION + ".0"), (
        f"MSIX version {MSIX_VERSION!r} does not derive from {VERSION!r}"
    )


def test_no_second_version_constant_in_the_installer_spec() -> None:
    """cortex_setup.iss must take the version from the build, not hold its own.

    A literal ``AppVersion=3.0.66`` here is how the installer starts shipping a
    number nobody bumped.
    """
    iss = ROOT / "cortex_setup.iss"
    if not iss.exists():
        pytest.skip("cortex_setup.iss not present")
    text = iss.read_text(encoding="utf-8", errors="replace")
    literals = re.findall(r"^\s*#define\s+MyAppVersion\s+(\S.*)$", text, re.MULTILINE)
    for literal in literals:
        assert not re.fullmatch(r'"\d+\.\d+\.\d+"', literal.strip()), (
            f"cortex_setup.iss hardcodes a version: {literal.strip()}. "
            "Use the /DMyAppVersion the build passes."
        )
