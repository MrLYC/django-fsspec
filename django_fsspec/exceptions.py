class DjangoFsspecError(Exception):
    """Base exception for django-fsspec."""


class FileConflictError(DjangoFsspecError):
    """Raised when optimistic lock detects concurrent modification."""


class PathValidationError(DjangoFsspecError):
    """Raised when a path fails validation (illegal characters, traversal, etc.)."""


class FileTooLargeError(DjangoFsspecError):
    """Raised when file size exceeds MAX_FILE_SIZE."""
