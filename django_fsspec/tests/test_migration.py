import hashlib

from django_test_migrations.contrib.unittest_case import MigratorTestCase


class TestInitialMigration(MigratorTestCase):
    """Test that the initial migration creates all tables with correct schema."""

    migrate_from = ("django_fsspec", None)
    migrate_to = ("django_fsspec", "0001_initial")

    def test_filenode_created(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        assert FileNode._meta.db_table == "django_fsspec_filenode"

    def test_filenode_fields(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        field_names = {f.name for f in FileNode._meta.get_fields()}
        expected = {
            "id", "namespace", "path", "size", "block_size",
            "checksum", "content_type", "version",
            "created_at", "updated_at", "blocks", "node_type",
        }
        assert expected.issubset(field_names), f"Missing fields: {expected - field_names}"

    def test_filenode_path_max_length(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        path_field = FileNode._meta.get_field("path")
        assert path_field.max_length == 700

    def test_filenode_unique_together(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        assert ("namespace", "path") in FileNode._meta.unique_together

    def test_storageblock_created(self):
        StorageBlock = self.new_state.apps.get_model("django_fsspec", "StorageBlock")
        field_names = {f.name for f in StorageBlock._meta.get_fields()}
        expected = {"id", "data", "size", "checksum", "is_free", "created_at"}
        assert expected.issubset(field_names)

    def test_fileblock_created(self):
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")
        field_names = {f.name for f in FileBlock._meta.get_fields()}
        expected = {"id", "file", "block", "sequence"}
        assert expected.issubset(field_names)

    def test_fileblock_unique_together(self):
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")
        assert ("file", "sequence") in FileBlock._meta.unique_together

    def test_can_create_and_query(self):
        """Verify the migration actually created usable tables."""
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        StorageBlock = self.new_state.apps.get_model("django_fsspec", "StorageBlock")
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")

        node = FileNode.objects.create(
            namespace_id=1, path="/test.txt", size=5,
            block_size=256 * 1024, checksum="abc", version=1,
        )
        block = StorageBlock.objects.create(
            data=b"hello", size=5, checksum="abc", is_free=False,
        )
        fb = FileBlock.objects.create(file=node, block=block, sequence=0)

        assert FileNode.objects.count() == 1
        assert StorageBlock.objects.count() == 1
        assert FileBlock.objects.count() == 1

        # Verify cascade delete
        node.delete()
        assert FileBlock.objects.count() == 0
        assert StorageBlock.objects.count() == 1  # PROTECT, not cascaded

    def test_fileblock_cascade_on_filenode_delete(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        StorageBlock = self.new_state.apps.get_model("django_fsspec", "StorageBlock")
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")

        node = FileNode.objects.create(
            namespace_id=1, path="/cascade.txt", size=0,
            block_size=256 * 1024, version=1,
        )
        block = StorageBlock.objects.create(data=b"", size=0, is_free=False)
        FileBlock.objects.create(file=node, block=block, sequence=0)

        node.delete()
        assert FileBlock.objects.count() == 0

    def test_namespace_isolation(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        Namespace = self.new_state.apps.get_model("django_fsspec", "Namespace")
        Namespace.objects.create(id=2, name="other")

        FileNode.objects.create(
            namespace_id=1, path="/test.txt", size=0,
            block_size=256 * 1024, version=1,
        )
        FileNode.objects.create(
            namespace_id=2, path="/test.txt", size=0,
            block_size=256 * 1024, version=1,
        )
        assert FileNode.objects.count() == 2


class TestRechunkMigration(MigratorTestCase):
    """Test RechunkOperation via the migration framework."""

    migrate_from = ("django_fsspec", "0001_initial")
    migrate_to = ("django_fsspec", "0002_rechunk_test")

    def prepare(self):
        """Create files with the original block size before rechunking."""
        FileNode = self.old_state.apps.get_model("django_fsspec", "FileNode")
        StorageBlock = self.old_state.apps.get_model("django_fsspec", "StorageBlock")
        FileBlock = self.old_state.apps.get_model("django_fsspec", "FileBlock")

        # File 1: 1000 bytes in one block (block_size=256KB)
        data1 = b"A" * 1000
        node1 = FileNode.objects.create(
            namespace_id=1, path="/rechunk/a.txt", size=len(data1),
            block_size=256 * 1024,
            checksum=hashlib.sha256(data1).hexdigest(),
            version=1,
        )
        block1 = StorageBlock.objects.create(
            data=data1, size=len(data1),
            checksum=hashlib.sha256(data1).hexdigest(),
            is_free=False,
        )
        FileBlock.objects.create(file=node1, block=block1, sequence=0)

        # File 2: empty file
        node2 = FileNode.objects.create(
            namespace_id=1, path="/rechunk/empty.txt", size=0,
            block_size=256 * 1024,
            checksum=hashlib.sha256(b"").hexdigest(),
            version=1,
        )

        # File 3: already at target block_size (should be skipped)
        data3 = b"B" * 100
        node3 = FileNode.objects.create(
            namespace_id=1, path="/rechunk/skip.txt", size=len(data3),
            block_size=500,  # target block_size
            checksum=hashlib.sha256(data3).hexdigest(),
            version=1,
        )
        block3 = StorageBlock.objects.create(
            data=data3, size=len(data3),
            checksum=hashlib.sha256(data3).hexdigest(),
            is_free=False,
        )
        FileBlock.objects.create(file=node3, block=block3, sequence=0)

    def test_rechunked_file_has_new_block_size(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        node = FileNode.objects.get(path="/rechunk/a.txt")
        assert node.block_size == 500

    def test_rechunked_file_has_correct_blocks(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")
        node = FileNode.objects.get(path="/rechunk/a.txt")

        blocks = FileBlock.objects.filter(file=node).order_by("sequence")
        # 1000 bytes / 500 block_size = 2 blocks
        assert blocks.count() == 2

    def test_rechunked_data_intact(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")
        node = FileNode.objects.get(path="/rechunk/a.txt")

        blocks = (
            FileBlock.objects.filter(file=node)
            .select_related("block")
            .order_by("sequence")
        )
        data = b"".join(bytes(fb.block.data) for fb in blocks)
        assert data == b"A" * 1000

    def test_empty_file_rechunked(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        node = FileNode.objects.get(path="/rechunk/empty.txt")
        assert node.block_size == 500

    def test_matching_file_skipped(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")
        node = FileNode.objects.get(path="/rechunk/skip.txt")
        assert node.block_size == 500  # was already 500
        assert FileBlock.objects.filter(file=node).count() == 1  # unchanged

    def test_old_blocks_freed(self):
        StorageBlock = self.new_state.apps.get_model("django_fsspec", "StorageBlock")
        # Original block from a.txt should be marked free
        free_count = StorageBlock.objects.filter(is_free=True).count()
        assert free_count >= 1

    def test_block_sequences_contiguous(self):
        FileNode = self.new_state.apps.get_model("django_fsspec", "FileNode")
        FileBlock = self.new_state.apps.get_model("django_fsspec", "FileBlock")

        for node in FileNode.objects.all():
            sequences = list(
                FileBlock.objects.filter(file=node)
                .order_by("sequence")
                .values_list("sequence", flat=True)
            )
            if sequences:
                assert sequences == list(range(len(sequences))), \
                    f"Non-contiguous sequences for {node.path}: {sequences}"
