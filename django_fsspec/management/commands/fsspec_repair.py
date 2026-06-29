import hashlib

from django.core.management.base import BaseCommand
from django.db import transaction

from django_fsspec.models import (
    NODE_TYPE_DIRECTORY,
    NODE_TYPE_FILE,
    FileBlock,
    FileNode,
    StorageBlock,
)


class Command(BaseCommand):
    help = "Repair filesystem metadata from currently recoverable database content"

    def add_arguments(self, parser):
        parser.add_argument(
            "--namespace",
            type=int,
            default=None,
            help="Repair only a specific namespace",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview repairs without modifying the database",
        )

    def handle(self, *args, **options):
        namespace = options["namespace"]
        dry_run = options["dry_run"]

        summary = {
            "block_metadata": 0,
            "free_referenced_blocks": 0,
            "unreferenced_used_blocks": 0,
            "directory_mappings": 0,
            "directory_metadata": 0,
            "file_sequences": 0,
            "file_metadata": 0,
        }

        if dry_run:
            self.stdout.write("Previewing filesystem repairs...")
        else:
            self.stdout.write("Repairing filesystem metadata...")

        with transaction.atomic():
            self._repair_free_referenced_blocks(namespace, dry_run, summary)
            self._repair_directories(namespace, dry_run, summary)
            self._repair_file_nodes(namespace, dry_run, summary)
            self._repair_block_metadata(namespace, dry_run, summary)
            if namespace is None:
                self._repair_unreferenced_used_blocks(dry_run, summary)

        self.stdout.write("")
        for label, count in summary.items():
            self.stdout.write(f"{label}: {count}")

        total = sum(summary.values())
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\nWould apply {total} repair actions. Re-run without --dry-run "
                    "to modify the database."
                )
            )
        elif total:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nApplied {total} repair actions. Run fsspec_fsck to verify."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nNo repair actions needed."))

    def _file_nodes(self, namespace):
        nodes = FileNode.objects.all()
        if namespace is not None:
            nodes = nodes.filter(namespace_id=namespace)
        return nodes

    def _file_blocks(self, namespace):
        blocks = FileBlock.objects.select_related("file", "block")
        if namespace is not None:
            blocks = blocks.filter(file__namespace_id=namespace)
        return blocks

    def _repair_free_referenced_blocks(self, namespace, dry_run, summary):
        block_ids = list(
            self._file_blocks(namespace)
            .filter(block__is_free=True)
            .values_list("block_id", flat=True)
            .distinct()
        )
        summary["free_referenced_blocks"] = len(block_ids)
        if block_ids and not dry_run:
            StorageBlock.objects.filter(id__in=block_ids).update(is_free=False)

    def _repair_unreferenced_used_blocks(self, dry_run, summary):
        referenced_block_ids = FileBlock.objects.values("block_id")
        blocks = StorageBlock.objects.filter(is_free=False).exclude(
            id__in=referenced_block_ids
        )
        count = blocks.count()
        summary["unreferenced_used_blocks"] = count
        if count and not dry_run:
            blocks.update(is_free=True)

    def _repair_block_metadata(self, namespace, dry_run, summary):
        if namespace is None:
            blocks = StorageBlock.objects.filter(is_free=False)
        else:
            block_ids = (
                self._file_blocks(namespace)
                .values_list("block_id", flat=True)
                .distinct()
            )
            blocks = StorageBlock.objects.filter(id__in=block_ids)

        count = 0
        for block in blocks.iterator():
            data = bytes(block.data)
            expected_size = len(data)
            expected_checksum = hashlib.sha256(data).hexdigest()
            fields = []
            if block.size != expected_size:
                block.size = expected_size
                fields.append("size")
            if block.checksum != expected_checksum:
                block.checksum = expected_checksum
                fields.append("checksum")
            if fields:
                count += 1
                if not dry_run:
                    block.save(update_fields=fields)
        summary["block_metadata"] = count

    def _repair_directories(self, namespace, dry_run, summary):
        directories = self._file_nodes(namespace).filter(node_type=NODE_TYPE_DIRECTORY)
        for directory in directories.iterator():
            file_blocks = FileBlock.objects.filter(file=directory)
            block_ids = list(file_blocks.values_list("block_id", flat=True))
            if block_ids:
                summary["directory_mappings"] += file_blocks.count()
                if not dry_run:
                    file_blocks.delete()
                    StorageBlock.objects.filter(
                        id__in=block_ids,
                        file_blocks__isnull=True,
                    ).update(is_free=True)

            fields = []
            if directory.size != 0:
                directory.size = 0
                fields.append("size")
            if directory.checksum != "":
                directory.checksum = ""
                fields.append("checksum")
            if fields:
                summary["directory_metadata"] += 1
                if not dry_run:
                    directory.save(update_fields=fields + ["updated_at"])

    def _repair_file_nodes(self, namespace, dry_run, summary):
        files = self._file_nodes(namespace).filter(node_type=NODE_TYPE_FILE)
        for file_node in files.iterator():
            file_blocks = list(
                FileBlock.objects.filter(file=file_node)
                .select_related("block")
                .order_by("sequence", "id")
            )

            sequences = [file_block.sequence for file_block in file_blocks]
            expected_sequences = list(range(len(file_blocks)))
            sequence_changed = sequences != expected_sequences
            if sequence_changed:
                summary["file_sequences"] += 1
                if not dry_run:
                    self._renumber_file_blocks(file_blocks)

            data = b"".join(bytes(file_block.block.data) for file_block in file_blocks)
            expected_size = len(data)
            expected_checksum = hashlib.sha256(data).hexdigest()

            fields = []
            if file_node.size != expected_size:
                file_node.size = expected_size
                fields.append("size")
            if file_node.checksum != expected_checksum:
                file_node.checksum = expected_checksum
                fields.append("checksum")

            if fields or sequence_changed:
                summary["file_metadata"] += 1
                if not dry_run:
                    file_node.version += 1
                    file_node.save(
                        update_fields=fields + ["version", "updated_at"]
                    )

    def _renumber_file_blocks(self, file_blocks):
        temp_base = -(max(file_block.id for file_block in file_blocks) + 1)
        for offset, file_block in enumerate(file_blocks):
            FileBlock.objects.filter(pk=file_block.pk).update(
                sequence=temp_base - offset
            )
        for offset, file_block in enumerate(file_blocks):
            FileBlock.objects.filter(pk=file_block.pk).update(sequence=offset)
