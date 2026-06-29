from django.db import migrations, transaction


class RechunkOperation(migrations.operations.base.Operation):
    """Migration operation to rechunk all files to a new block size.

    Usage in a migration:
        from django_fsspec.migrations_ops import RechunkOperation

        class Migration(migrations.Migration):
            operations = [
                RechunkOperation(new_block_size=64 * 1024),
            ]
    """

    reduces_to_sql = False
    reversible = False

    def __init__(self, new_block_size):
        if new_block_size <= 0:
            raise ValueError("new_block_size must be positive")
        self.new_block_size = new_block_size

    def state_forwards(self, app_label, state):
        pass

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        alias = schema_editor.connection.alias
        FileNode = from_state.apps.get_model("django_fsspec", "FileNode")
        FileBlock = from_state.apps.get_model("django_fsspec", "FileBlock")
        StorageBlock = from_state.apps.get_model("django_fsspec", "StorageBlock")

        import hashlib

        files_to_rechunk = FileNode.objects.using(alias).exclude(
            block_size=self.new_block_size
        )

        for file_node in files_to_rechunk.iterator():
            with transaction.atomic(using=alias):
                # Read all block data in order
                file_blocks = (
                    FileBlock.objects.using(alias)
                    .filter(file=file_node)
                    .select_related("block")
                    .order_by("sequence")
                )
                data = b"".join(fb.block.data for fb in file_blocks)

                # Mark old blocks as free
                old_block_ids = list(
                    FileBlock.objects.using(alias)
                    .filter(file=file_node)
                    .values_list("block_id", flat=True)
                )
                if old_block_ids:
                    StorageBlock.objects.using(alias).filter(id__in=old_block_ids).update(
                        is_free=True
                    )

                # Delete old file-block mappings
                FileBlock.objects.using(alias).filter(file=file_node).delete()

                # Re-chunk with new block size
                chunks = [
                    data[i : i + self.new_block_size]
                    for i in range(0, max(len(data), 1), self.new_block_size)
                ]
                if not data:
                    chunks = []

                new_blocks = []
                for chunk in chunks:
                    block = StorageBlock.objects.using(alias).create(
                        data=chunk,
                        size=len(chunk),
                        checksum=hashlib.sha256(chunk).hexdigest(),
                        is_free=False,
                    )
                    new_blocks.append(block)

                # Create new file-block mappings
                FileBlock.objects.using(alias).bulk_create(
                    [
                        FileBlock(file=file_node, block=block, sequence=i)
                        for i, block in enumerate(new_blocks)
                    ]
                )

                # Update file node
                file_node.block_size = self.new_block_size
                file_node.save(using=alias, update_fields=["block_size"])

    def describe(self):
        return f"Rechunk all files to block size {self.new_block_size}"

    def deconstruct(self):
        return (
            self.__class__.__qualname__,
            [],
            {"new_block_size": self.new_block_size},
        )
