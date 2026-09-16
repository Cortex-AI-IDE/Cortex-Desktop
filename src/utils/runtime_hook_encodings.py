"""
PyInstaller Runtime Hook - Force Import encodings & codecs at Boot

CRITICAL: PyInstaller sometimes fails to bundle the encodings stdlib module,
causing "Failed to start embedded python interpreter: Failed to import encodings
module" at startup. This hook forces ALL common encodings to be loaded before
the application starts, ensuring they are available in the frozen environment.
This hook runs BEFORE the application code.

References:
  - https://github.com/pyinstaller/pyinstaller/issues/4384
  - https://github.com/pyinstaller/pyinstaller/issues/4706
"""
# Force import of encodings module tree at boot time.
# This is the most commonly missed module in PyInstaller builds.
import encodings

# Explicitly import commonly used encoding submodules so PyInstaller's
# dependency graph includes them, even for indirect import paths.
import encodings.aliases
import encodings.ascii
import encodings.cp1250
import encodings.cp1251
import encodings.cp1252
import encodings.cp437
import encodings.idna
import encodings.latin_1
import encodings.punycode
import encodings.raw_unicode_escape
import encodings.unicode_escape
# encodings.unicode_internal was removed in Python 3.12+
import encodings.utf_8
import encodings.utf_16
import encodings.utf_16_be
import encodings.utf_16_le

# Force import of codecs module tree (also commonly missed on Windows).
import codecs
try:
    import codecs.mbcs  # Windows MBCS codec, critical on Windows builds
except (ImportError, ModuleNotFoundError):
    # Python 3.14+ / PyInstaller frozen: codecs is bundled as a flat module,
    # not a package, so submodule imports fail. MBCS is auto-registered by
    # the codecs registry on Windows anyway, so this is safe to skip.
    pass
