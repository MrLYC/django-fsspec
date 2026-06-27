import hashlib

from django.core.management.base import BaseCommand

from django_fsspec.models import FileBlock, FileNode, StorageBlock


class Command(BaseCommand):
    help = "Check filesystem integrity (block checksums and file consistency)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--namespace",
            type=int,
            default=None,
            help="Check only a specific namespace",
        )

    def handle(self, *args, **options):
        namespace = options["namespace"]
        errors = []

        # Check block checksums
        self.stdout.write("Checking block checksums...")
        blocks = StorageBlock.objects.filter(is_free=False)
        block_count = 0
        for block in blocks.iterator():
            block_count += 1
            expected = hashlib.sha256(bytes(block.data)).hexdigest()
            if block.checksum and block.checksum != expected:
                errors.append(
                    f"Block {block.id}: checksum mismatch "
                    f"(stored={block.checksum}, computed={expected})"
                )
            if block.size != len(bytes(block.data)):
                errors.append(
                    f"Block {block.id}: size mismatch "
                    f"(stored={block.size}, actual={len(bytes(block.data))})"
                )

        self.stdout.write(f"  Checked {block_count} blocks")

        # Check file checksums
        self.stdout.write("Checking file checksums...")
        files_qs = FileNode.objects.all()
        if namespace is not None:
            files_qs = files_qs.filter(namespace=namespace)

        file_count = 0
        for file_node in files_qs.iterator():
            file_count += 1
            file_blocks = (
                FileBlock.objects.filter(file=file_node)
                .select_related("block")
                .order_by("sequence")
            )

            data = b"".join(bytes(fb.block.data) for fb in file_blocks)
            actual_size = len(data)

            if file_node.size != actual_size:
                errors.append(
                    f"File {file_node.path} (ns={file_node.namespace}): "
                    f"size mismatch (stored={file_node.size}, actual={actual_size})"
                )

            if file_node.checksum:
                expected = hashlib.sha256(data).hexdigest()
                if file_node.checksum != expected:
                    errors.append(
                        f"File {file_node.path} (ns={file_node.namespace}): "
                        f"checksum mismatch (stored={file_node.checksum}, "
                        f"computed={expected})"
                    )

            # Check sequence continuity
            sequences = list(file_blocks.values_list("sequence", flat=True))
            expected_seq = list(range(len(sequences)))
            if sequences != expected_seq:
                errors.append(
                    f"File {file_node.path} (ns={file_node.namespace}): "
                    f"non-contiguous block sequences: {sequences}"
                )

        self.stdout.write(f"  Checked {file_count} files")

        # Check for orphaned file blocks
        self.stdout.write("Checking for orphaned blocks...")
        orphaned = FileBlock.objects.filter(block__is_free=True).count()
        if orphaned:
            errors.append(f"Found {orphaned} file blocks pointing to free storage blocks")

        if errors:
            self.stdout.write(self.style.ERROR(f"\nFound {len(errors)} errors:"))
            for error in errors:
                self.stdout.write(self.style.ERROR(f"  - {error}"))
        else:
            self.stdout.write(
                self.style.SUCCESS("\nFilesystem check passed. No errors found.")
            )
