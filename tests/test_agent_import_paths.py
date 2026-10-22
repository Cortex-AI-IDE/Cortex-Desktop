"""Relative imports in the agent tree must reach the module they name.

tool_search_tool.py sits two packages below src/agent/src but imported the
shared utils and services with two dots and snake_case names, so every one
of those imports raised ImportError and the local fallback ran instead. The
ones whose target is a real module now import it; the ones whose target is
an empty generated stub were replaced by the local code outright.
"""
from __future__ import annotations

import pytest

import src.agent.src.tools.tool_search_tool.tool_search_tool as tool_search
import src.agent.src.utils.settings.settings as settings
from src.agent.src.services import analytics
from src.agent.src.utils import log as agent_log


def test_tool_search_debug_logging_is_the_shared_logger() -> None:
    assert tool_search.log_for_debugging is agent_log.logForDebugging


def test_tool_search_analytics_is_the_analytics_package() -> None:
    assert tool_search.log_event is analytics.logEvent


def test_tool_search_local_helpers_still_behave() -> None:
    assert tool_search.escape_regexp("a.b*") == r"a\.b\*"
    assert tool_search.is_tool_search_enabled_optimistic() is True
    assert tool_search.lazy_schema(len) is len


def test_remote_managed_settings_cache_is_empty() -> None:
    assert settings.getRemoteManagedSettingsSyncFromCache() is None


@pytest.mark.parametrize(("platform", "origin"), [("darwin", "plist"), ("win32", "hklm")])
def test_policy_origin_names_the_mdm_store_for_the_platform(monkeypatch, platform, origin) -> None:
    """The platform lookup went through an empty stub, so macOS said 'hklm'."""
    monkeypatch.setattr(settings, "getMdmSettings", lambda: {"settings": {"k": 1}})
    monkeypatch.setattr(settings.sys, "platform", platform)

    assert settings.getPolicySettingsOrigin() == origin
