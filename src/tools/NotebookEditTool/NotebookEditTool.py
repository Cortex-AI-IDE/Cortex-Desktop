"""
NotebookEditTool - Jupyter notebook cell editor.

Provides specialized editing for .ipynb files with:
- Cell-based operations (replace, insert, delete)
- Code and markdown cell support
- Execution state management (resets on edit)
- File history tracking
- Read-before-edit validation
- Staleness detection

Jupyter notebooks are JSON files with complex cell structures, requiring
specialized handling beyond plain text editing.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ...utils.errors import is_enoent
from .constants import NOTEBOOK_EDIT_TOOL_NAME
from .prompt import DESCRIPTION, PROMPT
from ...services.toolUseSummary import (
    get_tool_use_summary,
    render_tool_result_message,
    render_tool_use_error_message,
    render_tool_use_message,
    render_tool_use_rejected_message,
)


# Input schema definition
def _input_schema():
    """Schema for NotebookEditTool input parameters."""
    return {
        'notebook_path': {
            'type': str,
            'description': 'The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)',
        },
        'cell_id': {
            'type': Optional[str],
            'description': 'The ID of the cell to edit. When inserting a new cell, the new cell will be inserted after the cell with this ID, or at the beginning if not specified.',
        },
        'new_source': {
            'type': str,
            'description': 'The new source for the cell',
        },
        'cell_type': {
            'type': Optional[str],  # 'code' or 'markdown'
            'description': 'The type of the cell (code or markdown). If not specified, it defaults to the current cell type. If using edit_mode=insert, this is required.',
        },
        'edit_mode': {
            'type': Optional[str],  # 'replace', 'insert', or 'delete'
            'description': 'The type of edit to make (replace, insert, delete). Defaults to replace.',
        },
    }


# Output schema definition
def _output_schema():
    """Schema for NotebookEditTool output."""
    return {
        'new_source': {
            'type': str,
            'description': 'The new source code that was written to the cell',
        },
        'cell_id': {
            'type': Optional[str],
            'description': 'The ID of the cell that was edited',
        },
        'cell_type': {
            'type': str,  # 'code' or 'markdown'
            'description': 'The type of the cell',
        },
        'language': {
            'type': str,
            'description': 'The programming language of the notebook',
        },
        'edit_mode': {
            'type': str,
            'description': 'The edit mode that was used',
        },
        'error': {
            'type': Optional[str],
            'description': 'Error message if the operation failed',
        },
        # Fields for attribution tracking
        'notebook_path': {
            'type': str,
            'description': 'The path to the notebook file',
        },
        'original_file': {
            'type': str,
            'description': 'The original notebook content before modification',
        },
        'updated_file': {
            'type': str,
            'description': 'The updated notebook content after modification',
        },
    }


input_schema = lazy_schema(_input_schema)
output_schema = lazy_schema(_output_schema)


async def _description() -> str:
    """Tool description."""
    return DESCRIPTION


async def _prompt() -> str:
    """Tool prompt/instructions."""
    return PROMPT


def _user_facing_name() -> str:
    """User-facing tool name."""
    return 'Edit Notebook'


def _get_activity_description(input_data: Dict[str, Any]) -> str:
    """Generate activity description for transcript."""
    summary = get_tool_use_summary(input_data)
    return f'Editing notebook {summary}' if summary else 'Editing notebook'


def _to_auto_classifier_input(input_data: Dict[str, Any]) -> str:
    """Convert input to auto-classifier format."""
    # Feature-gated: TRANSCRIPT_CLASSIFIER
    mode = input_data.get('edit_mode') or 'replace'
    return f"{input_data['notebook_path']} {mode}: {input_data['new_source']}"


def _get_path(input_data: Dict[str, Any]) -> str:
    """Get the file path from input."""
    return input_data['notebook_path']


async def _check_permissions(
    input_data: Dict[str, Any], context: ToolUseContext
) -> Dict[str, Any]:
    """Check write permissions for the notebook file."""
    app_state = context.get_app_state()
    return await check_write_permission_for_tool(
        NotebookEditTool,
        input_data,
        app_state.tool_permission_context,
    )


def _map_tool_result_to_block(output: Dict[str, Any], tool_use_id: str) -> Dict[str, Any]:
    """Convert tool result to Anthropic API format."""
    error = output.get('error')
    if error:
        return {
            'tool_use_id': tool_use_id,
            'type': 'tool_result',
            'content': error,
            'is_error': True,
        }
    
    edit_mode = output.get('edit_mode')
    cell_id = output.get('cell_id')
    new_source = output.get('new_source')
    
    if edit_mode == 'replace':
        return {
            'tool_use_id': tool_use_id,
            'type': 'tool_result',
            'content': f'Updated cell {cell_id} with {new_source}',
        }
    elif edit_mode == 'insert':
        return {
            'tool_use_id': tool_use_id,
            'type': 'tool_result',
            'content': f'Inserted cell {cell_id} with {new_source}',
        }
    elif edit_mode == 'delete':
        return {
            'tool_use_id': tool_use_id,
            'type': 'tool_result',
            'content': f'Deleted cell {cell_id}',
        }
    else:
        return {
            'tool_use_id': tool_use_id,
            'type': 'tool_result',
            'content': 'Unknown edit mode',
        }


async def _validate_input(
    input_data: Dict[str, Any], tool_use_context: ToolUseContext
) -> Dict[str, Any]:
    """Validate input parameters before execution."""
    notebook_path = input_data['notebook_path']
    cell_type = input_data.get('cell_type')
    cell_id = input_data.get('cell_id')
    edit_mode = input_data.get('edit_mode') or 'replace'
    
    full_path = notebook_path if os.path.isabs(notebook_path) else os.path.join(get_cwd(), notebook_path)
    
    # SECURITY: Skip filesystem operations for UNC paths to prevent NTLM credential leaks.
    if full_path.startswith('\\\\') or full_path.startswith('//'):
        return {'result': True}
    
    # Check file extension
    if Path(full_path).suffix != '.ipynb':
        return {
            'result': False,
            'message': 'File must be a Jupyter notebook (.ipynb file). For editing other file types, use the FileEdit tool.',
            'errorCode': 2,
        }
    
    # Validate edit mode
    if edit_mode not in ('replace', 'insert', 'delete'):
        return {
            'result': False,
            'message': 'Edit mode must be replace, insert, or delete.',
            'errorCode': 4,
        }
    
    # Cell type required for insert mode
    if edit_mode == 'insert' and not cell_type:
        return {
            'result': False,
            'message': 'Cell type is required when using edit_mode=insert.',
            'errorCode': 5,
        }
    
    # Require Read-before-Edit (matches FileEditTool/FileWriteTool). Without
    # this, the model could edit a notebook it never saw, or edit against a
    # stale view after an external change — silent data loss.
    read_timestamp = tool_use_context.read_file_state.get(full_path)
    if not read_timestamp:
        return {
            'result': False,
            'message': 'File has not been read yet. Read it first before writing to it.',
            'errorCode': 9,
        }
    
    if get_file_modification_time(full_path) > read_timestamp['timestamp']:
        return {
            'result': False,
            'message': 'File has been modified since read, either by the user or by a linter. Read it again before attempting to write it.',
            'errorCode': 10,
        }
    
    # Read file content
    try:
        file_data = read_file_sync_with_metadata(full_path)
        content = file_data['content']
    except Exception as e:
        if is_enoent(e):
            return {
                'result': False,
                'message': 'Notebook file does not exist.',
                'errorCode': 1,
            }
        raise
    
    # Parse JSON
    notebook = safe_parse_json(content)
    if not notebook:
        return {
            'result': False,
            'message': 'Notebook is not valid JSON.',
            'errorCode': 6,
        }
    
    # Validate cell_id
    if not cell_id:
        if edit_mode != 'insert':
            return {
                'result': False,
                'message': 'Cell ID must be specified when not inserting a new cell.',
                'errorCode': 7,
            }
    else:
        # First try to find the cell by its actual ID
        cells = notebook.get('cells', [])
        cell_index = next((i for i, cell in enumerate(cells) if cell.get('id') == cell_id), -1)
        
        if cell_index == -1:
            # If not found, try to parse as a numeric index (cell-N format)
            parsed_cell_index = parse_cell_id(cell_id)
            if parsed_cell_index is not None:
                if parsed_cell_index >= len(cells):
                    return {
                        'result': False,
                        'message': f'Cell with index {parsed_cell_index} does not exist in notebook.',
                        'errorCode': 7,
                    }
            else:
                return {
                    'result': False,
                    'message': f'Cell with ID "{cell_id}" not found in notebook.',
                    'errorCode': 8,
                }
    
    return {'result': True}


async def _call(
    input_data: Dict[str, Any],
    tool_use_context: ToolUseContext,
    *args,
    parent_message=None,
) -> Dict[str, Any]:
    """Execute the notebook edit operation."""
    notebook_path = input_data['notebook_path']
    new_source = input_data['new_source']
    cell_id = input_data.get('cell_id')
    cell_type = input_data.get('cell_type')
    original_edit_mode = input_data.get('edit_mode') or 'replace'
    
    full_path = notebook_path if os.path.isabs(notebook_path) else os.path.join(get_cwd(), notebook_path)
    
    # Track file history if enabled
    if file_history_enabled():
        await file_history_track_edit(
            tool_use_context.update_file_history_state,
            full_path,
            parent_message.uuid if parent_message else None,
        )
    
    try:
        # read_file_sync_with_metadata gives content + encoding + line endings in
        # one safe_resolve_path + readFileSync pass, replacing the previous
        # detect_file_encoding + readFile + detect_line_endings chain.
        file_data = read_file_sync_with_metadata(full_path)
        content = file_data['content']
        encoding = file_data['encoding']
        line_endings = file_data['line_endings']
        
        # Must use non-memoized json_parse here: safe_parse_json caches by content
        # string and returns a shared object reference, but we mutate the
        # notebook in place below (cells.splice, targetCell.source = ...).
        # Using the memoized version poisons the cache for validate_input() and
        # any subsequent call() with the same file content.
        try:
            notebook = json_parse(content)
        except Exception:
            return {
                'data': {
                    'new_source': new_source,
                    'cell_type': cell_type or 'code',
                    'language': 'python',
                    'edit_mode': 'replace',
                    'error': 'Notebook is not valid JSON.',
                    'cell_id': cell_id,
                    'notebook_path': full_path,
                    'original_file': '',
                    'updated_file': '',
                }
            }
        
        # Find cell index
        if not cell_id:
            cell_index = 0  # Default to inserting at the beginning if no cell_id is provided
        else:
            # First try to find the cell by its actual ID
            cells = notebook.get('cells', [])
            cell_index = next((i for i, cell in enumerate(cells) if cell.get('id') == cell_id), -1)
            
            # If not found, try to parse as a numeric index (cell-N format)
            if cell_index == -1:
                parsed_cell_index = parse_cell_id(cell_id)
                if parsed_cell_index is not None:
                    cell_index = parsed_cell_index
            
            if original_edit_mode == 'insert':
                cell_index += 1  # Insert after the cell with this ID
        
        # Convert replace to insert if trying to replace one past the end
        edit_mode = original_edit_mode
        if edit_mode == 'replace' and cell_index == len(cells):
            edit_mode = 'insert'
            if not cell_type:
                cell_type = 'code'  # Default to code if no cell_type specified
        
        # Get notebook language
        language = notebook.get('metadata', {}).get('language_info', {}).get('name') or 'python'
        
        # Generate new cell ID for insert mode (nbformat 4.5+)
        new_cell_id = None
        nbformat = notebook.get('nbformat', 4)
        nbformat_minor = notebook.get('nbformat_minor', 0)
        
        if nbformat > 4 or (nbformat == 4 and nbformat_minor >= 5):
            if edit_mode == 'insert':
                import random
                import string
                new_cell_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=13))
            elif cell_id is not None:
                new_cell_id = cell_id
        
        # Perform the edit
        if edit_mode == 'delete':
            # Delete the specified cell
            cells.pop(cell_index)
        elif edit_mode == 'insert':
            # Create new cell
            if cell_type == 'markdown':
                new_cell = {
                    'cell_type': 'markdown',
                    'id': new_cell_id,
                    'source': new_source,
                    'metadata': {},
                }
            else:
                new_cell = {
                    'cell_type': 'code',
                    'id': new_cell_id,
                    'source': new_source,
                    'metadata': {},
                    'execution_count': None,
                    'outputs': [],
                }
            # Insert the new cell
            cells.insert(cell_index, new_cell)
        else:
            # Replace mode - update existing cell
            target_cell = cells[cell_index]  # validate_input ensures cell_number is in bounds
            target_cell['source'] = new_source
            if target_cell['cell_type'] == 'code':
                # Reset execution count and clear outputs since cell was modified
                target_cell['execution_count'] = None
                target_cell['outputs'] = []
            if cell_type and cell_type != target_cell['cell_type']:
                target_cell['cell_type'] = cell_type
        
        # Write back to file
        IPYNB_INDENT = 1
        updated_content = json_stringify(notebook, indent=IPYNB_INDENT)
        write_text_content(full_path, updated_content, encoding, line_endings)
        
        # Update readFileState with post-write mtime (matches FileEditTool/
        # FileWriteTool). offset:None breaks FileReadTool's dedup match —
        # without this, Read→NotebookEdit→Read in the same millisecond would
        # return the file_unchanged stub against stale in-context content.
        tool_use_context.read_file_state[full_path] = {
            'content': updated_content,
            'timestamp': get_file_modification_time(full_path),
            'offset': None,
            'limit': None,
        }
        
        data = {
            'new_source': new_source,
            'cell_type': cell_type or 'code',
            'language': language,
            'edit_mode': edit_mode or 'replace',
            'cell_id': new_cell_id or None,
            'error': '',
            'notebook_path': full_path,
            'original_file': content,
            'updated_file': updated_content,
        }
        return {'data': data}
    
    except Exception as error:
        error_msg = str(error) if isinstance(error, Exception) else 'Unknown error occurred while editing notebook'
        data = {
            'new_source': new_source,
            'cell_type': cell_type or 'code',
            'language': 'python',
            'edit_mode': 'replace',
            'error': error_msg,
            'cell_id': cell_id,
            'notebook_path': full_path,
            'original_file': '',
            'updated_file': '',
        }
        return {'data': data}


NotebookEditTool = build_tool(
    name=NOTEBOOK_EDIT_TOOL_NAME,
    search_hint='edit Jupyter notebook cells (.ipynb)',
    max_result_size_chars=100_000,
    should_defer=True,
    description=_description,
    prompt=_prompt,
    user_facing_name=_user_facing_name,
    get_tool_use_summary=get_tool_use_summary,
    get_activity_description=_get_activity_description,
    input_schema=input_schema,
    output_schema=output_schema,
    to_auto_classifier_input=_to_auto_classifier_input,
    get_path=_get_path,
    check_permissions=_check_permissions,
    map_tool_result_to_tool_result_block_param=_map_tool_result_to_block,
    render_tool_use_message=render_tool_use_message,
    render_tool_use_rejected_message=render_tool_use_rejected_message,
    render_tool_use_error_message=render_tool_use_error_message,
    render_tool_result_message=render_tool_result_message,
    validate_input=_validate_input,
    call=_call,
)
