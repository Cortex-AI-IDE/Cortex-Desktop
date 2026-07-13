"""
Symbol Indexer Plugin for Cortex AI Agent IDE.
Demonstrates usage of the codebase index API.
"""

from src.plugin.plugin_manager import Plugin
from src.core.codebase_index import SymbolType
from src.utils.logger import get_logger

log = get_logger("plugin.symbol_indexer")


class SymbolIndexerPlugin(Plugin):
    """Plugin that provides symbol indexing capabilities."""
    
    def activate(self, api):
        log.info("SymbolIndexerPlugin activated")
        self.api = api
        # Register a command or menu item maybe
        # For now, just log when project opens
        log.info("Plugin ready. Use api.codebase_index to access symbol index.")
    
    def deactivate(self):
        log.info("SymbolIndexerPlugin deactivated")
    
    def on_project_open(self, project_path: str):
        """Called when a project is opened."""
        log.info(f"Project opened: {project_path}")
        try:
            index = self.api.codebase_index
            if index is None:
                log.warning("No codebase index available")
                return
            stats = index.get_project_stats()
            log.info(f"Codebase index stats: {stats}")
            # Example: find all classes
            classes = index.find_symbols(sym_type=SymbolType.CLASS)
            log.info(f"Found {len(classes)} classes in project")
            # Log first 5 class names
            for cls in classes[:5]:
                log.info(f"  - {cls.name} at {cls.file_path}:{cls.line}")
        except Exception as e:
            log.error(f"Error accessing codebase index: {e}")
    
    def on_file_open(self, file_path: str):
        """Called when a file is opened."""
        # Example: get symbols in this file
        try:
            index = self.api.codebase_index
            if index is None:
                return
            symbols = index.get_file_symbols(file_path)
            log.debug(f"File {file_path} has {len(symbols)} symbols")
        except Exception:
            pass