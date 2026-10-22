"""Settings -> Appearance -> Theme must survive a restart.

Settings._save() writes only the leaves this instance touched and copies the
rest from the file on disk, so a sibling Cortex cannot revert keys it never
changed. The `theme` property setter wrote _data["theme"] without marking it
touched, so every save copied the old value back from disk: picking Dark,
then restarting, came back in whatever the file already said (Light).
"""

import json

import pytest

from src.config import settings as settings_mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod.Path, "home", classmethod(lambda cls: tmp_path))
    cfg = tmp_path / ".cortex"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps({"theme": "light", "ui": {"color_theme": "cortex"}}),
        encoding="utf-8")
    return cfg / "settings.json"


def _on_disk(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_theme_choice_is_written_to_disk(home):
    s = settings_mod.Settings()
    assert s.theme == "light"
    s.theme = "dark"
    assert _on_disk(home)["theme"] == "dark"
    # A restart reads the file again.
    assert settings_mod.Settings().theme == "dark"


def test_theme_and_color_theme_survive_restart_together(home):
    s = settings_mod.Settings()
    s.set("ui", "color_theme", "dracula")
    s.theme = "system"
    s.set("ui", "font_size", 15)   # a later unrelated save must not revert it
    restarted = settings_mod.Settings()
    assert restarted.theme == "system"
    assert restarted.get("ui", "color_theme") == "dracula"
