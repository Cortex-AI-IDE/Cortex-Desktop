# prompt.py
# Prompt and description constants for FileReadTool.

# Tool name constant. The app loads this class by dotted path, so the name stays.
FILE_READ_TOOL_NAME = 'Read'

# Stub message returned when the file has not changed since the last read
FILE_UNCHANGED_STUB = (
    'File unchanged since your last read. The copy already in this conversation '
    'is current, so work from that instead of reading it again.'
)

# Maximum lines returned by a default read (matches agent_bridge
# DEFAULT_READ_CHUNK_LINES_FALLBACK)
MAX_LINES_TO_READ = 500

# Description
DESCRIPTION = 'Read a file from disk and return its contents with line numbers.'

# Line format instruction
LINE_FORMAT_INSTRUCTION = (
    '- Every line arrives numbered from 1, in the style of cat -n'
)

# Offset instructions
OFFSET_INSTRUCTION_DEFAULT = (
    '- Leave limit out and you get one safe chunk. To go deeper, call again with '
    'offset and limit.'
)

OFFSET_INSTRUCTION_TARGETED = (
    '- Once you know which part of the file matters, ask for only that part. On a '
    'large file this is the difference between one cheap call and an expensive one.'
)


def is_pdf_supported() -> bool:
    """Cortex reads PDFs directly, so this is always true."""
    return True


def render_prompt_template(
    line_format: str,
    max_size_instruction: str,
    offset_instruction: str,
) -> str:
    """Renders the Read tool prompt template."""
    bash_tool_name = 'Bash'

    pdf_instruction = ''
    if is_pdf_supported():
        pdf_instruction = (
            chr(10) + '- PDFs are read directly. Past 10 pages you must pass pages '
            '(for example pages: "1-5"), at most 20 pages per call.'
        )

    return f'''Reads a file from disk and returns its contents.

Assume this process can reach every file on the machine. If the user hands you a path, treat it as real. A bad path comes back as an error, never as an invented file.

How to call it:
- file_path must be absolute. Relative paths are rejected.
- With no limit it returns the first {MAX_LINES_TO_READ} lines{max_size_instruction}
{offset_instruction}
{line_format}
- Images (PNG, JPG and the rest) arrive as visual content, because Cortex AI IDE hands them to a vision-capable model.{pdf_instruction}
- Notebooks (.ipynb) arrive with every cell plus its stored output, so code, prose and rendered results stay together.
- Files only. To list a directory, run ls through the {bash_tool_name} tool.
- Screenshots are routine input. Whenever the user gives a path to one, open it here, including the temporary paths the IDE creates.
- A file that exists but is empty returns a system reminder in place of content.
- Reading a file you already hold is waste. When the tool reports nothing changed, keep the copy you have.'''


__all__ = [
    'FILE_READ_TOOL_NAME',
    'FILE_UNCHANGED_STUB',
    'MAX_LINES_TO_READ',
    'DESCRIPTION',
    'LINE_FORMAT_INSTRUCTION',
    'OFFSET_INSTRUCTION_DEFAULT',
    'OFFSET_INSTRUCTION_TARGETED',
    'is_pdf_supported',
    'render_prompt_template',
]
