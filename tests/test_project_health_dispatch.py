"""Contract exemptions for the unused-symbol pass.

Two shapes of method are declarations, not implementations, so no call site
can ever exist for them and reporting them as "unused" is a false positive:

* **Runtime dispatch.** ``getattr(plugin, f"on_{event}")`` builds the method
  name from a literal prefix plus a runtime suffix. Every ``on_*`` hook of
  that plugin base is reached this way, and none of them appears at a call
  site.
* **Empty contract stubs.** A body of ``pass``, ``...`` or
  ``raise NotImplementedError`` exists to be overridden by external or
  subclass code. Deleting it would remove the contract, not dead code.

Both exemptions are narrow: the prefix rule applies to methods only, and the
stub rule needs an actually empty body. A method with a real body that nothing
calls must still be reported, which the negative cases below pin down.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.project_health import ProjectHealthScanner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _unused(payload: dict) -> set:
    return {u["name"] for u in payload["dead_code"]["unused_symbols"]}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "main.py", "import pkg.plugins\n")
    return tmp_path


# -- runtime dispatch by literal prefix ---------------------------------------


def test_fstring_dispatch_prefix_exempts_the_hooks(project: Path):
    _write(
        project / "pkg" / "plugins.py",
        "class Plugin:\n"
        "    def on_file_open(self, path):\n"
        "        self.seen = path\n",
    )
    _write(
        project / "pkg" / "manager.py",
        "class Manager:\n"
        "    def __init__(self):\n"
        "        self.plugins = []\n"
        "    def broadcast(self, event_name, *args):\n"
        "        for plugin in self.plugins:\n"
        "            handler = getattr(plugin, f'on_{event_name}', None)\n"
        "            if handler:\n"
        "                handler(*args)\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "on_file_open" not in _unused(payload)


def test_concatenated_dispatch_prefix_exempts_the_hooks(project: Path):
    _write(
        project / "pkg" / "plugins.py",
        "class Plugin:\n"
        "    def on_project_open(self, path):\n"
        "        self.seen = path\n",
    )
    _write(
        project / "pkg" / "manager.py",
        "class Manager:\n"
        "    def __init__(self):\n"
        "        self.plugins = []\n"
        "    def broadcast(self, event_name):\n"
        "        for plugin in self.plugins:\n"
        "            handler = getattr(plugin, 'on_' + event_name, None)\n"
        "            if handler:\n"
        "                handler(event_name)\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "on_project_open" not in _unused(payload)


def test_dispatch_prefix_does_not_exempt_a_module_level_function(project: Path):
    """The prefix rule is for methods; a stray ``on_*`` function stays dead."""
    _write(
        project / "pkg" / "manager.py",
        "class Manager:\n"
        "    def broadcast(self, event_name):\n"
        "        return getattr(self, f'on_{event_name}', None)\n"
        "\n"
        "def on_orphan():\n"
        "    return 1\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "on_orphan" in _unused(payload)


def test_dispatch_prefix_does_not_exempt_an_unrelated_method(project: Path):
    _write(
        project / "pkg" / "manager.py",
        "class Manager:\n"
        "    def broadcast(self, event_name):\n"
        "        return getattr(self, f'on_{event_name}', None)\n"
        "\n"
        "class Report:\n"
        "    def render(self):\n"
        "        return 'x'\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "render" in _unused(payload)


# -- empty contract stubs -----------------------------------------------------


def test_pass_only_method_is_a_contract_not_dead_code(project: Path):
    _write(
        project / "pkg" / "plugins.py",
        "class Plugin:\n"
        "    def on_file_save(self, path):\n"
        "        \"\"\"Called when a file is saved.\"\"\"\n"
        "        pass\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "on_file_save" not in _unused(payload)


def test_ellipsis_only_method_is_a_contract(project: Path):
    _write(
        project / "pkg" / "bridge.py",
        "class Api:\n"
        "    def register_command(self, name):\n"
        "        ...\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "register_command" not in _unused(payload)


def test_not_implemented_method_is_a_contract(project: Path):
    _write(
        project / "pkg" / "bridge.py",
        "class Api:\n"
        "    def add_menu_item(self, menu):\n"
        "        raise NotImplementedError\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "add_menu_item" not in _unused(payload)


def test_a_method_with_a_real_body_is_still_reported(project: Path):
    """The stub rule must not become a blanket 'methods are exempt' rule."""
    _write(
        project / "pkg" / "bridge.py",
        "class Api:\n"
        "    def add_toolbar_button(self, icon):\n"
        "        \"\"\"Add a toolbar button.\"\"\"\n"
        "        self.icon = icon\n"
        "        return self.icon\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "add_toolbar_button" in _unused(payload)


def test_a_function_with_an_empty_body_is_still_reported(project: Path):
    """A module-level ``pass`` function is not a class contract."""
    _write(
        project / "pkg" / "orphans.py",
        "def helper():\n"
        "    pass\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "helper" in _unused(payload)
