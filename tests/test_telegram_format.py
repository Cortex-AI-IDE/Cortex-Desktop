"""Telegram formatter: Markdown becomes HTML and tables become lists."""

from src.services import telegram_format as tf


SAMPLE = """### Files changed

| File | change |
|---|---|
| `a.py` | sql fix |
| `b.py` | tests |

`x()` and **bold** stay.

- one
- two
"""


def test_table_becomes_bullets_not_pipes():
    chunks = tf.to_telegram(SAMPLE)
    body = "\n".join(chunks)

    assert "|" not in body
    assert "---" not in body
    assert "•  <b><code>a.py</code></b> — sql fix" in body
    assert "•  <b><code>b.py</code></b> — tests" in body


def test_table_header_is_one_bold_line():
    body = "\n".join(tf.to_telegram(SAMPLE))

    assert "<b>File · change</b>" in body
    assert "File" in tf.plain_of(body)


def test_tables_without_separator_are_all_rows():
    text = "| a | b |\n| c | d |"
    body = "\n".join(tf.to_telegram(text))

    assert "<b>a</b> — b" in body
    assert "<b>c</b> — d" in body


def test_heading_is_bold_and_never_nested():
    body = "\n".join(tf.to_telegram("## Speed **matters**"))

    assert body.startswith("<b>")
    assert body.count("<b>") == 1


def test_code_fence_becomes_pre_and_hides_backticks():
    body = "\n".join(tf.to_telegram("```python\nx = 1 < 2\n```"))

    assert "<pre>x = 1 &lt; 2</pre>" in body
    assert "```" not in body


def test_inline_code_and_bold_survive():
    body = "\n".join(tf.to_telegram("Use `pip` and **run** it."))

    assert "<code>pip</code>" in body
    assert "<b>run</b>" in body


def test_html_in_the_answer_is_escaped():
    body = "\n".join(tf.to_telegram("<script>alert(1)</script>"))

    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_link_becomes_anchor():
    body = "\n".join(tf.to_telegram("see [docs](https://example.com/a)"))

    assert 'href="https://example.com/a"' in body
    assert ">docs</a>" in body


def test_control_tags_are_removed():
    text = "<task_summary>{\"a\": 1}</task_summary>\nDone."

    assert tf.to_telegram(text) == ["Done."]


def test_blank_input_gives_nothing():
    assert tf.to_telegram("") == []
    assert tf.to_telegram(None) == []
    assert tf.to_telegram("   \n  ") == []


def test_truncation_marker_on_the_last_chunk():
    chunks = tf.to_telegram("x" * 500, max_chars=100)

    assert chunks[-1].endswith(tf.TRUNCATED)


def test_long_answer_is_split_under_the_limit():
    text = "\n\n".join(f"paragraph {i} " + "y" * 200 for i in range(40))
    chunks = tf.to_telegram(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= tf.MAX_MESSAGE for chunk in chunks)


def test_every_chunk_has_balanced_tags():
    text = "\n\n".join(["**bold** and `code`", "```\n" + "z\n" * 900 + "```", "| a | b |\n|---|---|\n| 1 | 2 |"])
    chunks = tf.to_telegram(text, limit=500)

    for chunk in chunks:
        for tag in ("b", "i", "code", "pre", "s", "a"):
            assert chunk.count(f"<{tag}") == chunk.count(f"</{tag}>"), (tag, chunk[:120])


def test_plain_of_round_trips_the_readable_text():
    body = tf.plain_of("\n".join(tf.to_telegram(SAMPLE)))

    assert "Files changed" in body
    assert "sql fix" in body
    assert "<b>" not in body
    assert "&amp;" not in body


def test_a_single_enormous_line_is_still_split():
    body = tf.to_telegram("**" + "a" * 5000 + "**", limit=400)

    assert len(body) > 1
    assert all(len(chunk) <= 400 for chunk in body)


def test_table_row_cap_keeps_a_note():
    rows = "\n".join(f"| f{i}.py | change {i} |" for i in range(tf.TABLE_MAX_ROWS + 5))
    body = "\n".join(tf.to_telegram("| File | what |\n|---|---|\n" + rows))

    assert "5 more rows" in body


def test_carriage_returns_do_not_leak():
    body = tf.to_telegram("one\r\ntwo\r\n")

    assert "\r" not in body


def test_horizontal_rule_is_not_a_table_row():
    body = "\n".join(tf.to_telegram("a\n\n---\n\nb"))

    assert "─" in body
    assert "|" not in body
