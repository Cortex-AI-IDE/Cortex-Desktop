"""
Plugin System for Cortex AI IDE
Provides extensibility through plugins
"""

from PyQt6.QtCore import QObject, pyqtSignal
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Type
from dataclasses import dataclass
from pathlib import Path
import importlib.util
import sys
import json
from src.utils.logger import get_logger

log = get_logger("plugin_manager")


@dataclass
class PluginInfo:
    """Plugin metadata."""
    id: str
    name: str
    version: str
    author: str
    description: str
    min_api_version: str
    dependencies: List[str]
    entry_point: str


class PluginAPI:
    """API provided to plugins."""
    
    def __init__(self, ide_instance):
        self._ide = ide_instance
        
    @property
    def editor(self):
        """Get current editor."""
        return self._ide.current_editor()
        
    @property
    def project_path(self) -> Optional[str]:
        """Get current project path."""
        if hasattr(self._ide, '_project_manager'):
            return str(self._ide._project_manager.root) if self._ide._project_manager.root else None
        return None

    @property
    def codebase_index(self):
        """Get the codebase index for the current project."""
        if hasattr(self._ide, 'codebase_index'):
            return self._ide.codebase_index
        return None
        
    def show_message(self, message: str, message_type: str = "info"):
        """Show a message to the user."""
        # Implementation would show in UI
        log.info(f"Plugin message [{message_type}]: {message}")
        
    def register_command(self, name: str, callback, shortcut: str = None):
        """Register a command."""
        # Would register in command palette
        pass
        
    def add_menu_item(self, menu: str, label: str, callback):
        """Add an item to a menu."""
        pass
        
    def add_toolbar_button(self, icon: str, tooltip: str, callback):
        """Add a toolbar button."""
        pass
        
    def get_setting(self, key: str, default=None):
        """Get a setting value."""
        return default
        
    def set_setting(self, key: str, value):
        """Set a setting value."""
        pass


class Plugin(ABC):
    """Base class for plugins."""
    
    def __init__(self):
        self.api: Optional[PluginAPI] = None
        self.info: Optional[PluginInfo] = None
        self.enabled = False
        
    @abstractmethod
    def activate(self, api: PluginAPI):
        """Called when plugin is activated."""
        pass
        
    @abstractmethod
    def deactivate(self):
        """Called when plugin is deactivated."""
        pass
        
    def on_file_open(self, file_path: str):
        """Called when a file is opened."""
        pass
        
    def on_file_save(self, file_path: str):
        """Called when a file is saved."""
        pass
        
    def on_editor_change(self, file_path: str):
        """Called when editor content changes."""
        pass
        
    def on_project_open(self, project_path: str):
        """Called when a project is opened."""
        pass


class PluginManager(QObject):
    """Manages plugins."""
    
    plugin_loaded = pyqtSignal(str)  # plugin_id
    plugin_unloaded = pyqtSignal(str)  # plugin_id
    plugin_error = pyqtSignal(str, str)  # plugin_id, error_message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._plugins: Dict[str, Plugin] = {}
        self._plugin_info: Dict[str, PluginInfo] = {}
        self._plugin_paths: List[Path] = []
        self._api: Optional[PluginAPI] = None
        
    def initialize(self, ide_instance):
        """Initialize with IDE instance."""
        self._api = PluginAPI(ide_instance)
        
        # Load plugin paths
        self._setup_plugin_paths()
        
        # Discover and load plugins
        self._discover_plugins()
        
    def _setup_plugin_paths(self):
        """Setup plugin search paths."""
        # User plugins
        user_plugins = Path.home() / ".cortex" / "plugins"
        user_plugins.mkdir(parents=True, exist_ok=True)
        self._plugin_paths.append(user_plugins)
        
        # System plugins (if installed)
        system_plugins = Path(__file__).parent.parent.parent / "plugins"
        if system_plugins.exists():
            self._plugin_paths.append(system_plugins)
            
    def _discover_plugins(self):
        """Discover available plugins."""
        for plugin_dir in self._plugin_paths:
            if not plugin_dir.exists():
                continue
                
            for item in plugin_dir.iterdir():
                if item.is_dir():
                    manifest = item / "plugin.json"
                    if manifest.exists():
                        try:
                            self._load_plugin_manifest(item)
                        except Exception as e:
                            log.error(f"Failed to load plugin from {item}: {e}")
                            
    def _load_plugin_manifest(self, plugin_dir: Path):
        """Load plugin from manifest file."""
        manifest_path = plugin_dir / "plugin.json"
        
        with open(manifest_path, 'r') as f:
            data = json.load(f)
            
        info = PluginInfo(
            id=data.get('id', plugin_dir.name),
            name=data.get('name', plugin_dir.name),
            version=data.get('version', '2.8.0'),
            author=data.get('author', 'Unknown'),
            description=data.get('description', ''),
            min_api_version=data.get('min_api_version', '1.0.1'),
            dependencies=data.get('dependencies', []),
            entry_point=data.get('entry_point', 'plugin.py')
        )
        
        self._plugin_info[info.id] = info
        
        # Auto-load if enabled
        if data.get('enabled', True):
            self.load_plugin(info.id, plugin_dir)
            
    def load_plugin(self, plugin_id: str, plugin_dir: Path = None) -> bool:
        """Load a plugin by ID."""
        if plugin_id in self._plugins:
            return True  # Already loaded
            
        info = self._plugin_info.get(plugin_id)
        if not info:
            log.error(f"Plugin {plugin_id} not found")
            return False
            
        if not plugin_dir:
            # Find plugin directory
            for p in self._plugin_paths:
                candidate = p / plugin_id
                if candidate.exists():
                    plugin_dir = candidate
                    break
                    
        if not plugin_dir:
            log.error(f"Plugin directory for {plugin_id} not found")
            return False
            
        try:
            # Load plugin module
            entry_file = plugin_dir / info.entry_point
            if not entry_file.exists():
                log.error(f"Plugin entry point not found: {entry_file}")
                return False
                
            # Import plugin module
            spec = importlib.util.spec_from_file_location(
                f"cortex_plugin_{plugin_id}",
                entry_file
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            
            # Find plugin class
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and 
                    issubclass(attr, Plugin) and 
                    attr != Plugin):
                    plugin_class = attr
                    break
                    
            if not plugin_class:
                log.error(f"No plugin class found in {plugin_id}")
                return False
                
            # Instantiate and activate
            plugin = plugin_class()
            plugin.info = info
            plugin.api = self._api
            plugin.activate(self._api)
            
            self._plugins[plugin_id] = plugin
            plugin.enabled = True
            
            log.info(f"Plugin loaded: {info.name} v{info.version}")
            self.plugin_loaded.emit(plugin_id)
            return True
            
        except Exception as e:
            log.error(f"Failed to load plugin {plugin_id}: {e}")
            self.plugin_error.emit(plugin_id, str(e))
            return False
            
    def unload_plugin(self, plugin_id: str) -> bool:
        """Unload a plugin."""
        if plugin_id not in self._plugins:
            return False
            
        try:
            plugin = self._plugins[plugin_id]
            plugin.deactivate()
            plugin.enabled = False
            
            del self._plugins[plugin_id]
            
            log.info(f"Plugin unloaded: {plugin_id}")
            self.plugin_unloaded.emit(plugin_id)
            return True
            
        except Exception as e:
            log.error(f"Failed to unload plugin {plugin_id}: {e}")
            return False
            
    def enable_plugin(self, plugin_id: str) -> bool:
        """Enable a plugin."""
        if plugin_id in self._plugins:
            return True
        return self.load_plugin(plugin_id)
        
    def disable_plugin(self, plugin_id: str) -> bool:
        """Disable a plugin."""
        return self.unload_plugin(plugin_id)
        
    def get_plugin(self, plugin_id: str) -> Optional[Plugin]:
        """Get a loaded plugin."""
        return self._plugins.get(plugin_id)
        
    def get_all_plugins(self) -> Dict[str, PluginInfo]:
        """Get all plugin info."""
        return dict(self._plugin_info)
        
    def get_loaded_plugins(self) -> List[str]:
        """Get list of loaded plugin IDs."""
        return list(self._plugins.keys())
        
    def is_loaded(self, plugin_id: str) -> bool:
        """Check if plugin is loaded."""
        return plugin_id in self._plugins
        
    def broadcast_event(self, event_name: str, *args, **kwargs):
        """Broadcast an event to all plugins."""
        for plugin in self._plugins.values():
            try:
                handler = getattr(plugin, f"on_{event_name}", None)
                if handler:
                    handler(*args, **kwargs)
            except Exception as e:
                log.error(f"Plugin {plugin.info.id} event handler error: {e}")


# Example plugin implementation
class ExamplePlugin(Plugin):
    """Example plugin demonstrating the plugin API."""
    
    def activate(self, api: PluginAPI):
        log.info("ExamplePlugin activated!")
        
    def deactivate(self):
        log.info("ExamplePlugin deactivated!")
        
    def on_file_open(self, file_path: str):
        log.info(f"File opened: {file_path}")
