class DjangoFsspecError(Exception):
    """Base exception for django-fsspec."""


class FileConflictError(DjangoFsspecError):
    """Raised when optimistic lock detects concurrent modification."""


class DataIntegrityError(DjangoFsspecError, ValueError):
    """Raised when persisted filesystem metadata or content is inconsistent."""


class PathValidationError(DjangoFsspecError):
    """Raised when a path fails validation (illegal characters, traversal, etc.)."""


class FileTooLargeError(DjangoFsspecError):
    """Raised when file size exceeds MAX_FILE_SIZE."""


class NamespaceNotFoundError(DjangoFsspecError, FileNotFoundError):
    """Raised when an operation targets a missing namespace."""
