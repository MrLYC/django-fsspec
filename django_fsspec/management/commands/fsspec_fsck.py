import hashlib
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from django_fsspec.models import (
    NODE_TYPE_DIRECTORY,
    NODE_TYPE_FILE,
    FileBlock,
    FileNode,
    StorageBlock,
)
from django_fsspec.validators import validate_path


EXIT_ATTENTION = 1


class Command(BaseCommand):
    help = "Check filesystem integrity (block checksums and file consistency)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--namespace",
            type=int,
            default=None,
            help="Check only a specific namespace",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable findings",
        )

    def handle(self, *args, **options):
        namespace = options["namespace"]
        json_output = options["json"]
        findings = []

        def add(severity, code, message, **details):
            findings.append(
                {
                    "severity": severity,
                    "code": code,
                    "message": message,
                    **details,
                }
            )

        if not json_output:
            self.stdout.write("Checking block checksums...")
        block_count = self._check_blocks(namespace, add)
        if not json_output:
            self.stdout.write(f"  Checked {block_count} blocks")

        if not json_output:
            self.stdout.write("Checking file checksums...")
        file_count = self._check_files(namespace, add)
        if not json_output:
            self.stdout.write(f"  Checked {file_count} files")

        if not json_output:
            self.stdout.write("Checking for orphaned blocks...")
        self._check_block_graph(namespace, add)

        if json_output:
            self.stdout.write(
                json.dumps(
                    {
                        "ok": not findings,
                        "findings": findings,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        elif findings:
            self.stdout.write(self.style.ERROR(f"\nFound {len(findings)} errors:"))
            for finding in findings:
                self.stdout.write(
                    self.style.ERROR(f"  - {finding['message']}")
                )
        else:
            self.stdout.write(
                self.style.SUCCESS("\nFilesystem check passed. No errors found.")
            )

        if findings:
            raise CommandError(
                "Filesystem integrity check failed",
                returncode=EXIT_ATTENTION,
            )

    def _check_blocks(self, namespace, add):
        if namespace is None:
            blocks = StorageBlock.objects.filter(is_free=False)
        else:
            block_ids = FileBlock.objects.filter(
                file__namespace_id=namespace,
                block__is_free=False,
            ).values_list("block_id", flat=True)
            blocks = StorageBlock.objects.filter(id__in=block_ids)

        block_count = 0
        for block in blocks.iterator():
            block_count += 1
            data = bytes(block.data)
            expected = hashlib.sha256(data).hexdigest()
            if block.checksum and block.checksum != expected:
                add(
                    "recoverable",
                    "block_checksum_mismatch",
                    f"Block {block.id}: checksum mismatch "
                    f"(stored={block.checksum}, computed={expected})",
                    block_id=block.id,
                )
            if block.size != len(data):
                add(
                    "recoverable",
                    "block_size_mismatch",
                    f"Block {block.id}: size mismatch "
                    f"(stored={block.size}, actual={len(data)})",
                    block_id=block.id,
                )
        return block_count

    def _check_files(self, namespace, add):
        files_qs = FileNode.objects.all()
        if namespace is not None:
            files_qs = files_qs.filter(namespace_id=namespace)

        file_count = 0
        for file_node in files_qs.iterator():
            file_count += 1
            self._check_file_node(file_node, add)
        return file_count

    def _check_file_node(self, file_node, add):
        try:
            validate_path(file_node.path)
        except Exception as exc:
            add(
                "unresolved",
                "invalid_path",
                f"FileNode {file_node.id} (ns={file_node.namespace_id}): "
                f"invalid path {file_node.path!r}: {exc}",
                file_id=file_node.id,
                namespace_id=file_node.namespace_id,
                path=file_node.path,
            )

        if file_node.node_type not in {NODE_TYPE_FILE, NODE_TYPE_DIRECTORY}:
            add(
                "critical",
                "invalid_node_type",
                f"File {file_node.path} (ns={file_node.namespace_id}): "
                f"invalid node_type {file_node.node_type!r}",
                file_id=file_node.id,
                namespace_id=file_node.namespace_id,
                path=file_node.path,
            )

        descendant_count = FileNode.objects.filter(
            namespace_id=file_node.namespace_id,
            path__startswith=file_node.path.rstrip("/") + "/",
        ).count()
        if file_node.node_type == NODE_TYPE_FILE and descendant_count:
            add(
                "unresolved",
                "path_conflict",
                f"File {file_node.path} (ns={file_node.namespace_id}): "
                f"path has {descendant_count} descendants",
                file_id=file_node.id,
                namespace_id=file_node.namespace_id,
                path=file_node.path,
            )

        file_blocks = list(
            FileBlock.objects.filter(file=file_node)
            .select_related("block")
            .order_by("sequence", "id")
        )

        if file_node.node_type == NODE_TYPE_DIRECTORY:
            if file_blocks:
                add(
                    "recoverable",
                    "directory_has_blocks",
                    f"Directory {file_node.path} (ns={file_node.namespace_id}): "
                    f"has {len(file_blocks)} file block mappings",
                    file_id=file_node.id,
                    namespace_id=file_node.namespace_id,
                    path=file_node.path,
                )
            if file_node.size != 0:
                add(
                    "recoverable",
                    "directory_size_mismatch",
                    f"Directory {file_node.path} (ns={file_node.namespace_id}): "
                    f"size mismatch (stored={file_node.size}, actual=0)",
                    file_id=file_node.id,
                    namespace_id=file_node.namespace_id,
                    path=file_node.path,
                )
            if file_node.checksum:
                add(
                    "recoverable",
                    "directory_checksum_mismatch",
                    f"Directory {file_node.path} (ns={file_node.namespace_id}): "
                    "checksum should be empty",
                    file_id=file_node.id,
                    namespace_id=file_node.namespace_id,
                    path=file_node.path,
                )
            return

        data_parts = []
        for file_block in file_blocks:
            block = file_block.block
            block_data = bytes(block.data)
            data_parts.append(block_data)
            if block.is_free:
                add(
                    "recoverable",
                    "file_references_free_block",
                    f"File {file_node.path} (ns={file_node.namespace_id}): "
                    f"references free block {block.id}",
                    file_id=file_node.id,
                    block_id=block.id,
                    namespace_id=file_node.namespace_id,
                    path=file_node.path,
                )

        data = b"".join(data_parts)
        actual_size = len(data)

        if file_node.size != actual_size:
            add(
                "recoverable",
                "file_size_mismatch",
                f"File {file_node.path} (ns={file_node.namespace_id}): "
                f"size mismatch (stored={file_node.size}, actual={actual_size})",
                file_id=file_node.id,
                namespace_id=file_node.namespace_id,
                path=file_node.path,
            )

        if file_node.checksum:
            expected = hashlib.sha256(data).hexdigest()
            if file_node.checksum != expected:
                add(
                    "recoverable",
                    "file_checksum_mismatch",
                    f"File {file_node.path} (ns={file_node.namespace_id}): "
                    f"checksum mismatch (stored={file_node.checksum}, "
                    f"computed={expected})",
                    file_id=file_node.id,
                    namespace_id=file_node.namespace_id,
                    path=file_node.path,
                )

        sequences = [file_block.sequence for file_block in file_blocks]
        expected_seq = list(range(len(sequences)))
        if sequences != expected_seq:
            add(
                "recoverable",
                "non_contiguous_sequences",
                f"File {file_node.path} (ns={file_node.namespace_id}): "
                f"non-contiguous block sequences: {sequences}",
                file_id=file_node.id,
                namespace_id=file_node.namespace_id,
                path=file_node.path,
            )

    def _check_block_graph(self, namespace, add):
        orphaned_qs = FileBlock.objects.filter(block__is_free=True)
        if namespace is not None:
            orphaned_qs = orphaned_qs.filter(file__namespace_id=namespace)
        orphaned = orphaned_qs.count()
        if orphaned:
            add(
                "recoverable",
                "file_blocks_to_free_storage",
                f"Found {orphaned} file blocks pointing to free storage blocks",
                count=orphaned,
            )

        shared_blocks = FileBlock.objects.values("block_id").annotate(
            file_owner_count=Count("file_id", distinct=True)
        )
        if namespace is not None:
            shared_blocks = shared_blocks.filter(
                file__namespace_id=namespace,
            )
        shared_blocks = shared_blocks.filter(file_owner_count__gt=1)
        for block_id, owner_count in shared_blocks.values_list(
            "block_id",
            "file_owner_count",
        ):
            add(
                "critical",
                "shared_storage_block",
                f"Block {block_id}: referenced by {owner_count} files",
                block_id=block_id,
                owner_count=owner_count,
            )

        if namespace is None:
            ownerless = StorageBlock.objects.filter(
                is_free=False,
                file_blocks__isnull=True,
            ).count()
            if ownerless:
                add(
                    "recoverable",
                    "used_block_without_owner",
                    f"Found {ownerless} used storage blocks without file owners",
                    count=ownerless,
                )
