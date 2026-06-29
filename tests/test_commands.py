from io import StringIO

import pytest
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.test import TestCase

from django_fsspec.models import FileBlock, FileNode, Namespace, StorageBlock
from django_fsspec.operations import delete_file, write_file


class TestFsspecGc(TestCase):
    def test_gc_no_free_blocks(self):
        out = StringIO()
        call_command("fsspec_gc", stdout=out)
        assert "Nothing to clean" in out.getvalue()

    def test_gc_deletes_free_blocks(self):
        write_file(1, "/test.txt", b"data")
        delete_file(1, "/test.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

        out = StringIO()
        call_command("fsspec_gc", stdout=out)
        assert StorageBlock.objects.filter(is_free=True).count() == 0
        assert "Deleted 1" in out.getvalue()

    def test_gc_keep(self):
        # Create 5 separate free blocks by writing different-sized files
        # to prevent block reuse
        for i in range(5):
            write_file(1, f"/file{i}.txt", b"x" * (i + 1))
        for i in range(5):
            delete_file(1, f"/file{i}.txt")
        free_count = StorageBlock.objects.filter(is_free=True).count()
        assert free_count == 5

        out = StringIO()
        call_command("fsspec_gc", "--keep=2", stdout=out)
        assert StorageBlock.objects.filter(is_free=True).count() == 2
        assert "Deleted 3" in out.getvalue()

    def test_gc_dry_run(self):
        write_file(1, "/test.txt", b"data")
        delete_file(1, "/test.txt")

        out = StringIO()
        call_command("fsspec_gc", "--dry-run", stdout=out)
        assert StorageBlock.objects.filter(is_free=True).count() == 1
        assert "Would delete" in out.getvalue()

    def test_gc_keep_more_than_available(self):
        write_file(1, "/test.txt", b"data")
        delete_file(1, "/test.txt")

        out = StringIO()
        call_command("fsspec_gc", "--keep=10", stdout=out)
        assert "Nothing to clean" in out.getvalue()


class TestFsspecFsck(TestCase):
    def test_fsck_healthy(self):
        write_file(1, "/test.txt", b"hello world")
        out = StringIO()
        call_command("fsspec_fsck", stdout=out)
        assert "No errors found" in out.getvalue()

    def test_fsck_corrupted_block_checksum(self):
        write_file(1, "/test.txt", b"hello")
        block = StorageBlock.objects.filter(is_free=False).first()
        block.checksum = "bad_checksum"
        block.save(update_fields=["checksum"])

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", stdout=out)
        assert "checksum mismatch" in out.getvalue()

    def test_fsck_corrupted_block_size(self):
        write_file(1, "/test.txt", b"hello")
        block = StorageBlock.objects.filter(is_free=False).first()
        block.size = 999
        block.save(update_fields=["size"])

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", stdout=out)
        assert "size mismatch" in out.getvalue()

    def test_fsck_corrupted_file_checksum(self):
        write_file(1, "/test.txt", b"hello")
        node = FileNode.objects.get(path="/test.txt")
        node.checksum = "bad_file_checksum"
        node.save(update_fields=["checksum"])

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", stdout=out)
        assert "checksum mismatch" in out.getvalue()

    def test_fsck_corrupted_file_size(self):
        write_file(1, "/test.txt", b"hello")
        node = FileNode.objects.get(path="/test.txt")
        node.size = 999
        node.save(update_fields=["size"])

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", stdout=out)
        assert "size mismatch" in out.getvalue()

    def test_fsck_namespace_filter(self):
        write_file(1, "/test0.txt", b"ns0")
        from django_fsspec.models import Namespace
        Namespace.objects.create(id=2, name="other")
        write_file(2, "/test1.txt", b"ns2")

        out = StringIO()
        call_command("fsspec_fsck", "--namespace=1", stdout=out)
        assert "Checked 1 files" in out.getvalue()

    def test_fsck_namespace_filter_ignores_other_namespace_block_corruption(self):
        write_file(1, "/test0.txt", b"ns0")
        from django_fsspec.models import Namespace
        Namespace.objects.create(id=2, name="other")
        write_file(2, "/test1.txt", b"ns2")

        node = FileNode.objects.get(namespace_id=2, path="/test1.txt")
        block = FileBlock.objects.get(file=node).block
        block.checksum = "bad"
        block.save(update_fields=["checksum"])

        out = StringIO()
        call_command("fsspec_fsck", "--namespace=1", stdout=out)
        assert "Checked 1 blocks" in out.getvalue()
        assert "Checked 1 files" in out.getvalue()

    def test_fsck_namespace_filter_reports_namespace_block_corruption(self):
        write_file(1, "/test0.txt", b"ns0")
        from django_fsspec.models import Namespace
        Namespace.objects.create(id=2, name="other")
        write_file(2, "/test1.txt", b"ns2")

        node = FileNode.objects.get(namespace_id=1, path="/test0.txt")
        block = FileBlock.objects.get(file=node).block
        block.checksum = "bad"
        block.save(update_fields=["checksum"])

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", "--namespace=1", stdout=out)
        assert "Block" in out.getvalue()
        assert "checksum mismatch" in out.getvalue()

    def test_fsck_orphaned_file_blocks(self):
        write_file(1, "/test.txt", b"data")
        # Corrupt: mark a used block as free without cleaning file blocks
        block = StorageBlock.objects.filter(is_free=False).first()
        block.is_free = True
        block.save(update_fields=["is_free"])

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", stdout=out)
        assert "orphaned" in out.getvalue().lower() or "free storage blocks" in out.getvalue()

    def test_fsck_non_contiguous_sequences(self):
        write_file(1, "/test.txt", b"data")
        node = FileNode.objects.get(path="/test.txt")
        # Corrupt: change sequence to make it non-contiguous
        fb = FileBlock.objects.filter(file=node).first()
        fb.sequence = 5
        fb.save(update_fields=["sequence"])

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", stdout=out)
        assert "non-contiguous" in out.getvalue()


class TestFsspecNamespace(TestCase):
    def test_namespace_list_includes_default(self):
        out = StringIO()
        call_command("fsspec_namespace", "list", stdout=out)
        output = out.getvalue()
        assert "default" in output
        assert "Default namespace" in output

    def test_namespace_create(self):
        out = StringIO()
        call_command(
            "fsspec_namespace",
            "create",
            "media",
            "--description=Media files",
            stdout=out,
        )

        namespace = Namespace.objects.get(name="media")
        assert namespace.description == "Media files"
        assert "Created namespace" in out.getvalue()
        assert "media" in out.getvalue()

    def test_namespace_create_duplicate_raises_command_error(self):
        Namespace.objects.create(name="media")

        out = StringIO()
        with pytest.raises(CommandError, match="already exists"):
            call_command("fsspec_namespace", "create", "media", stdout=out)

    def test_namespace_create_with_groups(self):
        Group.objects.create(name="readers")
        Group.objects.create(name="writers")

        out = StringIO()
        call_command(
            "fsspec_namespace",
            "create",
            "media",
            "--read-group=readers",
            "--write-group=writers",
            stdout=out,
        )

        namespace = Namespace.objects.get(name="media")
        assert set(namespace.read_groups.values_list("name", flat=True)) == {"readers"}
        assert set(namespace.write_groups.values_list("name", flat=True)) == {"writers"}

    def test_namespace_create_missing_group_raises_command_error(self):
        out = StringIO()
        with pytest.raises(CommandError, match="Group not found"):
            call_command(
                "fsspec_namespace",
                "create",
                "media",
                "--read-group=missing",
                stdout=out,
            )
        assert not Namespace.objects.filter(name="media").exists()

    def test_namespace_show_by_name(self):
        Namespace.objects.create(name="media", description="Media files")

        out = StringIO()
        call_command("fsspec_namespace", "show", "media", stdout=out)
        output = out.getvalue()
        assert "Namespace" in output
        assert "Name:" in output
        assert "media" in output
        assert "Media files" in output

    def test_namespace_show_by_id(self):
        namespace = Namespace.objects.create(name="media", description="Media files")

        out = StringIO()
        call_command("fsspec_namespace", "show", f"--id={namespace.id}", stdout=out)
        output = out.getvalue()
        assert f"Namespace {namespace.id}" in output
        assert "media" in output

    def test_namespace_show_missing_raises_command_error(self):
        out = StringIO()
        with pytest.raises(CommandError, match="Namespace not found"):
            call_command("fsspec_namespace", "show", "missing", stdout=out)

    def test_namespace_update_description(self):
        Namespace.objects.create(name="media", description="Old")

        out = StringIO()
        call_command(
            "fsspec_namespace",
            "update",
            "media",
            "--description=New",
            stdout=out,
        )

        namespace = Namespace.objects.get(name="media")
        assert namespace.description == "New"
        assert "Updated namespace" in out.getvalue()

    def test_namespace_update_groups(self):
        old = Group.objects.create(name="old")
        Group.objects.create(name="new")
        namespace = Namespace.objects.create(name="media")
        namespace.read_groups.add(old)

        out = StringIO()
        call_command(
            "fsspec_namespace",
            "update",
            "media",
            "--read-group=new",
            stdout=out,
        )

        namespace = Namespace.objects.get(name="media")
        assert set(namespace.read_groups.values_list("name", flat=True)) == {"new"}

    def test_namespace_update_clear_groups(self):
        group = Group.objects.create(name="readers")
        namespace = Namespace.objects.create(name="media")
        namespace.read_groups.add(group)

        out = StringIO()
        call_command(
            "fsspec_namespace",
            "update",
            "media",
            "--clear-read-groups",
            stdout=out,
        )

        namespace = Namespace.objects.get(name="media")
        assert namespace.read_groups.count() == 0

    def test_namespace_update_no_changes_raises_command_error(self):
        Namespace.objects.create(name="media")

        out = StringIO()
        with pytest.raises(CommandError, match="No changes"):
            call_command("fsspec_namespace", "update", "media", stdout=out)

    def test_namespace_update_rejects_conflicting_group_options(self):
        Namespace.objects.create(name="media")

        out = StringIO()
        with pytest.raises(CommandError, match="not both"):
            call_command(
                "fsspec_namespace",
                "update",
                "media",
                "--read-group=readers",
                "--clear-read-groups",
                stdout=out,
            )

    def test_namespace_delete_empty_namespace(self):
        Namespace.objects.create(name="media")

        out = StringIO()
        call_command("fsspec_namespace", "delete", "media", stdout=out)

        assert not Namespace.objects.filter(name="media").exists()
        assert "Deleted namespace" in out.getvalue()

    def test_namespace_delete_default_raises_command_error(self):
        out = StringIO()
        with pytest.raises(CommandError, match="default namespace"):
            call_command("fsspec_namespace", "delete", "default", stdout=out)

        assert Namespace.objects.filter(id=1, name="default").exists()

    def test_namespace_delete_with_files_raises_command_error(self):
        Namespace.objects.create(id=2, name="media")
        write_file(2, "/test.txt", b"data")

        out = StringIO()
        with pytest.raises(CommandError, match="contains files"):
            call_command("fsspec_namespace", "delete", "media", stdout=out)

        assert Namespace.objects.filter(name="media").exists()

    def test_stats_empty(self):
        out = StringIO()
        call_command("fsspec_stats", stdout=out)
        output = out.getvalue()
        assert "Files:            0" in output
        assert "Storage blocks:   0" in output

    def test_stats_with_data(self):
        write_file(1, "/test.txt", b"hello")
        write_file(1, "/test2.txt", b"world")

        out = StringIO()
        call_command("fsspec_stats", stdout=out)
        output = out.getvalue()
        assert "Files:            2" in output

    def test_stats_with_free_blocks(self):
        write_file(1, "/test.txt", b"data")
        delete_file(1, "/test.txt")

        out = StringIO()
        call_command("fsspec_stats", stdout=out)
        output = out.getvalue()
        assert "Free:           1" in output

    def test_stats_namespace_filter(self):
        write_file(1, "/test0.txt", b"ns0")
        from django_fsspec.models import Namespace
        Namespace.objects.create(id=2, name="other")
        write_file(2, "/test1.txt", b"ns2")

        out = StringIO()
        call_command("fsspec_stats", "--namespace=1", stdout=out)
        output = out.getvalue()
        assert "Files:            1" in output
        assert "Namespace:        1" in output


class TestFormatSize(TestCase):
    def test_bytes(self):
        from django_fsspec.management.commands.fsspec_stats import _format_size

        assert _format_size(500) == "500 B"

    def test_kilobytes(self):
        from django_fsspec.management.commands.fsspec_stats import _format_size

        assert "KB" in _format_size(2048)

    def test_megabytes(self):
        from django_fsspec.management.commands.fsspec_stats import _format_size

        assert "MB" in _format_size(2 * 1024 * 1024)

    def test_gigabytes(self):
        from django_fsspec.management.commands.fsspec_stats import _format_size

        assert "GB" in _format_size(2 * 1024 * 1024 * 1024)
