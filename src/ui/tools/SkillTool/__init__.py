"""SkillTool package for the Cortex desktop UI.

Deliberately imports nothing: ``skill_tool`` pulls in the skill registry, and
doing that at package-import time would make every ``src.ui.tools`` import pay
for it (and risk a cycle, since ``src.commands`` loads the same registry).
Import ``src.ui.tools.SkillTool.skill_tool`` explicitly where needed.
"""
