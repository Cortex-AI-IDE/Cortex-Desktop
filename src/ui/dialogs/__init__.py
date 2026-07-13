# src.ui.dialogs package — required so PyInstaller's collect_submodules('src.ui')
# discovers the dialog modules (update_dialog, memory_manager, diff_viewer) in
# frozen builds. Without this file the directory is a namespace package, which
# collect_submodules() silently skips.
