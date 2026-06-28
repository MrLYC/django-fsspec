import mimetypes
import urllib.parse
from datetime import datetime, timezone

from .. import operations
from ..models import Namespace
from ..validators import validate_path


def normalize_webdav_path(webdav_path: str) -> str:
    """Normalize a URL-captured WebDAV path to a filesystem path.

    Django's ``<path:>`` converter already URL-decodes the value. An empty
    string is treated as the root ``"/"``.
    """
    if not webdav_path:
        return "/"
    path = "/" + webdav_path.strip("/")
    return validate_path(path)


def http_date(dt: datetime) -> str:
    """Format a datetime as an RFC 7231 IMF-fixdate string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def iso_date(dt: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC timestamp for WebDAV creationdate."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_etag(checksum: str) -> str:
    """Return a quoted strong ETag from a raw checksum string."""
    return f'"{checksum}"'


def resolve_content_type(path: str, provided: str = "") -> str:
    """Resolve the content type for a file path.

    If the caller supplied a non-empty value it is used verbatim. Otherwise
    ``mimetypes.guess_type`` is tried; if that also fails, fall back to
    ``application/octet-stream``.
    """
    if provided:
        return provided
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _webdav_prefix(request, source_path: str) -> str:
    """Compute the WebDAV mount prefix ending with the namespace segment.

    For example, if ``request.path_info`` is ``/webdav/1/a/b`` and
    ``source_path`` is ``/a/b``, the prefix is ``/webdav/1/``.
    """
    if source_path == "/":
        prefix = request.path_info
    else:
        if not request.path_info.endswith(source_path):
            raise ValueError("Request path does not match the source path")
        prefix = request.path_info[: -len(source_path)]

    if not prefix.endswith("/"):
        prefix += "/"
    return prefix


def parse_destination(request, namespace_id: int, source_path: str) -> str:
    """Parse and validate the WebDAV ``Destination`` header.

    Returns the destination file path within the same namespace. Raises
    ``ValueError`` if the destination is missing, malformed, or points to a
    different namespace.
    """
    destination = request.headers.get("Destination", "")
    if not destination:
        raise ValueError("Missing Destination header")

    parsed = urllib.parse.urlparse(destination)
    if parsed.netloc and parsed.netloc != request.get_host():
        raise ValueError("Destination host does not match request host")

    dest_path = urllib.parse.unquote(parsed.path)
    prefix = _webdav_prefix(request, source_path)

    if not dest_path.startswith(prefix):
        raise ValueError("Destination is outside the WebDAV mount")

    dest_file_path = "/" + dest_path[len(prefix):].strip("/")
    return validate_path(dest_file_path)


def is_collection(path: str, namespace_id: int) -> bool:
    """Return True if ``path`` is an implicit directory in ``namespace_id``."""
    try:
        info = operations.get_file_info(namespace_id, path)
    except FileNotFoundError:
        return False
    return info.get("type") == "directory"


def ensure_namespace_exists(namespace_id: int) -> Namespace:
    """Return the Namespace or raise FileNotFoundError."""
    try:
        return Namespace.objects.get(id=namespace_id)
    except Namespace.DoesNotExist as exc:
        raise FileNotFoundError(
            f"Namespace not found: {namespace_id}"
        ) from exc


__all__ = [
    "ensure_namespace_exists",
    "http_date",
    "iso_date",
    "is_collection",
    "make_etag",
    "normalize_webdav_path",
    "parse_destination",
    "resolve_content_type",
]
