"""Replies that render as one big code block (2026-10-21).

An inline mention of a fence - "That stray ` ``` ` lands", a table cell
"| A fenced ```json example |" - was split onto a line of its own by
auto_fix_broken_code_fences(). A ``` alone on a line IS a fence, so the rest
of the reply (headings, tables, the task summary) rendered as monospace, live
and again after every restart. Measured on a real saved reply: 1,854 of 2,122
characters inside one <pre>.

Two halves:
  - auto_fix_broken_code_fences() no longer creates such fences,
  - sanitize_prose_markdown() repairs replies older builds already saved.

Run:  python -m pytest tests/test_prose_sanitize.py -q
"""
import re

from src.ui.chat_text import auto_fix_broken_code_fences, sanitize_prose_markdown

FENCE_LINE = re.compile(r"(?m)^[ \t]*```")


def fence_lines(text):
    return len(FENCE_LINE.findall(text))


class TestFenceRulesLeaveInlineMentionsAlone:
    def test_inline_code_mention(self):
        src = "That stray ` ``` ` lands on its own line."
        assert auto_fix_broken_code_fences(src) == src

    def test_fence_inside_a_table_cell(self):
        src = "| The JSON block | A fenced ```json example inside my reply |"
        assert auto_fix_broken_code_fences(src) == src

    def test_plain_mention_mid_sentence(self):
        src = "Wrap it in ``` fences before you paste it."
        assert fence_lines(auto_fix_broken_code_fences(src)) == 0


class TestRealFenceRepairsStillWork:
    def test_opener_after_a_label(self):
        assert auto_fix_broken_code_fences("- **Web Browser**: ```bash\nls -la\n```") == \
            "- **Web Browser**:\n```bash\nls -la\n```"

    def test_code_on_the_opening_line(self):
        assert auto_fix_broken_code_fences("```python print(1)\n```") == "```python\nprint(1)\n```"

    def test_bare_fence_with_code_on_its_line(self):
        assert auto_fix_broken_code_fences("``` echo hi\n```") == "```\necho hi\n```"

    def test_well_formed_block_unchanged(self):
        src = "Run this:\n\n```bash\nnpm test\n```\n\nDone."
        assert auto_fix_broken_code_fences(src) == src


class TestRepairOfAlreadySavedReplies:
    def test_scar_of_a_split_inline_mention(self):
        saved = "That stray `\n```\n` lands on its own line.\n\n## Next\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
        fixed = sanitize_prose_markdown(saved)
        assert "That stray ` ``` ` lands" in fixed
        assert fence_lines(fixed) == 0

    def test_fence_stuck_under_a_table_row(self):
        saved = ("| Screenshot | What it is |\n| --- | --- |\n| The JSON block | A fenced\n"
                 "```\nexample inside my reply |\n\n## After\n\nText.")
        fixed = sanitize_prose_markdown(saved)
        assert fence_lines(fixed) == 0
        assert "## After" in fixed

    def test_unclosed_fence_is_closed(self):
        fixed = sanitize_prose_markdown("Intro\n\n```bash\nls -la\n\nMore text")
        assert fence_lines(fixed) % 2 == 0

    def test_task_summary_tags_removed(self):
        fixed = sanitize_prose_markdown(
            'Done.\n\n<task_summary>{"title": "x", "files": []}</task_summary>')
        assert "<task_summary" not in fixed
        assert fixed.strip().startswith("Done.")

    def test_idempotent_and_clean_text_unchanged(self):
        clean = "## Title\n\nSome text with `code`.\n\n```python\nprint(1)\n```\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
        assert sanitize_prose_markdown(clean) == clean
        once = sanitize_prose_markdown("That stray `\n```\n` lands.")
        assert sanitize_prose_markdown(once) == once
