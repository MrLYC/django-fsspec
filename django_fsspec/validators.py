import re
import unicodedata

from .exceptions import PathValidationError

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f]")
_CONSECUTIVE_SLASHES_RE = re.compile(r"//+")
_DOTDOT_SEGMENT_RE = re.compile(r"(^|/)\.\\.(/|$)")


def validate_path(path: str) -> str:
    """Validate and normalize a file path.

    Rules:
    - Must start with '/'
    - No null bytes or control characters (\\x00-\\x1f)
    - No '.' or '..' path segments
    - No consecutive slashes
    - No trailing slash (except root '/' for ls)
    - Unicode NFC normalization applied

    Returns the normalized path.
    Raises PathValidationError on invalid input.
    """
    if not isinstance(path, str):
        raise PathValidationError(f"Path must be a string, got {type(path).__name__}")

    if not path:
        raise PathValidationError("Path must not be empty")

    if not path.startswith("/"):
        raise PathValidationError(f"Path must start with '/', got: {path!r}")

    if _CONTROL_CHARS_RE.search(path):
        raise PathValidationError(f"Path contains control characters: {path!r}")

    if _CONSECUTIVE_SLASHES_RE.search(path):
        raise PathValidationError(f"Path contains consecutive slashes: {path!r}")

    if path != "/" and path.endswith("/"):
        raise PathValidationError(f"Path must not end with '/': {path!r}")

    # Check for '.' and '..' segments
    segments = path.split("/")
    for segment in segments:
        if segment in (".", ".."):
            raise PathValidationError(
                f"Path contains {segment!r} segment: {path!r}"
            )

    # Unicode NFC normalization
    normalized = unicodedata.normalize("NFC", path)

    return normalized
