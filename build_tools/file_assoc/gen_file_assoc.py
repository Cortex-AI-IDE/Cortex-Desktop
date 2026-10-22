"""Generate each distribution's file-association data from
src/core/file_associations.py, the only list of file types.

  python build_tools/file_assoc/gen_file_assoc.py iss      # writes cortex_file_assoc.iss (this folder)
  python build_tools/file_assoc/gen_file_assoc.py check    # exit 1 if cortex_file_assoc.iss is out of date
  python build_tools/file_assoc/gen_file_assoc.py msix     # prints the manifest's <Extensions> block
  python build_tools/file_assoc/gen_file_assoc.py linux OUT_DIR [--desktop FILE.desktop]
      # writes OUT_DIR/cortex-ide.xml (shared-mime-info package) and, with
      # --desktop, sets that file's MimeType= line and adds %F to Exec=

build.ps1 runs `iss` before ISCC, build_msix.ps1 runs `msix`. The Linux
build runs `linux` (see Docs/FILE_ASSOCIATIONS.md).
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ISS_PATH = os.path.join(HERE, "cortex_file_assoc.iss")


def _load():
    # Load the module by path so no src package (and none of its imports) runs.
    path = os.path.join(ROOT, "src", "core", "file_associations.py")
    spec = importlib.util.spec_from_file_location("cortex_file_associations", path)
    assert spec and spec.loader, path
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up here
    spec.loader.exec_module(mod)
    return mod


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    fa = _load()
    cmd = argv[0]
    if cmd == "iss":
        _write(ISS_PATH, fa.inno_script())
        print(f"wrote {os.path.relpath(ISS_PATH, ROOT)} ({len(fa.FILE_TYPES)} file types)")
        return 0
    if cmd == "check":
        current = open(ISS_PATH, encoding="utf-8").read() if os.path.isfile(ISS_PATH) else ""
        if current.replace("\r\n", "\n") != fa.inno_script():
            print(f"{os.path.relpath(ISS_PATH, ROOT)} is out of date: run gen_file_assoc.py iss")
            return 1
        print("cortex_file_assoc.iss is up to date")
        return 0
    if cmd == "msix":
        sys.stdout.write(fa.msix_extensions_xml() + "\n")
        return 0
    if cmd == "linux" and len(argv) >= 2:
        out_dir = argv[1]
        os.makedirs(out_dir, exist_ok=True)
        xml_path = os.path.join(out_dir, "cortex-ide.xml")
        _write(xml_path, fa.linux_mime_package_xml())
        print(f"wrote {xml_path}")
        if "--desktop" in argv:
            desktop = argv[argv.index("--desktop") + 1]
            text = open(desktop, encoding="utf-8").read()
            _write(desktop, fa.linux_desktop_with_mime(text))
            print(f"updated MimeType= and Exec= in {desktop} ({len(fa.linux_mime_types())} MIME types)")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
