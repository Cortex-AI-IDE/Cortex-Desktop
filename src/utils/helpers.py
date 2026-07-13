"""
Helper utilities for Cortex AI IDE
"""
import os
from pathlib import Path


LANGUAGE_MAP = {
    # ============================================
    # Python
    # ============================================
    ".py":      "python",
    ".pyw":     "python",
    ".pyi":     "python",
    
    # ============================================
    # Web - HTML, CSS, Preprocessors
    # ============================================
    ".html":    "html",       # auto handles embedded JS + CSS
    ".htm":     "html",
    ".css":     "css",
    ".scss":    "scss",
    ".sass":    "scss",
    ".less":    "less",
    
    # ============================================
    # JavaScript / TypeScript
    # ============================================
    ".js":      "javascript",
    ".jsx":     "javascript",  # React JSX â€” Monaco colors tags
    ".ts":      "typescript",
    ".tsx":     "typescript",  # React TSX
    ".mjs":     "javascript",
    ".cjs":     "javascript",
    
    # ============================================
    # Frameworks & Template Engines
    # (html mode covers template syntax well enough)
    # ============================================
    ".vue":     "html",        # Vue SFC
    ".svelte":  "html",        # Svelte
    ".astro":   "html",        # Astro
    ".njk":     "html",        # Nunjucks
    ".jinja":   "html",        # Jinja2
    ".jinja2":  "html",        # Jinja2 / Django templates
    ".twig":    "html",        # Twig (PHP)
    ".blade":   "html",        # Laravel Blade
    ".erb":     "html",        # Ruby ERB
    
    # ============================================
    # Data / Config
    # ============================================
    ".json":    "json",
    ".jsonc":   "json",
    ".json5":   "json",
    ".yaml":    "yaml",
    ".yml":     "yaml",
    ".toml":    "ini",
    ".env":     "ini",
    ".ini":     "ini",
    ".cfg":     "ini",
    
    # ============================================
    # Markup / Docs
    # ============================================
    ".md":      "markdown",
    ".mdx":     "markdown",
    ".rst":     "markdown",
    ".xml":     "xml",
    ".svg":     "xml",
    ".xaml":    "xml",
    
    # ============================================
    # Systems Programming
    # ============================================
    ".go":      "go",
    ".rs":      "rust",
    ".rb":      "ruby",
    ".php":     "php",
    ".java":    "java",
    ".cs":      "csharp",
    ".cpp":     "cpp",
    ".cc":      "cpp",
    ".cxx":     "cpp",
    ".c":       "c",
    ".h":       "cpp",
    ".hpp":     "cpp",
    ".swift":   "swift",
    ".kt":      "kotlin",
    ".dart":    "dart",
    ".r":       "r",
    
    # ============================================
    # Shell / DevOps
    # ============================================
    ".sh":      "shell",
    ".bash":    "shell",
    ".zsh":     "shell",
    ".fish":    "shell",
    ".ps1":     "powershell",
    ".bat":     "batch",
    ".cmd":     "batch",
    
    # ============================================
    # Database / Query
    # ============================================
    ".sql":     "sql",
    ".graphql": "graphql",
    ".gql":     "graphql",
    
    # ============================================
    # Other
    # ============================================
    ".tf":      "hcl",         # Terraform
    ".proto":   "proto",       # Protobuf
    ".tex":     "latex",       # LaTeX
    ".txt":     "plaintext",
    ".log":     "plaintext",
}

FILE_ICONS = {
    ".py":   "ðŸ",
    ".js":   "ðŸ“œ",
    ".ts":   "ðŸ“˜",
    ".jsx":  "âš›ï¸",
    ".tsx":  "âš›ï¸",
    ".html": "ðŸŒ",
    ".css":  "ðŸŽ¨",
    ".json": "ðŸ“‹",
    ".md":   "ðŸ“",
    ".yml":  "âš™ï¸",
    ".yaml": "âš™ï¸",
    ".txt":  "ðŸ“„",
    ".env":  "ðŸ”‘",
    ".git":  "ðŸ”€",
    ".vue":  "ðŸ’š",
    ".svelte": "ðŸ”¶",
    ".rs":   "ðŸ¦€",
    ".go":   "ðŸ¹",
    ".java": "â˜•",
    ".php":  "ðŸ˜",
    ".rb":   "ðŸ’Ž",
    ".sql":  "ðŸ—ƒï¸",
    "dir":   "ðŸ“",
}


def detect_language(filepath: str) -> str:
    """Detect Monaco language mode from file extension."""
    name = os.path.basename(filepath).lower()
    
    # Special filenames (no extension)
    if name == "dockerfile":
        return "dockerfile"
    if name == "makefile":
        return "makefile"
    if name == "vagrantfile":
        return "ruby"
    if name == "gemfile":
        return "ruby"
    if name == "rakefile":
        return "ruby"
    if name.startswith("readme"):
        return "markdown"
    if name.startswith("license"):
        return "plaintext"
    if name.startswith(".env"):
        return "ini"
    
    ext = Path(filepath).suffix.lower()
    return LANGUAGE_MAP.get(ext, "plaintext")


def file_icon(filepath: str, is_dir: bool = False) -> str:
    if is_dir:
        return FILE_ICONS["dir"]
    ext = Path(filepath).suffix.lower()
    return FILE_ICONS.get(ext, "ðŸ“„")


def human_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def shorten_path(path: str, max_len: int = 50) -> str:
    p = Path(path)
    s = str(p)
    if len(s) <= max_len:
        return s
    # Bug history: this literal was corrupted to "â€¦/" (UTF-8 ellipsis
    # bytes E2 80 A6 mis-decoded as Windows-1252/cp1252) — the status bar
    # showed garbled "â€¦/cortex_desktop/.env.example" instead of a clean
    # "…/cortex_desktop/.env.example".
    return "…/" + "/".join(p.parts[-2:])
