"""Desktop-side agent tool ports.

This package exists so ``src.ui.tools.SkillTool.skill_tool`` is importable at
all: without an ``__init__.py`` here and in ``SkillTool/`` the directory was
never a package, which is why its ``from src.commands import ...`` could not
have worked even if the target module had existed.

Kept intentionally empty. Do not import tool modules here: they pull in the
skill registry and are loaded lazily by their callers.
"""
