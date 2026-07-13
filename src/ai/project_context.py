"""
ProjectContext — Background project scanning for instant AI awareness.
Runs when a project folder is opened, not when the user sends a message.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from PyQt6.QtCore import QThread, pyqtSignal
from src.utils.logger import get_logger

log = get_logger("project_context")

# Files to read for framework/project type detection
KEY_CONFIG_FILES = [
    "package.json",       "pyproject.toml",     "setup.py",
    "setup.cfg",          "requirements.txt",    "Cargo.toml",
    "go.mod",             "pubspec.yaml",         "build.gradle",
    "pom.xml",            "composer.json",        "Gemfile",
    "mix.exs",            "*.csproj",             "Makefile",
    "Dockerfile",         "docker-compose.yml",   ".env.example",
    "tsconfig.json",      "vite.config.ts",       "vite.config.js",
    "next.config.js",     "nuxt.config.ts",       "angular.json",
    "README.md",          "README.rst",           "CONTRIBUTING.md",
]

# Directories to completely skip during scan
SKIP_DIRS = {
    'node_modules', '.venv', 'venv', 'env', '__pycache__', '.git',
    '.svn', '.hg', 'dist', 'build', 'target', '.cargo', '.gradle',
    'Pods', '.dart_tool', '.pub-cache', '.next', '.nuxt', '.turbo',
    'coverage', '.mypy_cache', '.pytest_cache', '.tox', 'vendor',
    '.eggs', '.idea', 'DerivedData', '.stack-work', '_build',
}

# Extensions that signal source code (prioritized in scan)
SOURCE_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.vue', '.svelte',
    '.dart', '.go', '.rs', '.java', '.kt', '.swift', '.cs',
    '.cpp', '.c', '.h', '.rb', '.php', '.ex', '.exs',
    '.html', '.css', '.scss', '.sass',
}


@dataclass
class ProjectContext:
    """Cached project intelligence — built once, used in every AI call."""
    
    root_path: str = ""
    project_name: str = ""
    project_type: str = "unknown"        # web-frontend, python-backend, mobile, etc.
    primary_language: str = "unknown"
    frameworks: List[str] = field(default_factory=list)
    entry_points: List[str] = field(default_factory=list)  # main.py, index.js, etc.
    test_dirs: List[str] = field(default_factory=list)
    config_files: Dict[str, str] = field(default_factory=dict)  # filename → content
    file_tree: str = ""                  # formatted for LLM
    readme_content: str = ""
    source_file_count: int = 0
    total_file_count: int = 0
    key_insights: List[str] = field(default_factory=list)  # "Uses React 18", etc.
    
    # Virtual environment info
    has_venv: bool = False
    venv_path: str = ""
    venv_python_version: str = ""
    
    # Package info
    dependencies: List[str] = field(default_factory=list)  # top-level deps only
    dev_dependencies: List[str] = field(default_factory=list)
    
    # State
    is_ready: bool = False
    build_time_ms: float = 0.0
    error: str = ""

    def to_system_prompt_block(self) -> str:
        """
        Format project context for injection into AI system prompt.
        Concise enough to fit in ~2000 tokens, informative enough to be useful.
        """
        if not self.is_ready:
            return f"## PROJECT\nRoot: {self.root_path}\n(Analysis in progress...)"

        lines = [
            "## PROJECT CONTEXT",
            f"Root: {self.root_path}",
            f"Name: {self.project_name}",
            f"Type: {self.project_type}",
            f"Language: {self.primary_language}",
        ]

        if self.frameworks:
            lines.append(f"Frameworks: {', '.join(self.frameworks)}")

        if self.dependencies:
            deps_preview = ', '.join(self.dependencies[:15])
            if len(self.dependencies) > 15:
                deps_preview += f" ... (+{len(self.dependencies)-15} more)"
            lines.append(f"Dependencies: {deps_preview}")

        if self.has_venv:
            lines.append(f"Virtual env: {self.venv_path} ({self.venv_python_version})")

        if self.entry_points:
            lines.append(f"Entry points: {', '.join(self.entry_points[:5])}")

        if self.test_dirs:
            lines.append(f"Test directories: {', '.join(self.test_dirs)}")

        lines.append(f"Source files: {self.source_file_count} ({self.total_file_count} total)")

        if self.key_insights:
            lines.append("\nKey findings:")
            for insight in self.key_insights[:8]:
                lines.append(f"  • {insight}")

        if self.file_tree:
            lines.append("\nProject structure:")
            lines.append(self.file_tree)

        if self.readme_content:
            # Truncate README to first 500 chars
            readme_preview = self.readme_content[:500].strip()
            if len(self.readme_content) > 500:
                readme_preview += "\n..."
            lines.append(f"\nREADME excerpt:\n{readme_preview}")

        # Config file summaries
        for fname, content in list(self.config_files.items())[:3]:
            preview = content[:300].strip()
            lines.append(f"\n{fname}:\n```\n{preview}\n```")

        return "\n".join(lines)

    def to_warmup_summary(self) -> str:
        """
        Generate a user-friendly project summary to display in chat
        when the user first opens a project or asks "what is this?".
        """
        if not self.is_ready:
            return "🔍 Still scanning project..."

        lines = [
            f"## 📁 {self.project_name}",
            "",
        ]

        # Project type badge
        type_emoji = {
            "python-backend": "🐍",
            "web-frontend": "🌐",
            "fullstack": "⚡",
            "mobile-flutter": "📱",
            "mobile-react-native": "📱",
            "rust": "🦀",
            "go": "🐹",
            "java-android": "🤖",
            "nodejs": "💚",
        }.get(self.project_type, "💻")

        lines.append(f"{type_emoji} **{self.project_type.replace('-', ' ').title()}** project")

        if self.frameworks:
            lines.append(f"🔧 Frameworks: {', '.join(self.frameworks)}")

        lines.append(f"📊 {self.source_file_count} source files across {self.total_file_count} total files")

        if self.has_venv:
            lines.append(f"🐍 Virtual environment detected: `{self.venv_path}` ({self.venv_python_version})")

        if self.entry_points:
            lines.append(f"🚀 Entry points: {', '.join(f'`{e}`' for e in self.entry_points[:3])}")

        if self.key_insights:
            lines.append("\n**What I found:**")
            for insight in self.key_insights:
                lines.append(f"- {insight}")

        if self.file_tree:
            lines.append(f"\n**Project structure:**\n```\n{self.file_tree}\n```")

        lines.append(f"\n*Scanned in {self.build_time_ms:.0f}ms. Ready to help.*")

        return "\n".join(lines)


class ProjectContextBuilder(QThread):
    """
    Background thread that scans a project directory and builds
    a ProjectContext object. Runs when a project is opened,
    not when the user sends a message.
    """
    
    context_ready = pyqtSignal(object)   # ProjectContext
    progress = pyqtSignal(str)           # status message for UI

    def __init__(self, root_path: str, parent=None):
        super().__init__(parent)
        self.root_path = root_path
        self._context = ProjectContext(root_path=root_path)

    def run(self):
        import time
        start = time.time()
        
        try:
            self.progress.emit("Scanning project structure...")
            self._scan()
            
            self.progress.emit("Detecting frameworks...")
            self._detect_framework()
            
            self.progress.emit("Reading key files...")
            self._read_key_files()
            
            self.progress.emit("Building file tree...")
            self._build_file_tree()
            
            self.progress.emit("Analyzing dependencies...")
            self._analyze_dependencies()
            
            self.progress.emit("Detecting virtual environments...")
            self._detect_venv()
            
            self._context.build_time_ms = (time.time() - start) * 1000
            self._context.is_ready = True
            
            log.info(f"Project context built in {self._context.build_time_ms:.0f}ms "
                     f"for {self._context.root_path}")
            
        except Exception as e:
            log.error(f"ProjectContextBuilder error: {e}")
            self._context.error = str(e)
            self._context.is_ready = True  # still emit so agent isn't stuck
        
        self.context_ready.emit(self._context)

    def _scan(self):
        """Quick pass to count files and detect project type - OPTIMIZED."""
        root = Path(self.root_path)
        self._context.project_name = root.name
        
        source_count = 0
        total_count = 0
        
        # Performance optimization: limit recursion depth to 3 levels for speed
        # Most important files (package.json, requirements.txt, etc.) are at top levels
        for level in range(3):
            try:
                for fpath in root.glob("*" if level == 0 else "*/" * level + "*"):
                    # Skip blocked directories
                    parts = set(fpath.parts)
                    if parts & SKIP_DIRS:
                        continue
                    if fpath.is_file():
                        total_count += 1
                        if fpath.suffix in SOURCE_EXTENSIONS:
                            source_count += 1
            except Exception:
                continue
        
        self._context.source_file_count = source_count
        self._context.total_file_count = total_count
        self._context.scan_depth = 3  # Mark as partial scan

    def _detect_framework(self):
        """Detect project type and frameworks from config files."""
        root = Path(self.root_path)
        insights = []

        def exists(*names):
            return any((root / n).exists() for n in names)

        # Python
        if exists("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"):
            self._context.primary_language = "Python"
            
            # Django
            if exists("manage.py"):
                self._context.project_type = "python-backend"
                self._context.frameworks.append("Django")
                insights.append("Django web application (manage.py found)")
                self._context.entry_points = ["manage.py"]
            # FastAPI / Flask
            elif exists("main.py", "app.py", "wsgi.py"):
                self._context.project_type = "python-backend"
                entry = "main.py" if (root / "main.py").exists() else "app.py"
                self._context.entry_points = [entry]
                # Peek at requirements
                req_path = root / "requirements.txt"
                if req_path.exists():
                    req = req_path.read_text(errors='replace').lower()
                    if 'fastapi' in req:
                        self._context.frameworks.append("FastAPI")
                        insights.append("FastAPI backend")
                    elif 'flask' in req:
                        self._context.frameworks.append("Flask")
                        insights.append("Flask web app")
                    elif 'django' in req:
                        self._context.frameworks.append("Django")
            else:
                self._context.project_type = "python-library"

        # Node / JavaScript / TypeScript
        elif exists("package.json"):
            pkg_path = root / "package.json"
            try:
                pkg = json.loads(pkg_path.read_text(errors='replace'))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                dep_keys = set(deps.keys())
                
                self._context.primary_language = "TypeScript" if exists("tsconfig.json") else "JavaScript"
                
                if "next" in dep_keys:
                    self._context.project_type = "web-frontend"
                    self._context.frameworks.append("Next.js")
                    insights.append("Next.js React application")
                elif "react" in dep_keys:
                    self._context.project_type = "web-frontend"
                    self._context.frameworks.append("React")
                    insights.append("React application")
                elif "vue" in dep_keys:
                    self._context.project_type = "web-frontend"
                    self._context.frameworks.append("Vue.js")
                elif "svelte" in dep_keys or "@sveltejs/kit" in dep_keys:
                    self._context.project_type = "web-frontend"
                    self._context.frameworks.append("SvelteKit" if "@sveltejs/kit" in dep_keys else "Svelte")
                elif "express" in dep_keys or "fastify" in dep_keys:
                    self._context.project_type = "nodejs-backend"
                    fw = "Express" if "express" in dep_keys else "Fastify"
                    self._context.frameworks.append(fw)
                else:
                    self._context.project_type = "nodejs"
                
                # Check for entry points
                main = pkg.get("main", "")
                scripts = pkg.get("scripts", {})
                if main:
                    self._context.entry_points.append(main)
                if "start" in scripts:
                    insights.append(f"Start command: `npm run start` → {scripts['start'][:60]}")
                if "dev" in scripts:
                    insights.append(f"Dev command: `npm run dev` → {scripts['dev'][:60]}")
            except Exception:
                self._context.project_type = "nodejs"

        # Flutter / Dart
        elif exists("pubspec.yaml"):
            self._context.primary_language = "Dart"
            self._context.project_type = "mobile-flutter"
            self._context.frameworks.append("Flutter")
            self._context.entry_points = ["lib/main.dart"]
            insights.append("Flutter mobile application")

        # Rust
        elif exists("Cargo.toml"):
            self._context.primary_language = "Rust"
            self._context.project_type = "rust"
            self._context.frameworks.append("Cargo")
            insights.append("Rust project")

        # Go
        elif exists("go.mod"):
            self._context.primary_language = "Go"
            self._context.project_type = "go"
            insights.append("Go module")

        # Java / Android
        elif exists("build.gradle", "gradlew", "pom.xml"):
            self._context.primary_language = "Java/Kotlin"
            self._context.project_type = "java-android"
            insights.append("Android/Java project")

        # C# / .NET
        elif any(root.glob("*.csproj")) or any(root.glob("*.sln")):
            self._context.primary_language = "C#"
            self._context.project_type = "dotnet"

        # Ruby on Rails
        elif exists("Gemfile"):
            self._context.primary_language = "Ruby"
            if exists("config/routes.rb"):
                self._context.project_type = "ruby-rails"
                self._context.frameworks.append("Rails")

        # Detect test directories
        for test_name in ["tests", "test", "spec", "__tests__", "e2e"]:
            if (root / test_name).is_dir():
                self._context.test_dirs.append(test_name)
                insights.append(f"Tests in `{test_name}/`")

        # Detect Docker
        if exists("Dockerfile", "docker-compose.yml", "docker-compose.yaml"):
            insights.append("Docker configuration present")

        # Detect CI
        ci_paths = [".github/workflows", ".gitlab-ci.yml", ".circleci", "Jenkinsfile"]
        for ci in ci_paths:
            if (root / ci).exists():
                insights.append(f"CI/CD: {ci}")
                break

        self._context.key_insights = insights

    def _read_key_files(self):
        """Read content of key config/documentation files — LAZY: only read what's essential."""
        root = Path(self.root_path)
        
        # Only read the most essential config files (not all 12)
        priority_files = [
            "package.json", "pyproject.toml", "requirements.txt",
            "Cargo.toml", "go.mod", "pubspec.yaml",
        ]
        
        for fname in priority_files:
            fpath = root / fname
            if fpath.exists() and fpath.is_file():
                try:
                    content = fpath.read_text(encoding='utf-8', errors='replace')
                    # Truncate config files to 300 chars (was 500)
                    self._context.config_files[fname] = content[:300]
                except Exception:
                    pass
        
        # README: only read first 800 chars (was 2000) — AI can read more if needed
        for readme_name in ["README.md", "README.rst", "README.txt"]:
            readme_path = root / readme_name
            if readme_path.exists() and readme_path.is_file():
                try:
                    content = readme_path.read_text(encoding='utf-8', errors='replace')
                    self._context.readme_content = content[:800]
                except Exception:
                    pass
                break

    def _build_file_tree(self):
        """Build a compact directory tree for the LLM."""
        root = Path(self.root_path)
        lines = []
        
        def tree(path: Path, prefix: str = "", depth: int = 0):
            if depth > 3:
                return
            
            try:
                entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
            except PermissionError:
                return
            
            # Filter entries
            visible = []
            for e in entries:
                name = e.name
                if name.startswith('.') and name not in ('.env.example', '.gitignore'):
                    continue
                if e.is_dir() and name in SKIP_DIRS:
                    continue
                visible.append(e)
            
            # Limit to 20 entries per directory
            if len(visible) > 20:
                visible = visible[:20]
                truncated = True
            else:
                truncated = False
            
            for i, entry in enumerate(visible):
                is_last = (i == len(visible) - 1) and not truncated
                connector = "└── " if is_last else "├── "
                
                if entry.is_dir():
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    extension = "    " if is_last else "│   "
                    tree(entry, prefix + extension, depth + 1)
                else:
                    size = entry.stat().st_size
                    size_str = f" ({size//1024}KB)" if size > 1024 else ""
                    lines.append(f"{prefix}{connector}{entry.name}{size_str}")
            
            if truncated:
                lines.append(f"{prefix}└── ... (more files)")
        
        lines.append(root.name + "/")
        tree(root)
        
        # Keep tree compact — max 60 lines
        if len(lines) > 60:
            lines = lines[:60]
            lines.append("... (tree truncated)")
        
        self._context.file_tree = "\n".join(lines)

    def _analyze_dependencies(self):
        """Extract top-level dependency names."""
        root = Path(self.root_path)
        
        # Python requirements.txt
        req_path = root / "requirements.txt"
        if req_path.exists():
            try:
                for line in req_path.read_text(errors='replace').splitlines():
                    line = line.strip()
                    if line and not line.startswith('#') and not line.startswith('-'):
                        pkg = line.split('>=')[0].split('==')[0].split('[')[0].strip()
                        if pkg:
                            self._context.dependencies.append(pkg)
                self._context.dependencies = self._context.dependencies[:30]
            except Exception:
                pass
        
        # package.json
        pkg_path = root / "package.json"
        if pkg_path.exists():
            try:
                pkg = json.loads(pkg_path.read_text(errors='replace'))
                self._context.dependencies = list(pkg.get("dependencies", {}).keys())[:20]
                self._context.dev_dependencies = list(pkg.get("devDependencies", {}).keys())[:10]
            except Exception:
                pass

    def _detect_venv(self):
        """Detect Python virtual environments."""
        root = Path(self.root_path)
        
        for venv_name in ['venv', '.venv', 'env', 'ENV', 'virtualenv']:
            venv_path = root / venv_name
            if venv_path.is_dir():
                # Confirm it's actually a venv
                activate = (
                    (venv_path / 'bin' / 'activate').exists() or
                    (venv_path / 'Scripts' / 'activate').exists()
                )
                if activate:
                    self._context.has_venv = True
                    self._context.venv_path = str(venv_path.relative_to(root))
                    
                    # Try to read Python version
                    cfg = venv_path / 'pyvenv.cfg'
                    if cfg.exists():
                        for line in cfg.read_text(errors='replace').splitlines():
                            if line.startswith('version'):
                                self._context.venv_python_version = line.split('=')[1].strip()
                    break


# ── Singleton cache ──────────────────────────────────────────────────────────

_project_contexts: Dict[str, ProjectContext] = {}
_builders: Dict[str, ProjectContextBuilder] = {}
_cache_timestamps: Dict[str, float] = {}  # Performance: track when context was built


def get_project_context(root_path: str) -> Optional[ProjectContext]:
    """Get cached project context (returns None if not ready yet)."""
    ctx = _project_contexts.get(root_path)
    return ctx if (ctx and ctx.is_ready) else None


def build_project_context(root_path: str, on_ready=None, force_rebuild=False) -> ProjectContextBuilder:
    """
    Start building project context in background.
    Call this when a project folder is opened.
    
    on_ready: callback(ProjectContext) called when scan completes
    force_rebuild: if True, rebuild even if recently cached
    """
    import time
    
    # Performance optimization: skip rebuild if cached within last 5 minutes
    current_time = time.time()
    if not force_rebuild and root_path in _cache_timestamps:
        age = current_time - _cache_timestamps[root_path]
        if age < 300:  # 5 minutes
            log.info(f"Using cached project context ({age:.0f}s old)")
            # Return existing context immediately
            existing = _project_contexts.get(root_path)
            if existing and existing.is_ready and on_ready:
                on_ready(existing)
            return None
    
    # Cancel existing builder for this path
    if root_path in _builders:
        old = _builders[root_path]
        if old.isRunning():
            old.terminate()
    
    builder = ProjectContextBuilder(root_path)
    
    def _on_context_ready(ctx: ProjectContext):
        _project_contexts[root_path] = ctx
        _cache_timestamps[root_path] = time.time()  # Cache timestamp
        log.info(f"Project context cached for: {root_path}")
        if on_ready:
            on_ready(ctx)
    
    builder.context_ready.connect(_on_context_ready)
    _builders[root_path] = builder
    builder.start()
    
    return builder
