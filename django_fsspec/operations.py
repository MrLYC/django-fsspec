import hashlib

from django.db import transaction
from django.db.models import Case, F, Value, When
from django.db.models.functions import StrIndex, Substr

from .exceptions import FileConflictError, FileTooLargeError
from .models import FileBlock, FileNode, StorageBlock, get_block_size, get_max_file_size
from .validators import validate_path


def _compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunk_data(data: bytes, block_size: int) -> list[bytes]:
    return [data[i : i + block_size] for i in range(0, len(data), block_size)]


def _allocate_blocks(chunks: list[bytes]) -> list[StorageBlock]:
    """Allocate storage blocks for chunks. Uses free block pool with optimistic
    locking, falls back to creating new blocks on contention."""
    need = len(chunks)
    if need == 0:
        return []

    # Try to grab free blocks
    free_ids = list(
        StorageBlock.objects.filter(is_free=True)
        .values_list("id", flat=True)[: need * 2]
    )

    acquired_blocks = []
    if free_ids:
        candidate_ids = free_ids[:need]
        acquired_count = StorageBlock.objects.filter(
            id__in=candidate_ids, is_free=True
        ).update(is_free=False)

        if acquired_count > 0:
            acquired_blocks = list(
                StorageBlock.objects.filter(
                    id__in=candidate_ids, is_free=False
                )[:acquired_count]
            )

    # Write data into acquired (reused) blocks
    for block, chunk in zip(acquired_blocks, chunks):
        block.data = chunk
        block.size = len(chunk)
        block.checksum = _compute_checksum(chunk)
        block.save(update_fields=["data", "size", "checksum"])

    # Create new blocks for any shortfall (with data inline to avoid PK issues)
    shortfall = need - len(acquired_blocks)
    if shortfall > 0:
        remaining_chunks = chunks[len(acquired_blocks):]
        for chunk in remaining_chunks:
            block = StorageBlock.objects.create(
                data=chunk,
                size=len(chunk),
                checksum=_compute_checksum(chunk),
                is_free=False,
            )
            acquired_blocks.append(block)

    return acquired_blocks


def _release_blocks(file_node: FileNode):
    """Mark all blocks associated with a file as free and delete FileBlock mappings."""
    block_ids = list(
        FileBlock.objects.filter(file=file_node).values_list("block_id", flat=True)
    )
    if block_ids:
        StorageBlock.objects.filter(id__in=block_ids).update(is_free=True)
        FileBlock.objects.filter(file=file_node).delete()


def write_file(
    namespace: int, path: str, data: bytes, content_type: str = ""
) -> FileNode:
    """Write data to a file path. Creates or overwrites the file.

    Uses optimistic locking via the version field. If another process modified
    the file between read and write, raises FileConflictError.
    """
    path = validate_path(path)
    max_file_size = get_max_file_size()
    if len(data) > max_file_size:
        raise FileTooLargeError(
            f"File size {len(data)} exceeds maximum {max_file_size}"
        )

    block_size = get_block_size()
    chunks = _chunk_data(data, block_size)
    file_checksum = _compute_checksum(data)

    with transaction.atomic():
        try:
            file_node = FileNode.objects.get(namespace=namespace, path=path)
        except FileNode.DoesNotExist:
            file_node = None

        if file_node is not None:
            old_version = file_node.version
            _release_blocks(file_node)

            # Optimistic lock: UPDATE ... WHERE version = old_version
            updated = FileNode.objects.filter(
                pk=file_node.pk, version=old_version
            ).update(
                size=len(data),
                block_size=block_size,
                checksum=file_checksum,
                content_type=content_type if content_type else file_node.content_type,
                version=old_version + 1,
            )
            if updated == 0:
                raise FileConflictError(
                    f"File was modified by another process: {path}"
                )
            file_node.refresh_from_db()
        else:
            file_node = FileNode.objects.create(
                namespace=namespace,
                path=path,
                size=len(data),
                block_size=block_size,
                checksum=file_checksum,
                content_type=content_type,
            )

        blocks = _allocate_blocks(chunks)
        FileBlock.objects.bulk_create(
            [
                FileBlock(file=file_node, block=block, sequence=i)
                for i, block in enumerate(blocks)
            ]
        )

    return file_node


def create_file_exclusive(
    namespace: int, path: str, data: bytes, content_type: str = ""
) -> FileNode:
    """Create a file exclusively. Raises FileExistsError if it already exists."""
    path = validate_path(path)
    max_file_size = get_max_file_size()
    if len(data) > max_file_size:
        raise FileTooLargeError(
            f"File size {len(data)} exceeds maximum {max_file_size}"
        )

    block_size = get_block_size()
    chunks = _chunk_data(data, block_size)
    file_checksum = _compute_checksum(data)

    with transaction.atomic():
        if FileNode.objects.filter(namespace=namespace, path=path).exists():
            raise FileExistsError(f"File already exists: {path}")

        try:
            file_node = FileNode.objects.create(
                namespace=namespace,
                path=path,
                size=len(data),
                block_size=block_size,
                checksum=file_checksum,
                content_type=content_type,
            )
        except Exception:
            raise FileExistsError(f"File already exists: {path}")

        blocks = _allocate_blocks(chunks)
        FileBlock.objects.bulk_create(
            [
                FileBlock(file=file_node, block=block, sequence=i)
                for i, block in enumerate(blocks)
            ]
        )

    return file_node


def append_file(
    namespace: int, path: str, data: bytes, content_type: str = ""
) -> FileNode:
    """Append data to an existing file, or create it if it doesn't exist."""
    path = validate_path(path)

    try:
        existing_data = read_file(namespace, path)
    except FileNotFoundError:
        existing_data = b""

    new_data = existing_data + data
    return write_file(namespace, path, new_data, content_type)


def read_file(namespace: int, path: str, verify_checksum: bool = False) -> bytes:
    """Read entire file content.

    Parameters
    ----------
    verify_checksum : bool
        If True, verify block and file checksums on read. Raises ValueError
        on mismatch.
    """
    path = validate_path(path)

    try:
        file_node = FileNode.objects.get(namespace=namespace, path=path)
    except FileNode.DoesNotExist:
        raise FileNotFoundError(f"File not found: {path}")

    file_blocks = (
        FileBlock.objects.filter(file=file_node)
        .select_related("block")
        .order_by("sequence")
    )

    parts = []
    for fb in file_blocks:
        block_data = bytes(fb.block.data)
        if verify_checksum and fb.block.checksum:
            actual = _compute_checksum(block_data)
            if actual != fb.block.checksum:
                raise ValueError(
                    f"Block {fb.block.pk} checksum mismatch: "
                    f"expected {fb.block.checksum}, got {actual}"
                )
        parts.append(block_data)

    data = b"".join(parts)

    if verify_checksum and file_node.checksum:
        actual = _compute_checksum(data)
        if actual != file_node.checksum:
            raise ValueError(
                f"File {path} checksum mismatch: "
                f"expected {file_node.checksum}, got {actual}"
            )

    return data


def read_file_range(namespace: int, path: str, start: int, end: int) -> bytes:
    """Read a byte range [start, end) from a file."""
    path = validate_path(path)

    try:
        file_node = FileNode.objects.get(namespace=namespace, path=path)
    except FileNode.DoesNotExist:
        raise FileNotFoundError(f"File not found: {path}")

    block_size = file_node.block_size
    start_block = start // block_size
    end_block = (end - 1) // block_size if end > 0 else 0

    file_blocks = (
        FileBlock.objects.filter(
            file=file_node, sequence__gte=start_block, sequence__lte=end_block
        )
        .select_related("block")
        .order_by("sequence")
    )

    result = b"".join(fb.block.data for fb in file_blocks)

    # Trim to requested range
    offset_in_first = start % block_size
    length = end - start
    return result[offset_in_first : offset_in_first + length]


def get_file_info(namespace: int, path: str) -> dict:
    """Get file metadata."""
    path = validate_path(path)

    try:
        file_node = FileNode.objects.get(namespace=namespace, path=path)
        return {
            "name": file_node.path,
            "size": file_node.size,
            "type": "file",
            "checksum": file_node.checksum,
            "content_type": file_node.content_type,
            "version": file_node.version,
            "block_size": file_node.block_size,
            "created": file_node.created_at,
            "updated": file_node.updated_at,
        }
    except FileNode.DoesNotExist:
        pass

    # Check if it's an implicit directory
    prefix = path.rstrip("/") + "/"
    if FileNode.objects.filter(namespace=namespace, path__startswith=prefix).exists():
        return {
            "name": path,
            "size": 0,
            "type": "directory",
        }

    raise FileNotFoundError(f"Path not found: {path}")


def file_exists(namespace: int, path: str) -> bool:
    """Check if a file or implicit directory exists."""
    path = validate_path(path)

    if FileNode.objects.filter(namespace=namespace, path=path).exists():
        return True

    # Check implicit directory
    prefix = path.rstrip("/") + "/"
    return FileNode.objects.filter(
        namespace=namespace, path__startswith=prefix
    ).exists()


def delete_file(namespace: int, path: str, recursive: bool = False):
    """Delete a file or directory."""
    path = validate_path(path)

    # Try as file first
    try:
        file_node = FileNode.objects.get(namespace=namespace, path=path)
        with transaction.atomic():
            _release_blocks(file_node)
            file_node.delete()
        return
    except FileNode.DoesNotExist:
        pass

    # Try as directory
    prefix = path.rstrip("/") + "/"
    children = FileNode.objects.filter(namespace=namespace, path__startswith=prefix)

    if not children.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    if not recursive:
        raise IsADirectoryError(f"Path is a directory, use recursive=True: {path}")

    with transaction.atomic():
        # Mark all blocks as free
        block_ids = list(
            FileBlock.objects.filter(file__in=children).values_list(
                "block_id", flat=True
            )
        )
        if block_ids:
            StorageBlock.objects.filter(id__in=block_ids).update(is_free=True)
        children.delete()


def list_directory(namespace: int, path: str) -> list[str]:
    """List immediate children of a directory (names only, not full paths)."""
    if path == "/":
        prefix = "/"
    else:
        path = validate_path(path)
        prefix = path.rstrip("/") + "/"

    prefix_len = len(prefix)

    return list(
        FileNode.objects.filter(
            namespace=namespace,
            path__startswith=prefix,
        )
        .annotate(
            relative=Substr("path", prefix_len + 1),
            slash_pos=StrIndex("relative", Value("/")),
            next_part=Case(
                When(slash_pos=0, then="relative"),
                default=Substr("relative", 1, F("slash_pos") - 1),
            ),
        )
        .values_list("next_part", flat=True)
        .distinct()
        .order_by("next_part")
    )


def list_directory_detail(namespace: int, path: str) -> list[dict]:
    """List immediate children with detail (name, size, type)."""
    children = list_directory(namespace, path)

    if path == "/":
        prefix = "/"
    else:
        prefix = path.rstrip("/") + "/"

    result = []
    for name in children:
        child_path = prefix + name
        try:
            info = get_file_info(namespace, child_path)
            result.append(info)
        except FileNotFoundError:
            # Implicit directory
            result.append(
                {
                    "name": child_path,
                    "size": 0,
                    "type": "directory",
                }
            )
    return result


def copy_file(namespace: int, src: str, dst: str):
    """Copy a file from src to dst (no block reuse, copies data)."""
    data = read_file(namespace, src)
    src_info = get_file_info(namespace, src)
    write_file(namespace, dst, data, content_type=src_info.get("content_type", ""))


def move_file(namespace: int, src: str, dst: str):
    """Move a file by updating its path."""
    src = validate_path(src)
    dst = validate_path(dst)

    try:
        file_node = FileNode.objects.get(namespace=namespace, path=src)
    except FileNode.DoesNotExist:
        raise FileNotFoundError(f"File not found: {src}")

    if FileNode.objects.filter(namespace=namespace, path=dst).exists():
        raise FileExistsError(f"Destination already exists: {dst}")

    file_node.path = dst
    file_node.save(update_fields=["path", "updated_at"])
