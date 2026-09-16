"""File types Cortex opens from the operating system, and how each
distribution registers them.

FILE_TYPES below is the only list. Every distribution is generated from it:

- Inno Setup installer (Cortex_Setup_*.exe): build.ps1 runs
  `build_tools/file_assoc/gen_file_assoc.py iss`, which writes
  build_tools/file_assoc/cortex_file_assoc.iss; cortex_setup.iss includes it.
- MSIX (Microsoft Store): build_msix.ps1 fills {FILE_TYPE_ASSOCIATIONS} in
  AppxManifest.template.xml from `gen_file_assoc.py msix`.
- Linux .deb / .rpm: `gen_file_assoc.py linux` writes the shared-mime-info
  package and the MimeType= line of the .desktop file.
- A Windows build run without the installer: `Cortex.exe --register-file-types`
  writes the installer's registry entries for the current user
  (`--unregister-file-types` removes them).

Registering never takes a file type over. It adds Cortex to "Open with" and to
Settings > Default apps; the user makes Cortex the default there, or with
"Open with > Always" (Windows does not let an installer set the default).
To support another extension, add one line to FILE_TYPES and rebuild.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

APP_NAME = "Cortex AI IDE"
EXE_NAME = "Cortex.exe"
PROGID_PREFIX = "Cortex"
CAPABILITIES_KEY = r"Software\CortexAIIDE\Capabilities"
CONTEXT_MENU_KEY = r"Software\Classes\*\shell\CortexIDE"
CONTEXT_MENU_LABEL = "Open with Cortex IDE"
LINUX_DESKTOP_ID = "cortex-ide.desktop"

# Group key -> the type name Windows shows ("Type" column) and the MSIX
# association's display name.
GROUPS: Dict[str, str] = {
    "python": "Python source file",
    "javascript": "JavaScript source file",
    "typescript": "TypeScript source file",
    "web": "Web page or component",
    "style": "Style sheet",
    "markup": "Markdown or reStructuredText document",
    "text": "Text document",
    "data": "Data file",
    "config": "Configuration file",
    "shell": "Shell script",
    "c": "C/C++ source file",
    "jvm": "Java, Kotlin or Scala source file",
    "dotnet": "C# source file",
    "source": "Source file",
    "build": "Build or project file",
}


@dataclass(frozen=True)
class FileType:
    ext: str                     # ".py"
    group: str                   # key of GROUPS
    mimes: Tuple[str, ...] = ()  # Linux MIME types, main one first; empty = not offered on Linux
    msix: bool = True            # False: Windows reserves the type (script launchers), skip in MSIX
    define_mime: bool = False    # True: not in every distro's MIME database, ship a definition


def _t(ext: str, group: str, *mimes: str, msix: bool = True, define: bool = False) -> FileType:
    return FileType(ext, group, tuple(mimes), msix, define)


FILE_TYPES: List[FileType] = [
    # Python
    _t(".py", "python", "text/x-python3", "text/x-python"),
    _t(".pyw", "python", "text/x-python3"),
    _t(".pyi", "python", "text/x-python3"),
    # JavaScript / TypeScript
    _t(".js", "javascript", "text/javascript", "application/javascript"),
    _t(".mjs", "javascript", "text/javascript"),
    _t(".cjs", "javascript", "text/javascript"),
    _t(".jsx", "javascript", "text/jsx", define=True),
    _t(".ts", "typescript", "text/x-typescript", define=True),
    _t(".mts", "typescript", "text/x-typescript", define=True),
    _t(".cts", "typescript", "text/x-typescript", define=True),
    _t(".tsx", "typescript", "text/x-tsx", define=True),
    # Web
    _t(".html", "web", "text/html"),
    _t(".htm", "web", "text/html"),
    _t(".vue", "web", "text/x-vue", define=True),
    _t(".svelte", "web", "text/x-svelte", define=True),
    _t(".css", "style", "text/css"),
    _t(".scss", "style", "text/x-scss"),
    _t(".sass", "style", "text/x-sass"),
    _t(".less", "style", "text/x-less", define=True),
    # Documents
    _t(".md", "markup", "text/markdown"),
    _t(".markdown", "markup", "text/markdown"),
    _t(".mdx", "markup", "text/x-mdx", define=True),
    _t(".rst", "markup", "text/x-rst"),
    _t(".txt", "text", "text/plain"),
    _t(".log", "text", "text/x-log"),
    # Data
    _t(".json", "data", "application/json"),
    _t(".jsonc", "data", "application/x-jsonc", define=True),
    _t(".xml", "data", "application/xml", "text/xml"),
    _t(".yaml", "data", "application/yaml", "application/x-yaml"),
    _t(".yml", "data", "application/yaml", "application/x-yaml"),
    _t(".csv", "data", "text/csv"),
    _t(".sql", "data", "application/sql", "text/x-sql"),
    # Configuration
    _t(".toml", "config", "application/toml"),
    _t(".ini", "config", "text/plain"),
    _t(".cfg", "config", "text/plain"),
    _t(".conf", "config", "text/plain"),
    _t(".env", "config", "text/plain"),
    # Shell
    _t(".sh", "shell", "application/x-shellscript"),
    _t(".bash", "shell", "application/x-shellscript"),
    _t(".zsh", "shell", "application/x-shellscript"),
    _t(".bat", "shell", msix=False),
    _t(".cmd", "shell", msix=False),
    _t(".ps1", "shell", msix=False),
    # C family
    _t(".c", "c", "text/x-csrc"),
    _t(".h", "c", "text/x-chdr"),
    _t(".cpp", "c", "text/x-c++src"),
    _t(".cc", "c", "text/x-c++src"),
    _t(".cxx", "c", "text/x-c++src"),
    _t(".hpp", "c", "text/x-c++hdr"),
    _t(".hh", "c", "text/x-c++hdr"),
    # JVM
    _t(".java", "jvm", "text/x-java"),
    _t(".kt", "jvm", "text/x-kotlin"),
    _t(".kts", "jvm", "text/x-kotlin"),
    _t(".scala", "jvm", "text/x-scala"),
    _t(".gradle", "jvm", "text/x-gradle", define=True),
    # .NET
    _t(".cs", "dotnet", "text/x-csharp"),
    # Other languages
    _t(".go", "source", "text/x-go"),
    _t(".rs", "source", "text/rust"),
    _t(".php", "source", "application/x-php"),
    _t(".rb", "source", "application/x-ruby"),
    _t(".swift", "source", "text/x-swift"),
    _t(".dart", "source", "text/x-dart", define=True),
    _t(".lua", "source", "text/x-lua"),
    _t(".pl", "source", "application/x-perl"),
    # ── More source languages ──
    _t(".ex", "source", "text/x-elixir", define=True),
    _t(".exs", "source", "text/x-elixir", define=True),
    _t(".erl", "source", "text/x-erlang"),
    _t(".hrl", "source", "text/x-erlang"),
    _t(".hs", "source", "text/x-haskell"),
    _t(".elm", "source", "text/x-elm", define=True),
    _t(".clj", "source", "text/x-clojure", define=True),
    _t(".cljs", "source", "text/x-clojure", define=True),
    _t(".edn", "source", "text/x-clojure", define=True),
    _t(".scm", "source", "text/x-scheme"),
    _t(".rkt", "source", "text/x-scheme"),
    _t(".ml", "source", "text/x-ocaml"),
    _t(".mli", "source", "text/x-ocaml"),
    _t(".fs", "dotnet", "text/x-fsharp", define=True),
    _t(".fsx", "dotnet", "text/x-fsharp", define=True),
    _t(".vb", "dotnet", "text/x-vb", msix=False),
    _t(".jl", "source", "text/x-julia", define=True),
    _t(".groovy", "jvm", "text/x-groovy"),
    _t(".zig", "source", "text/x-zig", define=True),
    _t(".nim", "source", "text/x-nim", define=True),
    _t(".cr", "source", "text/x-crystal", define=True),
    _t(".d", "source", "text/x-dsrc"),
    _t(".r", "source", "text/x-r"),
    _t(".m", "c", "text/x-objcsrc"),
    _t(".mm", "c", "text/x-objc++src"),
    _t(".ino", "c", "text/x-arduino", define=True),
    _t(".cu", "c", "text/x-cuda", define=True),
    _t(".cuh", "c", "text/x-cuda", define=True),
    _t(".hxx", "c", "text/x-c++hdr"),
    _t(".asm", "source", "text/x-asm"),
    _t(".s", "source", "text/x-asm"),
    _t(".pas", "source", "text/x-pascal"),
    _t(".f90", "source", "text/x-fortran"),
    _t(".sol", "source", "text/x-solidity", define=True),
    _t(".pyx", "python", "text/x-cython", define=True),
    _t(".coffee", "javascript", "text/coffeescript"),
    _t(".astro", "web", "text/x-astro", define=True),
    _t(".hbs", "web", "text/x-handlebars", define=True),
    _t(".ejs", "web", "text/x-ejs", define=True),
    _t(".pug", "web", "text/x-pug", define=True),
    _t(".jinja", "web", "text/x-jinja", define=True),
    _t(".j2", "web", "text/x-jinja", define=True),
    _t(".styl", "style", "text/x-stylus", define=True),
    _t(".pcss", "style", "text/x-postcss", define=True),
    _t(".svg", "markup", "image/svg+xml"),
    _t(".xsd", "data", "application/xml"),
    _t(".xsl", "data", "application/xslt+xml"),
    _t(".xslt", "data", "application/xslt+xml"),
    _t(".dtd", "data", "application/xml-dtd"),
    # ── Documents ──
    _t(".tex", "markup", "text/x-tex"),
    _t(".bib", "markup", "text/x-bibtex"),
    _t(".adoc", "markup", "text/x-asciidoc", define=True),
    _t(".org", "markup", "text/x-org", define=True),
    _t(".diff", "text", "text/x-patch"),
    _t(".patch", "text", "text/x-patch"),
    # ── Data ──
    _t(".tsv", "data", "text/tab-separated-values"),
    _t(".json5", "data", "application/json5", define=True),
    _t(".jsonl", "data", "application/x-ndjson", define=True),
    _t(".ndjson", "data", "application/x-ndjson", define=True),
    _t(".ipynb", "data", "application/x-ipynb+json", define=True),
    _t(".webmanifest", "data", "application/manifest+json", define=True),
    _t(".proto", "data", "text/x-protobuf", define=True),
    _t(".graphql", "data", "application/graphql", define=True),
    _t(".gql", "data", "application/graphql", define=True),
    _t(".prisma", "data", "text/x-prisma", define=True),
    # ── Configuration ──
    _t(".spec", "build", "text/x-rpm-spec"),
    _t(".lock", "config", "text/plain"),
    _t(".properties", "config", "text/x-java-properties", define=True),
    _t(".editorconfig", "config", "text/plain"),
    _t(".gitignore", "config", "text/plain"),
    _t(".gitattributes", "config", "text/plain"),
    _t(".gitmodules", "config", "text/plain"),
    _t(".dockerignore", "config", "text/plain"),
    _t(".npmrc", "config", "text/plain"),
    _t(".babelrc", "config", "application/json"),
    _t(".eslintrc", "config", "application/json"),
    _t(".prettierrc", "config", "application/json"),
    _t(".service", "config", "text/plain"),
    _t(".tf", "config", "text/x-terraform", define=True),
    _t(".tfvars", "config", "text/x-terraform", define=True),
    _t(".hcl", "config", "text/x-terraform", define=True),
    _t(".nix", "config", "text/x-nix", define=True),
    _t(".plist", "config", "application/x-plist", define=True),
    # ── Build and project files ──
    _t(".cmake", "build", "text/x-cmake"),
    _t(".mk", "build", "text/x-makefile"),
    _t(".bzl", "build", "text/x-bazel", define=True),
    _t(".csproj", "build"),
    _t(".vbproj", "build"),
    _t(".vcxproj", "build"),
    _t(".props", "build"),
    _t(".targets", "build"),
    _t(".sln", "build"),
    _t(".xaml", "build", "application/xaml+xml", define=True),
    # ── Scripts (script-launcher types stay out of the MSIX) ──
    _t(".psm1", "shell", msix=False),
    _t(".psd1", "shell", msix=False),
    _t(".vbs", "shell", msix=False),
    _t(".reg", "shell", msix=False),
    _t(".fish", "shell", "application/x-fishscript", define=True),
    _t(".ksh", "shell", "application/x-shellscript", msix=False),
    _t(".csh", "shell", "application/x-csh", msix=False),
]


def progid(ft: FileType) -> str:
    return f"{PROGID_PREFIX}{ft.ext}"  # "Cortex.py"


# ── Windows registry (installer and --register-file-types) ────────────────
#
# One entry: (subkey, value name, data, uninstall). A value name of None
# creates the key only. uninstall is "key" (delete the key and everything
# under it), "value" (delete only this value, for keys other programs own
# such as .py\OpenWithProgids), "keyifempty", or None (removed with a
# parent entry's "key"). Subkeys are relative to HKCU (per-user install) or
# HKLM (install for all users).
RegEntry = Tuple[str, Optional[str], str, Optional[str]]


def windows_registry_entries(exe: str) -> Dict[str, List[RegEntry]]:
    """Registry entries by installer task: "fileassoc" (Open with, Default
    apps) and "filecontextmenu" (right-click "Open with Cortex IDE" on any
    file). exe is the full path of Cortex.exe."""
    open_cmd = f'"{exe}" "%1"'
    icon = f'"{exe}",0'
    app_key = rf"Software\Classes\Applications\{EXE_NAME}"
    assoc: List[RegEntry] = [
        (app_key, "FriendlyAppName", APP_NAME, "key"),
        (app_key + r"\DefaultIcon", "", icon, None),
        (app_key + r"\shell\open\command", "", open_cmd, None),
        (CAPABILITIES_KEY, "ApplicationName", APP_NAME, "key"),
        (CAPABILITIES_KEY, "ApplicationDescription", "AI coding IDE", None),
        (CAPABILITIES_KEY, "ApplicationIcon", icon, None),
        (CAPABILITIES_KEY.rsplit("\\", 1)[0], None, "", "keyifempty"),
        (r"Software\RegisteredApplications", APP_NAME, CAPABILITIES_KEY, "value"),
    ]
    for ft in FILE_TYPES:
        pid = progid(ft)
        cls = rf"Software\Classes\{pid}"
        assoc += [
            (cls, "", GROUPS[ft.group], "key"),
            (cls + r"\DefaultIcon", "", icon, None),
            (cls + r"\shell\open\command", "", open_cmd, None),
            (rf"Software\Classes\{ft.ext}\OpenWithProgids", pid, "", "value"),
            (app_key + r"\SupportedTypes", ft.ext, "", None),
            (CAPABILITIES_KEY + r"\FileAssociations", ft.ext, pid, None),
        ]
    menu: List[RegEntry] = [
        (CONTEXT_MENU_KEY, "", CONTEXT_MENU_LABEL, "key"),
        (CONTEXT_MENU_KEY, "Icon", icon, None),
        (CONTEXT_MENU_KEY + r"\command", "", open_cmd, None),
    ]
    return {"fileassoc": assoc, "filecontextmenu": menu}


def is_msix_package() -> bool:
    """True when running from the MSIX package, whose file types come from
    the manifest (registry writes there are virtualised and have no effect)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        length = ctypes.c_uint32(0)
        rc = ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None)
        return rc != 15700  # APPMODEL_ERROR_NO_PACKAGE
    except Exception:
        return False


def _notify_shell() -> None:
    try:
        import ctypes
        # SHCNE_ASSOCCHANGED, SHCNF_IDLIST | SHCNF_FLUSH
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None)
    except Exception:
        pass


def _delete_tree(root, subkey: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(root, subkey + "\\" + child)
        winreg.DeleteKey(root, subkey)
    except FileNotFoundError:
        pass


def register_windows_user(exe: str, context_menu: bool = True) -> int:
    """Write the installer's entries under HKCU. Returns the value count."""
    import winreg
    count = 0
    for task, entries in windows_registry_entries(exe).items():
        if task == "filecontextmenu" and not context_menu:
            continue
        for subkey, name, data, _uninstall in entries:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_WRITE) as key:
                if name is not None:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, data)
                    count += 1
    _notify_shell()
    return count


def unregister_windows_user() -> int:
    """Remove what register_windows_user wrote. Returns the entries removed."""
    import winreg
    removed = 0
    hkcu = winreg.HKEY_CURRENT_USER
    for entries in windows_registry_entries(EXE_NAME).values():
        for subkey, name, _data, uninstall in entries:
            try:
                if uninstall == "key":
                    _delete_tree(hkcu, subkey)
                elif uninstall == "value" and name is not None:
                    with winreg.OpenKey(hkcu, subkey, 0, winreg.KEY_WRITE) as key:
                        winreg.DeleteValue(key, name)
                elif uninstall == "keyifempty":
                    winreg.DeleteKey(hkcu, subkey)
                else:
                    continue
                removed += 1
            except OSError:
                pass  # already gone, or not empty
    _notify_shell()
    return removed


def cli(args: List[str]) -> Tuple[int, str]:
    """--register-file-types / --unregister-file-types. Returns (exit code, message)."""
    if sys.platform != "win32":
        return 1, "Linux file types come from the .desktop file and MIME package the .deb/.rpm installs."
    if is_msix_package():
        return 1, "The Microsoft Store version declares its file types in the package; nothing to register."
    if "--unregister-file-types" in args:
        n = unregister_windows_user()
        return 0, f"Removed {n} Cortex file-type entries for this user."
    if not getattr(sys, "frozen", False):
        return 1, "Run this from the built Cortex.exe, so the entries point at it."
    n = register_windows_user(sys.executable, context_menu="--no-context-menu" not in args)
    return 0, (f"Registered Cortex for {len(FILE_TYPES)} file types ({n} entries). "
               "Choose it in Open with > Always, or Settings > Apps > Default apps.")


# ── Generated data for the installers ─────────────────────────────────────

def _iss_str(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def inno_script() -> str:
    """[Tasks] and [Registry] for cortex_setup.iss, rooted at HKA (HKCU for
    'install for me only', HKLM for 'install for all users')."""
    n = len(FILE_TYPES)
    lines = [
        "; GENERATED by build_tools/file_assoc/gen_file_assoc.py from",
        "; src/core/file_associations.py. Edit that list, not this file.",
        "",
        "[Tasks]",
        f'Name: "fileassoc"; Description: "Add Cortex to ""Open with"" for {n} code and text file types"; '
        'GroupDescription: "File associations:"',
        'Name: "filecontextmenu"; Description: "Add ""Open with Cortex IDE"" to the right-click menu of every file"; '
        'GroupDescription: "File associations:"',
        "",
        "[Registry]",
    ]
    flags = {"key": "uninsdeletekey", "value": "uninsdeletevalue", "keyifempty": "uninsdeletekeyifempty"}
    app_key = rf"Software\Classes\Applications\{EXE_NAME}"
    for task, entries in windows_registry_entries(r"{app}\Cortex.exe").items():
        for subkey, name, data, uninstall in entries:
            # Applications\Cortex.exe is also written to HKCU on an "all
            # users" install: a per-user copy of that key (left by an older
            # per-user install, or created by Windows when the user picked
            # Cortex with "Choose another app") overrides HKLM, and one that
            # points at a Cortex.exe that no longer exists makes "Open with"
            # show the generic app icon instead of Cortex's.
            roots = [("HKA", "")]
            if subkey.startswith(app_key):
                roots.append(("HKCU", "Check: IsAdminInstallMode"))
            for root, check in roots:
                parts = [f"Root: {root}", f"Subkey: {_iss_str(subkey)}"]
                if name is not None:
                    parts += ["ValueType: string", f"ValueName: {_iss_str(name)}", f"ValueData: {_iss_str(data)}"]
                if uninstall in flags:
                    parts.append(f"Flags: {flags[uninstall]}")
                parts.append(f"Tasks: {task}")
                if check:
                    parts.append(check)
                lines.append("; ".join(parts))
    return "\n".join(lines) + "\n"


def msix_extensions_xml(indent: str = "      ") -> str:
    """The <Extensions> block for <Application> in AppxManifest.xml."""
    by_group: Dict[str, List[str]] = {}
    for ft in FILE_TYPES:
        if ft.msix:
            by_group.setdefault(ft.group, []).append(ft.ext)
    out = [f"{indent}<Extensions>"]
    for group, exts in by_group.items():
        out += [
            f'{indent}  <uap:Extension Category="windows.fileTypeAssociation">',
            f'{indent}    <uap:FileTypeAssociation Name="cortex{group}">',
            f"{indent}      <uap:DisplayName>{escape(GROUPS[group])}</uap:DisplayName>",
            f"{indent}      <uap:SupportedFileTypes>",
        ]
        out += [f"{indent}        <uap:FileType>{ext}</uap:FileType>" for ext in exts]
        out += [
            f"{indent}      </uap:SupportedFileTypes>",
            f"{indent}    </uap:FileTypeAssociation>",
            f"{indent}  </uap:Extension>",
        ]
    out.append(f"{indent}</Extensions>")
    return "\n".join(out)


def linux_mime_types() -> List[str]:
    """Every MIME type for the .desktop file's MimeType= line, in list order."""
    seen: List[str] = []
    for ft in FILE_TYPES:
        for m in ft.mimes:
            if m not in seen:
                seen.append(m)
    return seen


def linux_mime_package_xml() -> str:
    """shared-mime-info package defining the types that are not in every
    distro's database. Installed to /usr/share/mime/packages/cortex-ide.xml."""
    defined: Dict[str, Tuple[str, List[str]]] = {}
    for ft in FILE_TYPES:
        if ft.define_mime and ft.mimes:
            comment, globs = defined.setdefault(ft.mimes[0], (GROUPS[ft.group], []))
            globs.append("*" + ft.ext)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- GENERATED by build_tools/file_assoc/gen_file_assoc.py from src/core/file_associations.py -->",
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">',
    ]
    for mime, (comment, globs) in defined.items():
        out.append(f'  <mime-type type="{mime}">')
        out.append(f"    <comment>{escape(comment)}</comment>")
        out.append('    <sub-class-of type="text/plain"/>')
        out += [f'    <glob pattern="{g}"/>' for g in globs]
        out.append("  </mime-type>")
    out.append("</mime-info>")
    return "\n".join(out) + "\n"


def linux_desktop_with_mime(desktop_text: str) -> str:
    """Set MimeType= in the [Desktop Entry] group and make sure Exec= passes
    the opened files (%F) to Cortex."""
    mime_line = "MimeType=" + ";".join(linux_mime_types()) + ";"
    out: List[str] = []
    group = ""
    have_mime = False

    def _insert_mime() -> None:
        at = len(out)
        while at > 0 and not out[at - 1].strip():
            at -= 1  # before the blank lines that end the group
        out.insert(at, mime_line)

    for line in desktop_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if group == "[Desktop Entry]" and not have_mime:
                _insert_mime()
                have_mime = True
            group = stripped
        elif group == "[Desktop Entry]":
            if stripped.startswith("MimeType="):
                line, have_mime = mime_line, True
            elif stripped.startswith("Exec=") and "%" not in stripped:
                line = line.rstrip() + " %F"
        out.append(line)
    if group == "[Desktop Entry]" and not have_mime:
        _insert_mime()
    return "\n".join(out) + "\n"
