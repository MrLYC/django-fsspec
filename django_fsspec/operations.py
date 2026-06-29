import hashlib

from django.db import transaction
from django.db.models import Case, F, Value, When
from django.db.models.functions import StrIndex, Substr

from .exceptions import FileConflictError, FileTooLargeError
from .models import (
    NODE_TYPE_DIRECTORY,
    NODE_TYPE_FILE,
    FileBlock,
    FileNode,
    StorageBlock,
    get_block_size,
    get_max_file_size,
)
from .validators import validate_path


def _compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunk_data(data: bytes, block_size: int) -> list[bytes]:
    return [data[i : i + block_size] for i in range(0, len(data), block_size)]


def _allocate_blocks(chunks: list[bytes]) -> list[StorageBlock]:
    """Allocate storage blocks for chunks.

    Always create new blocks instead of reusing the free block pool. Reusing free
    blocks safely across all supported databases requires row-locking semantics
    that are not uniformly available; correctness is more important than storage
    reuse on the write path. Free blocks can be permanently removed by GC.
    """
    blocks = []
    for chunk in chunks:
        blocks.append(
            StorageBlock.objects.create(
                data=chunk,
                size=len(chunk),
                checksum=_compute_checksum(chunk),
                is_free=False,
            )
        )
    return blocks


def _release_blocks(file_node: FileNode):
    """Mark all blocks associated with a file as free and delete FileBlock mappings."""
    block_ids = list(
        FileBlock.objects.filter(file=file_node).values_list("block_id", flat=True)
    )
    if block_ids:
        StorageBlock.objects.filter(id__in=block_ids).update(is_free=True)
        FileBlock.objects.filter(file=file_node).delete()


def _node_info(file_node: FileNode) -> dict:
    """Return fsspec-style metadata for a stored node."""
    if file_node.node_type == NODE_TYPE_DIRECTORY:
        return {
            "name": file_node.path,
            "size": 0,
            "type": "directory",
            "created": file_node.created_at,
            "updated": file_node.updated_at,
        }

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


def _reject_root_file_path(path: str):
    if path == "/":
        raise IsADirectoryError("Root is a directory")


def _ensure_parent_directory(namespace: int, path: str, *, require_exists: bool = False):
    parent = path.rsplit("/", 1)[0] or "/"
    if parent == "/":
        return

    try:
        parent_info = get_file_info(namespace, parent)
    except FileNotFoundError:
        if require_exists:
            raise FileNotFoundError(f"Parent directory not found: {parent}")
        return

    if parent_info["type"] != "directory":
        raise NotADirectoryError(f"Parent is not a directory: {parent}")


def _ensure_file_target(namespace: int, path: str):
    try:
        file_node = FileNode.objects.get(namespace_id=namespace, path=path)
    except FileNode.DoesNotExist:
        return None

    if file_node.node_type == NODE_TYPE_DIRECTORY:
        raise IsADirectoryError(f"Path is a directory: {path}")
    return file_node


def write_file(
    namespace: int, path: str, data: bytes, content_type: str = ""
) -> FileNode:
    """Write data to a file path. Creates or overwrites the file.

    """
    path = validate_path(path)
    _reject_root_file_path(path)
    _ensure_parent_directory(namespace, path)
    max_file_size = get_max_file_size()
    if len(data) > max_file_size:
        raise FileTooLargeError(
            f"File size {len(data)} exceeds maximum {max_file_size}"
        )

    block_size = get_block_size()
    chunks = _chunk_data(data, block_size)
    file_checksum = _compute_checksum(data)

    with transaction.atomic():
        file_node = _ensure_file_target(namespace, path)

        if file_node is not None:
            old_version = file_node.version

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
            _release_blocks(file_node)
            file_node.refresh_from_db()
        else:
            file_node = FileNode.objects.create(
                namespace_id=namespace,
                path=path,
                node_type=NODE_TYPE_FILE,
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
    _reject_root_file_path(path)
    _ensure_parent_directory(namespace, path)
    max_file_size = get_max_file_size()
    if len(data) > max_file_size:
        raise FileTooLargeError(
            f"File size {len(data)} exceeds maximum {max_file_size}"
        )

    block_size = get_block_size()
    chunks = _chunk_data(data, block_size)
    file_checksum = _compute_checksum(data)

    with transaction.atomic():
        if FileNode.objects.filter(namespace_id=namespace, path=path).exists():
            raise FileExistsError(f"File already exists: {path}")

        try:
            file_node = FileNode.objects.create(
                namespace_id=namespace,
                path=path,
                node_type=NODE_TYPE_FILE,
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
    """Append data to an existing file, or create it if it doesn't exist.

    The read and write are wrapped in a single transaction with optimistic
    locking, so concurrent appends will raise FileConflictError rather than
    silently losing data.
    """
    path = validate_path(path)
    _reject_root_file_path(path)
    _ensure_parent_directory(namespace, path)
    max_file_size = get_max_file_size()
    block_size = get_block_size()

    with transaction.atomic():
        file_node = _ensure_file_target(namespace, path)

        if file_node is not None:
            # Read existing data within the transaction
            file_blocks = (
                FileBlock.objects.filter(file=file_node)
                .select_related("block")
                .order_by("sequence")
            )
            existing_data = b"".join(fb.block.data for fb in file_blocks)
            new_data = existing_data + data

            if len(new_data) > max_file_size:
                raise FileTooLargeError(
                    f"File size {len(new_data)} exceeds maximum {max_file_size}"
                )

            old_version = file_node.version

            chunks = _chunk_data(new_data, block_size)
            file_checksum = _compute_checksum(new_data)

            updated = FileNode.objects.filter(
                pk=file_node.pk, version=old_version
            ).update(
                size=len(new_data),
                block_size=block_size,
                checksum=file_checksum,
                content_type=content_type if content_type else file_node.content_type,
                version=old_version + 1,
            )
            if updated == 0:
                raise FileConflictError(
                    f"File was modified by another process: {path}"
                )
            _release_blocks(file_node)
            file_node.refresh_from_db()
        else:
            # Create new file
            new_data = data
            if len(new_data) > max_file_size:
                raise FileTooLargeError(
                    f"File size {len(new_data)} exceeds maximum {max_file_size}"
                )

            chunks = _chunk_data(new_data, block_size)
            file_checksum = _compute_checksum(new_data)

            file_node = FileNode.objects.create(
                namespace_id=namespace,
                path=path,
                node_type=NODE_TYPE_FILE,
                size=len(new_data),
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
        file_node = FileNode.objects.get(namespace_id=namespace, path=path)
    except FileNode.DoesNotExist:
        raise FileNotFoundError(f"File not found: {path}")

    if file_node.node_type == NODE_TYPE_DIRECTORY:
        raise IsADirectoryError(f"Path is a directory: {path}")

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
        file_node = FileNode.objects.get(namespace_id=namespace, path=path)
    except FileNode.DoesNotExist:
        raise FileNotFoundError(f"File not found: {path}")

    if file_node.node_type == NODE_TYPE_DIRECTORY:
        raise IsADirectoryError(f"Path is a directory: {path}")

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
    """Get file or directory metadata."""
    path = validate_path(path)

    try:
        return _node_info(FileNode.objects.get(namespace_id=namespace, path=path))
    except FileNode.DoesNotExist:
        pass

    # Check if it's an implicit directory
    prefix = path.rstrip("/") + "/"
    if FileNode.objects.filter(namespace_id=namespace, path__startswith=prefix).exists():
        return {
            "name": path,
            "size": 0,
            "type": "directory",
        }

    raise FileNotFoundError(f"Path not found: {path}")


def file_exists(namespace: int, path: str) -> bool:
    """Check if a file or implicit directory exists."""
    path = validate_path(path)

    if FileNode.objects.filter(namespace_id=namespace, path=path).exists():
        return True

    # Check implicit directory
    prefix = path.rstrip("/") + "/"
    return FileNode.objects.filter(
        namespace_id=namespace, path__startswith=prefix
    ).exists()



def make_directory(namespace: int, path: str, create_parents: bool = False) -> FileNode:
    """Create a durable empty directory."""
    path = validate_path(path)
    if path == "/":
        raise FileExistsError("Root directory already exists")

    existing = FileNode.objects.filter(namespace_id=namespace, path=path).first()
    if existing is not None:
        if existing.node_type == NODE_TYPE_DIRECTORY:
            raise FileExistsError(f"Directory already exists: {path}")
        raise FileExistsError(f"File already exists: {path}")

    if FileNode.objects.filter(
        namespace_id=namespace, path__startswith=path.rstrip("/") + "/"
    ).exists():
        raise FileExistsError(f"Directory already exists: {path}")

    parent = path.rsplit("/", 1)[0] or "/"
    if parent != "/":
        try:
            parent_info = get_file_info(namespace, parent)
        except FileNotFoundError:
            if create_parents:
                make_directory(namespace, parent, create_parents=True)
            else:
                raise FileNotFoundError(f"Parent directory not found: {parent}")
        else:
            if parent_info["type"] != "directory":
                raise NotADirectoryError(f"Parent is not a directory: {parent}")

    return FileNode.objects.create(
        namespace_id=namespace,
        path=path,
        node_type=NODE_TYPE_DIRECTORY,
        size=0,
        checksum="",
        content_type="",
    )


def remove_directory(namespace: int, path: str, recursive: bool = False):
    """Remove a durable or implicit directory."""
    path = validate_path(path)
    if path == "/":
        raise IsADirectoryError("Cannot remove root directory")

    delete_file(namespace, path, recursive=recursive)


def delete_file(namespace: int, path: str, recursive: bool = False):
    """Delete a file or directory."""
    path = validate_path(path)

    try:
        file_node = FileNode.objects.get(namespace_id=namespace, path=path)
    except FileNode.DoesNotExist:
        file_node = None

    if file_node is not None and file_node.node_type == NODE_TYPE_FILE:
        with transaction.atomic():
            _release_blocks(file_node)
            file_node.delete()
        return

    prefix = path.rstrip("/") + "/"
    children = FileNode.objects.filter(namespace_id=namespace, path__startswith=prefix)

    if file_node is None and not children.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    if children.exists() and not recursive:
        raise IsADirectoryError(f"Path is a directory, use recursive=True: {path}")

    with transaction.atomic():
        nodes_to_delete_qs = children
        if file_node is not None:
            nodes_to_delete_qs = FileNode.objects.filter(pk=file_node.pk) | children

        node_ids = list(nodes_to_delete_qs.values_list("pk", flat=True))
        block_ids = list(
            FileBlock.objects.filter(
                file_id__in=node_ids,
                file__node_type=NODE_TYPE_FILE,
            ).values_list("block_id", flat=True)
        )
        if block_ids:
            StorageBlock.objects.filter(id__in=block_ids).update(is_free=True)
        FileBlock.objects.filter(file_id__in=node_ids).delete()
        FileNode.objects.filter(pk__in=node_ids).delete()


def list_directory(namespace: int, path: str) -> list[str]:
    """List immediate children of a directory (names only, not full paths)."""
    if path == "/":
        prefix = "/"
    else:
        path = validate_path(path)
        info = get_file_info(namespace, path)
        if info["type"] != "directory":
            raise NotADirectoryError(f"Path is not a directory: {path}")
        prefix = path.rstrip("/") + "/"

    prefix_len = len(prefix)

    return list(
        FileNode.objects.filter(
            namespace_id=namespace,
            path__startswith=prefix,
        )
        .exclude(path=path if path != "/" else "")
        .annotate(
            relative=Substr("path", prefix_len + 1),
            slash_pos=StrIndex("relative", Value("/")),
            next_part=Case(
                When(slash_pos=0, then="relative"),
                default=Substr("relative", 1, F("slash_pos") - 1),
            ),
        )
        .exclude(next_part="")
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
    src_info = get_file_info(namespace, src)
    if src_info["type"] == "directory":
        raise IsADirectoryError(f"Source is a directory: {src}")
    data = read_file(namespace, src)
    write_file(namespace, dst, data, content_type=src_info.get("content_type", ""))


def move_file(namespace: int, src: str, dst: str, overwrite: bool = False):
    """Move a file by updating its path."""
    src = validate_path(src)
    dst = validate_path(dst)
    _reject_root_file_path(src)
    _reject_root_file_path(dst)
    _ensure_parent_directory(namespace, dst)

    with transaction.atomic():
        try:
            file_node = FileNode.objects.select_for_update().get(
                namespace_id=namespace,
                path=src,
            )
        except FileNode.DoesNotExist:
            raise FileNotFoundError(f"File not found: {src}")

        if file_node.node_type == NODE_TYPE_DIRECTORY:
            raise IsADirectoryError(f"Source is a directory: {src}")

        try:
            dest_node = FileNode.objects.select_for_update().get(
                namespace_id=namespace,
                path=dst,
            )
        except FileNode.DoesNotExist:
            dest_node = None

        if dest_node is not None:
            if not overwrite:
                raise FileExistsError(f"Destination already exists: {dst}")
            if dest_node.node_type == NODE_TYPE_DIRECTORY:
                raise IsADirectoryError(f"Destination is a directory: {dst}")
            _release_blocks(dest_node)
            dest_node.delete()

        file_node.path = dst
        file_node.save(update_fields=["path", "updated_at"])
