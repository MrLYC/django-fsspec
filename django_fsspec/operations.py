import hashlib
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, F, Value, When
from django.db.models.functions import StrIndex, Substr

from .exceptions import (
    DataIntegrityError,
    FileConflictError,
    FileTooLargeError,
    NamespaceNotFoundError,
)
from .models import (
    NODE_TYPE_DIRECTORY,
    NODE_TYPE_FILE,
    FileBlock,
    FileNode,
    Namespace,
    StorageBlock,
    get_block_size,
    get_max_file_size,
)
from .validators import validate_path

INTEGRITY_OFF = "off"
INTEGRITY_METADATA = "metadata"
INTEGRITY_CHECKSUM = "checksum"
INTEGRITY_POLICIES = {INTEGRITY_OFF, INTEGRITY_METADATA, INTEGRITY_CHECKSUM}


def _compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_read_integrity(integrity: str | None = None, *, verify_checksum=False) -> str:
    if verify_checksum:
        return INTEGRITY_CHECKSUM
    if integrity is None:
        integrity = getattr(settings, "DJANGO_FSSPEC_READ_INTEGRITY", INTEGRITY_OFF)
    if integrity not in INTEGRITY_POLICIES:
        raise ValueError(
            "DJANGO_FSSPEC_READ_INTEGRITY must be one of "
            f"{sorted(INTEGRITY_POLICIES)}, got {integrity!r}"
        )
    return integrity


def _chunk_data(data: bytes, block_size: int) -> list[bytes]:
    return [data[i : i + block_size] for i in range(0, len(data), block_size)]


def _lock_namespace_for_write(namespace: int) -> Namespace:
    try:
        return Namespace.objects.select_for_update().get(pk=namespace)
    except Namespace.DoesNotExist:
        raise NamespaceNotFoundError(f"Namespace not found: {namespace}")


def _path_has_descendants(namespace: int, path: str, *, exclude_pk=None) -> bool:
    if path == "/":
        return False
    descendants = FileNode.objects.filter(
        namespace_id=namespace,
        path__startswith=path.rstrip("/") + "/",
    )
    if exclude_pk is not None:
        descendants = descendants.exclude(pk=exclude_pk)
    return descendants.exists()


def _validate_file_node_type(file_node: FileNode):
    if file_node.node_type != NODE_TYPE_FILE:
        raise IsADirectoryError(f"Path is a directory: {file_node.path}")


def _ordered_file_blocks(file_node: FileNode):
    return list(
        FileBlock.objects.filter(file=file_node)
        .select_related("block")
        .order_by("sequence", "id")
    )


def _load_file_data(
    file_node: FileNode,
    *,
    integrity: str = INTEGRITY_OFF,
    require_unshared: bool = False,
) -> bytes:
    _validate_file_node_type(file_node)

    file_blocks = _ordered_file_blocks(file_node)
    parts = []
    for file_block in file_blocks:
        parts.append(bytes(file_block.block.data))
    data = b"".join(parts)

    if integrity == INTEGRITY_OFF and not require_unshared:
        return data

    if _path_has_descendants(
        file_node.namespace_id,
        file_node.path,
        exclude_pk=file_node.pk,
    ):
        raise DataIntegrityError(
            f"File {file_node.path} has descendant paths; run fsspec_repair --dry-run"
        )

    sequences = [file_block.sequence for file_block in file_blocks]
    expected_sequences = list(range(len(file_blocks)))
    if sequences != expected_sequences:
        raise DataIntegrityError(
            f"File {file_node.path} has non-contiguous block sequences: {sequences}"
        )

    for file_block, block_data in zip(file_blocks, parts):
        block = file_block.block
        if block.is_free:
            raise DataIntegrityError(
                f"File {file_node.path} references free block {block.pk}"
            )
        if block.size != len(block_data):
            raise DataIntegrityError(
                f"Block {block.pk} size mismatch: stored={block.size}, "
                f"actual={len(block_data)}"
            )
        if require_unshared:
            shared = (
                FileBlock.objects.filter(block=block)
                .exclude(file=file_node)
                .exists()
            )
            if shared:
                raise DataIntegrityError(
                    f"Block {block.pk} is referenced by multiple files; "
                    "run fsspec_repair --dry-run"
                )
        if integrity == INTEGRITY_CHECKSUM and block.checksum:
            actual = _compute_checksum(block_data)
            if actual != block.checksum:
                raise DataIntegrityError(
                    f"Block {block.pk} checksum mismatch: "
                    f"expected {block.checksum}, got {actual}"
                )

    if file_node.size != len(data):
        raise DataIntegrityError(
            f"File {file_node.path} size mismatch: stored={file_node.size}, "
            f"actual={len(data)}"
        )

    if integrity == INTEGRITY_CHECKSUM and file_node.checksum:
        actual = _compute_checksum(data)
        if actual != file_node.checksum:
            raise DataIntegrityError(
                f"File {file_node.path} checksum mismatch: "
                f"expected {file_node.checksum}, got {actual}"
            )

    return data


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


def _release_file_blocks(file_ids):
    """Delete mappings and mark only now-ownerless storage blocks as free."""
    block_ids = list(
        FileBlock.objects.filter(file_id__in=file_ids).values_list(
            "block_id",
            flat=True,
        )
    )
    FileBlock.objects.filter(file_id__in=file_ids).delete()
    if block_ids:
        StorageBlock.objects.filter(
            id__in=block_ids,
            file_blocks__isnull=True,
        ).update(is_free=True)


def _release_blocks(file_node: FileNode):
    _release_file_blocks([file_node.pk])


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
        "id": file_node.pk,
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


def _ancestor_paths(path: str) -> list[str]:
    parts = path.strip("/").split("/")
    ancestors = []
    for i in range(1, len(parts)):
        ancestors.append("/" + "/".join(parts[:i]))
    return ancestors


def _ensure_parent_directory(namespace: int, path: str, *, require_exists: bool = False):
    parent = path.rsplit("/", 1)[0] or "/"
    ancestors = _ancestor_paths(path)
    if ancestors:
        blocking_file = (
            FileNode.objects.filter(
                namespace_id=namespace,
                path__in=ancestors,
                node_type=NODE_TYPE_FILE,
            )
            .order_by("path")
            .first()
        )
        if blocking_file is not None:
            raise NotADirectoryError(
                f"Parent is not a directory: {blocking_file.path}"
            )

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
        if FileNode.objects.filter(
            namespace_id=namespace,
            path__startswith=path.rstrip("/") + "/",
        ).exists():
            raise IsADirectoryError(f"Path is a directory: {path}")
        return None

    if file_node.node_type == NODE_TYPE_DIRECTORY:
        raise IsADirectoryError(f"Path is a directory: {path}")
    if _path_has_descendants(namespace, path, exclude_pk=file_node.pk):
        raise DataIntegrityError(
            f"File path has descendants: {path}; run fsspec_repair --dry-run"
        )
    return file_node


def write_file(
    namespace: int, path: str, data: bytes, content_type: str = ""
) -> FileNode:
    """Write data to a file path. Creates or overwrites the file.

    """
    path = validate_path(path)
    _reject_root_file_path(path)
    max_file_size = get_max_file_size()
    if len(data) > max_file_size:
        raise FileTooLargeError(
            f"File size {len(data)} exceeds maximum {max_file_size}"
        )

    block_size = get_block_size()
    chunks = _chunk_data(data, block_size)
    file_checksum = _compute_checksum(data)

    with transaction.atomic():
        _lock_namespace_for_write(namespace)
        _ensure_parent_directory(namespace, path)
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
    max_file_size = get_max_file_size()
    if len(data) > max_file_size:
        raise FileTooLargeError(
            f"File size {len(data)} exceeds maximum {max_file_size}"
        )

    block_size = get_block_size()
    chunks = _chunk_data(data, block_size)
    file_checksum = _compute_checksum(data)

    with transaction.atomic():
        _lock_namespace_for_write(namespace)
        _ensure_parent_directory(namespace, path)
        if _ensure_file_target(namespace, path) is not None:
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
        except IntegrityError:
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
    max_file_size = get_max_file_size()
    block_size = get_block_size()

    with transaction.atomic():
        _lock_namespace_for_write(namespace)
        _ensure_parent_directory(namespace, path)
        file_node = _ensure_file_target(namespace, path)

        if file_node is not None:
            # Read existing data within the transaction
            existing_data = _load_file_data(file_node)
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


def read_file(
    namespace: int,
    path: str,
    verify_checksum: bool = False,
    integrity: str | None = None,
) -> bytes:
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

    policy = _get_read_integrity(integrity, verify_checksum=verify_checksum)
    return _load_file_data(file_node, integrity=policy)


def read_file_range(
    namespace: int,
    path: str,
    start: int,
    end: int,
    *,
    integrity: str | None = None,
    file_id: int | None = None,
    version: int | None = None,
) -> bytes:
    """Read a byte range [start, end) from a file."""
    path = validate_path(path)

    try:
        if file_id is not None:
            file_node = FileNode.objects.get(namespace_id=namespace, pk=file_id)
        else:
            file_node = FileNode.objects.get(namespace_id=namespace, path=path)
    except FileNode.DoesNotExist:
        raise FileNotFoundError(f"File not found: {path}")

    if file_node.node_type == NODE_TYPE_DIRECTORY:
        raise IsADirectoryError(f"Path is a directory: {path}")
    if version is not None and file_node.version != version:
        raise FileConflictError(
            f"File was modified while reading: {path}"
        )

    policy = _get_read_integrity(integrity)
    if policy != INTEGRITY_OFF:
        data = _load_file_data(file_node, integrity=policy)
        return data[start:end]

    block_size = file_node.block_size
    if block_size <= 0:
        raise DataIntegrityError(
            f"File {path} has invalid block size: {block_size}"
        )
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

    with transaction.atomic():
        _lock_namespace_for_write(namespace)

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

    try:
        node = FileNode.objects.get(namespace_id=namespace, path=path)
    except FileNode.DoesNotExist:
        node = None
    if node is not None and node.node_type != NODE_TYPE_DIRECTORY:
        raise NotADirectoryError(f"Path is not a directory: {path}")

    delete_file(namespace, path, recursive=recursive)


def delete_file(namespace: int, path: str, recursive: bool = False):
    """Delete a file or directory."""
    path = validate_path(path)
    if path == "/":
        raise IsADirectoryError("Cannot remove root directory")

    with transaction.atomic():
        _lock_namespace_for_write(namespace)

        try:
            file_node = FileNode.objects.select_for_update().get(
                namespace_id=namespace,
                path=path,
            )
        except FileNode.DoesNotExist:
            file_node = None

        if file_node is not None and file_node.node_type == NODE_TYPE_FILE:
            _release_blocks(file_node)
            file_node.delete()
            return

        prefix = path.rstrip("/") + "/"
        children = list(
            FileNode.objects.select_for_update().filter(
                namespace_id=namespace,
                path__startswith=prefix,
            )
        )

        has_children = bool(children)
        if file_node is None and not has_children:
            raise FileNotFoundError(f"Path not found: {path}")

        if has_children and not recursive:
            raise IsADirectoryError(f"Path is a directory, use recursive=True: {path}")

        node_ids = [node.pk for node in children]
        if file_node is not None:
            node_ids.append(file_node.pk)
        _release_file_blocks(node_ids)
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


def list_directory_detail(
    namespace: int,
    path: str,
    *,
    tolerant: bool = False,
) -> list[dict]:
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
            if tolerant and info.get("type") == "file":
                try:
                    node = FileNode.objects.get(
                        namespace_id=namespace,
                        path=child_path,
                    )
                    _load_file_data(node, integrity=INTEGRITY_METADATA)
                except DataIntegrityError as exc:
                    if not tolerant:
                        raise
                    info = _corrupt_child_info(child_path, exc)
                except FileNode.DoesNotExist as exc:
                    integrity_error = DataIntegrityError(
                        f"Directory child disappeared while listing: {child_path}"
                    )
                    if not tolerant:
                        raise integrity_error from exc
                    info = _corrupt_child_info(child_path, integrity_error)
            result.append(info)
        except FileNotFoundError as exc:
            if tolerant:
                result.append(_corrupt_child_info(child_path, exc))
            else:
                # Implicit directory
                result.append(
                    {
                        "name": child_path,
                        "size": 0,
                        "type": "directory",
                    }
                )
    return result


def _corrupt_child_info(child_path: str, exc: Exception) -> dict:
    return {
        "name": child_path,
        "size": 0,
        "type": "corrupt",
        "error": str(exc),
    }


def copy_file(namespace: int, src: str, dst: str, *, integrity: str | None = None):
    """Copy a file from src to dst (no block reuse, copies data)."""
    src_info = get_file_info(namespace, src)
    if src_info["type"] == "directory":
        raise IsADirectoryError(f"Source is a directory: {src}")
    if integrity is None:
        integrity = INTEGRITY_OFF
    data = read_file(namespace, src, integrity=integrity)
    write_file(namespace, dst, data, content_type=src_info.get("content_type", ""))


def move_file(namespace: int, src: str, dst: str, overwrite: bool = False):
    """Move a file by updating its path."""
    src = validate_path(src)
    dst = validate_path(dst)
    _reject_root_file_path(src)
    _reject_root_file_path(dst)
    if src == dst:
        return

    with transaction.atomic():
        _lock_namespace_for_write(namespace)
        _ensure_parent_directory(namespace, dst)
        try:
            file_node = FileNode.objects.select_for_update().get(
                namespace_id=namespace,
                path=src,
            )
        except FileNode.DoesNotExist:
            if FileNode.objects.filter(
                namespace_id=namespace,
                path__startswith=src.rstrip("/") + "/",
            ).exists():
                raise IsADirectoryError(f"Source is a directory: {src}")
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

        if dest_node is None and FileNode.objects.filter(
            namespace_id=namespace,
            path__startswith=dst.rstrip("/") + "/",
        ).exists():
            raise IsADirectoryError(f"Destination is a directory: {dst}")

        if dest_node is not None:
            if not overwrite:
                raise FileExistsError(f"Destination already exists: {dst}")
            if dest_node.node_type == NODE_TYPE_DIRECTORY:
                raise IsADirectoryError(f"Destination is a directory: {dst}")
            _release_blocks(dest_node)
            dest_node.delete()

        file_node.path = dst
        file_node.save(update_fields=["path", "updated_at"])


def move_directory(namespace: int, src: str, dst: str, overwrite: bool = False):
    """Move a durable or implicit directory by rewriting FileNode paths."""
    src = validate_path(src)
    dst = validate_path(dst)
    _reject_root_file_path(src)
    _reject_root_file_path(dst)
    if src == dst:
        return

    src_prefix = src.rstrip("/") + "/"
    dst_prefix = dst.rstrip("/") + "/"
    if dst == src or dst.startswith(src_prefix):
        raise ValueError(f"Cannot move directory into itself: {src} -> {dst}")

    with transaction.atomic():
        _lock_namespace_for_write(namespace)
        _ensure_parent_directory(namespace, dst)

        try:
            source_node = FileNode.objects.select_for_update().get(
                namespace_id=namespace,
                path=src,
            )
        except FileNode.DoesNotExist:
            source_node = None

        if source_node is not None and source_node.node_type == NODE_TYPE_FILE:
            raise NotADirectoryError(f"Source is not a directory: {src}")

        source_qs = FileNode.objects.select_for_update().filter(
            namespace_id=namespace,
            path__startswith=src_prefix,
        )
        if source_node is not None:
            source_qs = FileNode.objects.filter(pk=source_node.pk) | source_qs

        source_nodes = list(source_qs.order_by("path"))
        if not source_nodes:
            raise FileNotFoundError(f"Path not found: {src}")

        destination_conflicts = FileNode.objects.filter(
            namespace_id=namespace,
            path__startswith=dst_prefix,
        )
        exact_destination = FileNode.objects.filter(
            namespace_id=namespace,
            path=dst,
        )
        if exact_destination.exists() or destination_conflicts.exists():
            if not overwrite:
                raise FileExistsError(f"Destination already exists: {dst}")
            destination_nodes = list(
                (exact_destination | destination_conflicts).order_by("path")
            )
            destination_ids = [node.pk for node in destination_nodes]
            _release_file_blocks(destination_ids)
            FileNode.objects.filter(pk__in=destination_ids).delete()

        move_id = uuid.uuid4().hex
        temp_prefix = f"/__django_fsspec_tmp_move_{move_id}"
        path_map = {}
        for node in source_nodes:
            if node.path == src:
                new_path = dst
            else:
                new_path = dst.rstrip("/") + node.path[len(src.rstrip("/")):]
            path_map[node.pk] = new_path
            temp_path = temp_prefix + node.path[len(src.rstrip("/")):]
            FileNode.objects.filter(pk=node.pk).update(path=temp_path)

        for node in source_nodes:
            FileNode.objects.filter(pk=node.pk).update(path=path_map[node.pk])
