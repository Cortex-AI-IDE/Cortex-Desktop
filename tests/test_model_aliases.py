"""Regression tests for the model alias table.

``src/agent/src/utils/model/aliases.py`` was deleted as "unused" while two
live callers still referenced it: ``tools/gen_model_inventory.py`` (a hard
import failure, reported as a project-health warning) and the memory manager
(a guarded import that silently disabled alias resolution).

The generator writes ``Docs/MODEL_INVENTORY.md`` at import time, so the
consumer check imports its *dependencies*, never the generator itself.
"""
from __future__ import annotations

import importlib

import pytest

ALIASES_MODULE = "src.agent.src.utils.model.aliases"

# Every module the generator requires at top level.
GENERATOR_REQUIRED_MODULES = [
    "src.ai.model_registry",
    "src.ai.model_limits",
    "src.agent.src.utils.thinking",
    ALIASES_MODULE,
]


def test_aliases_module_is_importable():
    module = importlib.import_module(ALIASES_MODULE)
    assert getattr(module, "ALIAS_MAP"), "ALIAS_MAP must not be empty"


@pytest.mark.parametrize("module", GENERATOR_REQUIRED_MODULES)
def test_generator_top_level_imports_resolve(module):
    """The exact imports that broke when the module was deleted."""
    assert importlib.import_module(module) is not None


def test_alias_table_is_well_formed():
    from src.agent.src.utils.model.aliases import ALIAS_MAP

    for alias, target in ALIAS_MAP.items():
        assert isinstance(alias, str) and alias, "alias keys must be non-empty strings"
        assert isinstance(target, str) and target, "alias targets must be non-empty"
        assert alias == alias.strip(), f"{alias!r} has surrounding whitespace"
        assert target == target.strip(), f"{target!r} has surrounding whitespace"


def test_alias_targets_are_not_other_aliases():
    """Resolution is a single dict lookup, never a chain, so no target is a key."""
    from src.agent.src.utils.model.aliases import ALIAS_MAP

    chained = sorted(set(ALIAS_MAP) & set(ALIAS_MAP.values()))
    assert chained == [], f"alias points at another alias: {chained}"


def test_resolve_or_passthrough_maps_a_known_alias():
    from src.agent.src.utils.model.aliases import ALIAS_MAP, resolveOrPassthrough

    alias = sorted(ALIAS_MAP)[0]
    assert resolveOrPassthrough(alias) == ALIAS_MAP[alias]


def test_resolve_or_passthrough_keeps_an_unknown_id():
    from src.agent.src.utils.model.aliases import resolveOrPassthrough

    unknown = "vendor/model-that-is-not-an-alias"
    assert resolveOrPassthrough(unknown) == unknown


def test_resolve_alias_returns_none_for_unknown():
    from src.agent.src.utils.model.aliases import resolveAlias

    assert resolveAlias("definitely-not-an-alias") is None
