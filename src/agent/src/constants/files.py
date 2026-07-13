"""
constants/files.py
Python conversion of constants/files.ts (157 lines)

Binary file extension detection and content analysis utilities.
"""

# Binary file extensions to skip for text-based operations.
# These files can't be meaningfully compared as text and are often large.
BINARY_EXTENSIONS: frozenset[str] = frozenset({
    # Images
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.bmp',
    '.ico',
    '.webp',
    '.tiff',
    '.tif',
    # Videos
    '.mp4',
    '.mov',
    '.avi',
    '.mkv',
    '.webm',
    '.wmv',
    '.flv',
    '.m4v',
    '.mpeg',
    '.mpg',
    # Audio
    '.mp3',
    '.wav',
    '.ogg',
    '.flac',
    '.aac',
    '.m4a',
    '.wma',
    '.aiff',
    '.opus',
    # Archives
    '.zip',
    '.tar',
    '.gz',
    '.bz2',
    '.7z',
    '.rar',
    '.xz',
    '.z',
    '.tgz',
    '.iso',
    # Executables/binaries
    '.exe',
    '.dll',
    '.so',
    '.dylib',
    '.bin',
    '.o',
    '.a',
    '.obj',
    '.lib',
    '.app',
    '.msi',
    '.deb',
    '.rpm',
    # Documents (PDF is here; FileReadTool excludes it at the call site)
    '.pdf',
    '.doc',
    '.docx',
    '.xls',
    '.xlsx',
    '.ppt',
    '.pptx',
    '.odt',
    '.ods',
    '.odp',
    # Fonts
    '.ttf',
    '.otf',
    '.woff',
    '.woff2',
    '.eot',
    # Bytecode / VM artifacts
    '.pyc',
    '.pyo',
    '.class',
    '.jar',
    '.war',
    '.ear',
    '.node',
    '.wasm',
    '.rlib',
    # Database files
    '.sqlite',
    '.sqlite3',
    '.db',
    '.mdb',
    '.idx',
    # Design / 3D
    '.psd',
    '.ai',
    '.eps',
    '.sketch',
    '.fig',
    '.xd',
    '.blend',
    '.3ds',
    '.max',
    # Flash
    '.swf',
    '.fla',
    # Lock/profiling data
    '.lockb',
    '.dat',
    '.data',
})


def has_binary_extension(file_path: str) -> bool:
    """
    Check if a file path has a binary extension.
    """
    # Find the last dot to extract extension
    last_dot = file_path.rfind('.')
    if last_dot == -1:
        return False
    ext = file_path[last_dot:].lower()
    return ext in BINARY_EXTENSIONS


# Number of bytes to read for binary content detection.
_BINARY_CHECK_SIZE: int = 8192


def is_binary_content(buffer: bytes) -> bool:
    """
    Check if a buffer contains binary content by looking for null bytes
    or a high proportion of non-printable characters.
    """
    # Check first BINARY_CHECK_SIZE bytes (or full buffer if smaller)
    check_size = min(len(buffer), _BINARY_CHECK_SIZE)

    non_printable = 0
    for i in range(check_size):
        byte = buffer[i]
        # Null byte is a strong indicator of binary
        if byte == 0:
            return True
        # Count non-printable, non-whitespace bytes
        # Printable ASCII is 32-126, plus common whitespace (9, 10, 13)
        if byte < 32 and byte not in (9, 10, 13):  # tab, newline, carriage return
            non_printable += 1

    # If more than 10% non-printable, likely binary
    return non_printable / check_size > 0.1
