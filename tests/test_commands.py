from io import StringIO
import hashlib
import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from django_fsspec.models import (
    NODE_TYPE_DIRECTORY,
    FileBlock,
    FileNode,
    Namespace,
    StorageBlock,
    get_block_size,
)
from django_fsspec.management.commands import fsspec_rechunk as rechunk_command
from django_fsspec.operations import delete_file, read_file, write_file


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


class TestFsspecRechunk(TestCase):
    def test_rechunk_basic(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            data = b"A" * 350
            write_file(1, "/rechunk/basic.bin", data)

        node = FileNode.objects.get(path="/rechunk/basic.bin")
        assert node.block_size == 100
        assert FileBlock.objects.filter(file=node).count() == 4
        old_block_count = StorageBlock.objects.filter(is_free=False).count()

        out = StringIO()
        call_command("fsspec_rechunk", "--block-size=500", stdout=out)

        node.refresh_from_db()
        assert node.block_size == 500
        assert node.version == 2
        assert FileBlock.objects.filter(file=node).count() == 1
        assert StorageBlock.objects.filter(is_free=True).count() == old_block_count
        assert read_file(1, "/rechunk/basic.bin", verify_checksum=True) == data
        assert "Rechunked 1 file(s)" in out.getvalue()

    def test_rechunk_json_success(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/json.bin", b"A" * 350)

        out = StringIO()
        call_command("fsspec_rechunk", "--block-size=500", "--json", stdout=out)

        payload = json.loads(out.getvalue())
        assert payload["ok"] is True
        assert payload["dry_run"] is False
        assert payload["skipped"] == []
        assert payload["summary"]["files_rechunked"] == 1
        assert payload["summary"]["files_skipped"] == 0

    def test_rechunk_dry_run_does_not_modify_rows(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/dry-run.bin", b"B" * 350)

        node = FileNode.objects.get(path="/rechunk/dry-run.bin")
        active_before = StorageBlock.objects.filter(is_free=False).count()

        out = StringIO()
        call_command(
            "fsspec_rechunk",
            "--block-size=500",
            "--dry-run",
            stdout=out,
        )

        node.refresh_from_db()
        assert node.block_size == 100
        assert FileBlock.objects.filter(file=node).count() == 4
        assert StorageBlock.objects.filter(is_free=True).count() == 0
        assert StorageBlock.objects.filter(is_free=False).count() == active_before
        assert "Would rechunk 1 file(s)" in out.getvalue()

    def test_rechunk_json_dry_run_does_not_modify_rows(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/json-dry-run.bin", b"B" * 350)

        node = FileNode.objects.get(path="/rechunk/json-dry-run.bin")
        out = StringIO()
        call_command(
            "fsspec_rechunk",
            "--block-size=500",
            "--dry-run",
            "--json",
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        node.refresh_from_db()
        assert payload["ok"] is True
        assert payload["dry_run"] is True
        assert payload["summary"]["files_rechunked"] == 1
        assert node.block_size == 100

    def test_rechunk_filters_namespace_prefix_source_and_limit(self):
        Namespace.objects.create(id=2, name="tenant-2")
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/scope/a.bin", b"a" * 150)
            write_file(1, "/scope/b.bin", b"b" * 150)
            write_file(1, "/other/c.bin", b"c" * 150)
            write_file(2, "/scope/d.bin", b"d" * 150)
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=200):
            write_file(1, "/scope/source-mismatch.bin", b"e" * 150)

        call_command(
            "fsspec_rechunk",
            "--block-size=500",
            "--namespace=1",
            "--prefix=/scope",
            "--source-block-size=100",
            "--limit=1",
            stdout=StringIO(),
        )

        assert FileNode.objects.get(namespace_id=1, path="/scope/a.bin").block_size == 500
        assert FileNode.objects.get(namespace_id=1, path="/scope/b.bin").block_size == 100
        assert FileNode.objects.get(namespace_id=1, path="/other/c.bin").block_size == 100
        assert FileNode.objects.get(namespace_id=2, path="/scope/d.bin").block_size == 100
        assert (
            FileNode.objects.get(
                namespace_id=1,
                path="/scope/source-mismatch.bin",
            ).block_size
            == 200
        )

    def test_rechunk_empty_file(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/empty.txt", b"")

        call_command("fsspec_rechunk", "--block-size=500", stdout=StringIO())

        node = FileNode.objects.get(path="/rechunk/empty.txt")
        assert node.block_size == 500
        assert FileBlock.objects.filter(file=node).count() == 0
        assert read_file(1, "/rechunk/empty.txt", verify_checksum=True) == b""

    def test_rechunk_skips_structural_damage_by_default(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/damaged.bin", b"x" * 101)

        node = FileNode.objects.get(path="/rechunk/damaged.bin")
        damaged = FileBlock.objects.filter(file=node).order_by("sequence").last()
        damaged.sequence = 5
        damaged.save(update_fields=["sequence"])

        out = StringIO()
        with pytest.raises(CommandError) as exc_info:
            call_command("fsspec_rechunk", "--block-size=50", stdout=out)

        node.refresh_from_db()
        assert exc_info.value.returncode == 1
        assert node.block_size == 100
        assert "errors: 1" in out.getvalue()
        assert "Skipped FileNode" in out.getvalue()

    def test_rechunk_json_reports_skipped_damage(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/json-damaged.bin", b"x" * 101)

        node = FileNode.objects.get(path="/rechunk/json-damaged.bin")
        damaged = FileBlock.objects.filter(file=node).order_by("sequence").last()
        damaged.sequence = 5
        damaged.save(update_fields=["sequence"])

        out = StringIO()
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "fsspec_rechunk",
                "--block-size=50",
                "--json",
                stdout=out,
            )

        payload = json.loads(out.getvalue())
        assert exc_info.value.returncode == 1
        assert payload["ok"] is False
        assert payload["summary"]["errors"] == 1
        assert payload["summary"]["files_skipped"] == 1
        assert payload["skipped"][0]["file_id"] == node.id
        assert payload["skipped"][0]["error"] == "DataIntegrityError"

    def test_rechunk_checksum_verify_skips_checksum_damage(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/checksum.bin", b"checksum")

        node = FileNode.objects.get(path="/rechunk/checksum.bin")
        block = FileBlock.objects.get(file=node).block
        block.checksum = "bad"
        block.save(update_fields=["checksum"])

        out = StringIO()
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "fsspec_rechunk",
                "--block-size=50",
                "--verify=checksum",
                stdout=out,
            )

        node.refresh_from_db()
        assert exc_info.value.returncode == 1
        assert node.block_size == 100
        assert "errors: 1" in out.getvalue()

    def test_rechunk_abort_raises_on_damage(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/abort.bin", b"x" * 101)

        node = FileNode.objects.get(path="/rechunk/abort.bin")
        damaged = FileBlock.objects.filter(file=node).order_by("sequence").last()
        damaged.sequence = 5
        damaged.save(update_fields=["sequence"])

        with pytest.raises(CommandError) as exc_info:
            call_command(
                "fsspec_rechunk",
                "--block-size=50",
                "--on-error=abort",
                stdout=StringIO(),
            )
        assert exc_info.value.returncode == 2

    def test_rechunk_skips_version_conflict(self):
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/rechunk/conflict.bin", b"conflict")

        original_load = rechunk_command._load_file_data

        def mutate_version(file_node, *args, **kwargs):
            data = original_load(file_node, *args, **kwargs)
            FileNode.objects.filter(pk=file_node.pk).update(
                version=file_node.version + 1
            )
            return data

        out = StringIO()
        with patch.object(rechunk_command, "_load_file_data", mutate_version):
            with pytest.raises(CommandError) as exc_info:
                call_command("fsspec_rechunk", "--block-size=50", stdout=out)

        node = FileNode.objects.get(path="/rechunk/conflict.bin")
        assert exc_info.value.returncode == 1
        assert node.block_size == 100
        assert node.version == 1
        assert "errors: 1" in out.getvalue()

    def test_rechunk_rejects_invalid_block_size(self):
        with pytest.raises(CommandError) as exc_info:
            call_command("fsspec_rechunk", "--block-size=0", stdout=StringIO())
        assert exc_info.value.returncode == 2


class TestFsspecFsck(TestCase):
    def test_fsck_healthy(self):
        write_file(1, "/test.txt", b"hello world")
        out = StringIO()
        call_command("fsspec_fsck", stdout=out)
        assert "No errors found" in out.getvalue()

    def test_fsck_json_healthy(self):
        write_file(1, "/json-healthy.txt", b"hello world")
        out = StringIO()
        call_command("fsspec_fsck", "--json", stdout=out)

        payload = json.loads(out.getvalue())
        assert payload == {"ok": True, "findings": []}

    def test_fsck_corrupted_block_checksum(self):
        write_file(1, "/test.txt", b"hello")
        block = StorageBlock.objects.filter(is_free=False).first()
        block.checksum = "bad_checksum"
        block.save(update_fields=["checksum"])

        out = StringIO()
        with pytest.raises(CommandError) as exc_info:
            call_command("fsspec_fsck", stdout=out)
        assert exc_info.value.returncode == 1
        assert "checksum mismatch" in out.getvalue()

    def test_fsck_json_reports_findings(self):
        write_file(1, "/json-corrupt.txt", b"hello")
        block = StorageBlock.objects.filter(is_free=False).first()
        block.checksum = "bad_checksum"
        block.save(update_fields=["checksum"])

        out = StringIO()
        with pytest.raises(CommandError) as exc_info:
            call_command("fsspec_fsck", "--json", stdout=out)

        payload = json.loads(out.getvalue())
        assert exc_info.value.returncode == 1
        assert payload["ok"] is False
        assert payload["findings"][0]["code"] == "block_checksum_mismatch"

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


class TestFsspecRepair(TestCase):
    def test_repair_metadata_tampering(self):
        write_file(1, "/tampered.txt", b"hello")
        node = FileNode.objects.get(path="/tampered.txt")
        block = FileBlock.objects.get(file=node).block

        # Attacker role: corrupt derived metadata while leaving bytes intact.
        block.size = 999
        block.checksum = "bad_block_checksum"
        block.save(update_fields=["size", "checksum"])
        node.size = 999
        node.checksum = "bad_file_checksum"
        node.save(update_fields=["size", "checksum"])

        out = StringIO()
        call_command("fsspec_repair", stdout=out)

        block.refresh_from_db()
        node.refresh_from_db()
        assert block.size == 5
        assert block.checksum == hashlib.sha256(b"hello").hexdigest()
        assert node.size == 5
        assert node.checksum == hashlib.sha256(b"hello").hexdigest()
        assert read_file(1, "/tampered.txt", verify_checksum=True) == b"hello"

        verify = StringIO()
        call_command("fsspec_fsck", stdout=verify)
        assert "No errors found" in verify.getvalue()

    def test_repair_free_block_reference_and_sequence_gap(self):
        data = b"a" * get_block_size() + b"b"
        write_file(1, "/sequence.bin", data)
        node = FileNode.objects.get(path="/sequence.bin")
        file_blocks = list(FileBlock.objects.filter(file=node).order_by("sequence"))
        assert len(file_blocks) == 2

        # Attacker role: mark live storage free and damage block ordering metadata.
        file_blocks[1].block.is_free = True
        file_blocks[1].block.save(update_fields=["is_free"])
        file_blocks[1].sequence = 5
        file_blocks[1].save(update_fields=["sequence"])

        out = StringIO()
        call_command("fsspec_repair", stdout=out)

        assert list(
            FileBlock.objects.filter(file=node).values_list("sequence", flat=True)
        ) == [0, 1]
        assert not StorageBlock.objects.filter(
            id=file_blocks[1].block_id, is_free=True
        ).exists()
        assert read_file(1, "/sequence.bin", verify_checksum=True) == data

        verify = StringIO()
        call_command("fsspec_fsck", stdout=verify)
        assert "No errors found" in verify.getvalue()

    def test_repair_deleted_mapping_preserves_consistency_not_lost_bytes(self):
        write_file(1, "/mapping-lost.txt", b"lost")
        node = FileNode.objects.get(path="/mapping-lost.txt")
        block_id = FileBlock.objects.get(file=node).block_id

        # Attacker role: delete the only file-to-block mapping. The old bytes still
        # exist in StorageBlock, but there is no trustworthy path ownership left.
        FileBlock.objects.filter(file=node).delete()

        out = StringIO()
        call_command("fsspec_repair", stdout=out)

        node.refresh_from_db()
        assert node.size == 0
        assert node.checksum == hashlib.sha256(b"").hexdigest()
        assert read_file(1, "/mapping-lost.txt", verify_checksum=True) == b""
        assert StorageBlock.objects.get(id=block_id).is_free

        verify = StringIO()
        call_command("fsspec_fsck", stdout=verify)
        assert "No errors found" in verify.getvalue()

    def test_repair_dry_run_does_not_modify_data(self):
        write_file(1, "/dry-run.txt", b"hello")
        node = FileNode.objects.get(path="/dry-run.txt")
        node.size = 999
        node.save(update_fields=["size"])

        out = StringIO()
        call_command("fsspec_repair", "--dry-run", stdout=out)

        node.refresh_from_db()
        assert node.size == 999
        assert "Would apply" in out.getvalue()

        with pytest.raises(CommandError):
            call_command("fsspec_fsck", stdout=StringIO())

    def test_repair_json_dry_run_does_not_modify_data(self):
        write_file(1, "/json-dry-run.txt", b"hello")
        node = FileNode.objects.get(path="/json-dry-run.txt")
        node.size = 999
        node.save(update_fields=["size"])

        out = StringIO()
        call_command("fsspec_repair", "--dry-run", "--json", stdout=out)

        payload = json.loads(out.getvalue())
        node.refresh_from_db()
        assert payload["ok"] is True
        assert payload["dry_run"] is True
        assert payload["unresolved"] is False
        assert payload["summary"]["file_metadata"] == 1
        assert node.size == 999

    def test_repair_healthy_filesystem_noop(self):
        write_file(1, "/healthy.txt", b"ok")

        out = StringIO()
        call_command("fsspec_repair", stdout=out)

        assert "No repair actions needed" in out.getvalue()
        assert read_file(1, "/healthy.txt", verify_checksum=True) == b"ok"

    def test_repair_empty_file_metadata(self):
        write_file(1, "/empty.txt", b"")
        node = FileNode.objects.get(path="/empty.txt")
        node.size = 10
        node.checksum = "bad"
        node.save(update_fields=["size", "checksum"])

        call_command("fsspec_repair", stdout=StringIO())

        assert read_file(1, "/empty.txt", verify_checksum=True) == b""

    def test_repair_mixed_adversarial_damage(self):
        Namespace.objects.create(id=2, name="tenant-2")
        large_data = b"a" * get_block_size() + b"b"
        write_file(1, "/meta.txt", b"metadata")
        write_file(1, "/large.bin", large_data)
        write_file(1, "/lost.txt", b"lost")
        write_file(2, "/tenant.txt", b"tenant")

        # Writer role: files were valid before the incident.
        meta = FileNode.objects.get(namespace_id=1, path="/meta.txt")
        large = FileNode.objects.get(namespace_id=1, path="/large.bin")
        lost = FileNode.objects.get(namespace_id=1, path="/lost.txt")
        tenant = FileNode.objects.get(namespace_id=2, path="/tenant.txt")

        # Metadata attacker role: corrupt derived metadata only.
        meta_block = FileBlock.objects.get(file=meta).block
        meta_block.size = 1
        meta_block.checksum = "bad"
        meta_block.save(update_fields=["size", "checksum"])
        meta.size = 1
        meta.checksum = "bad"
        meta.save(update_fields=["size", "checksum"])

        # Block-pool attacker role: make a live block look reclaimable and damage
        # its file ordering.
        large_blocks = list(FileBlock.objects.filter(file=large).order_by("sequence"))
        large_blocks[1].block.is_free = True
        large_blocks[1].block.save(update_fields=["is_free"])
        large_blocks[1].sequence = 7
        large_blocks[1].save(update_fields=["sequence"])

        # Mapping attacker role: delete the only path-to-block link.
        lost_block_id = FileBlock.objects.get(file=lost).block_id
        FileBlock.objects.filter(file=lost).delete()

        # Schema attacker role: create impossible directory payload state.
        directory = FileNode.objects.create(
            namespace_id=1,
            path="/fake-dir",
            node_type=NODE_TYPE_DIRECTORY,
            size=3,
            checksum="bad",
        )
        directory_block = StorageBlock.objects.create(
            data=b"dir",
            size=3,
            checksum=hashlib.sha256(b"dir").hexdigest(),
            is_free=False,
        )
        FileBlock.objects.create(file=directory, block=directory_block, sequence=0)

        # Tenant attacker role: corrupt another namespace in the same repair run.
        tenant.size = 1
        tenant.checksum = "bad"
        tenant.save(update_fields=["size", "checksum"])

        out = StringIO()
        call_command("fsspec_repair", stdout=out)

        assert read_file(1, "/meta.txt", verify_checksum=True) == b"metadata"
        assert read_file(1, "/large.bin", verify_checksum=True) == large_data
        assert read_file(1, "/lost.txt", verify_checksum=True) == b""
        assert read_file(2, "/tenant.txt", verify_checksum=True) == b"tenant"
        assert StorageBlock.objects.get(id=lost_block_id).is_free

        directory.refresh_from_db()
        directory_block.refresh_from_db()
        assert directory.size == 0
        assert directory.checksum == ""
        assert directory_block.is_free

        verify = StringIO()
        call_command("fsspec_fsck", stdout=verify)
        assert "No errors found" in verify.getvalue()

    def test_repair_namespace_scope(self):
        Namespace.objects.create(id=2, name="tenant-2")
        write_file(1, "/local.txt", b"local")
        write_file(2, "/remote.txt", b"remote")

        local = FileNode.objects.get(namespace_id=1, path="/local.txt")
        remote = FileNode.objects.get(namespace_id=2, path="/remote.txt")
        local.size = 99
        local.save(update_fields=["size"])
        remote.size = 99
        remote.save(update_fields=["size"])

        call_command("fsspec_repair", "--namespace=1", stdout=StringIO())

        assert read_file(1, "/local.txt", verify_checksum=True) == b"local"
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", "--namespace=2", stdout=StringIO())

    def test_repair_json_unresolved_damage_returns_attention(self):
        write_file(1, "/conflict.txt", b"file")
        FileNode.objects.create(
            namespace_id=1,
            path="/conflict.txt/child",
            node_type=NODE_TYPE_DIRECTORY,
        )

        out = StringIO()
        with pytest.raises(CommandError) as exc_info:
            call_command("fsspec_repair", "--dry-run", "--json", stdout=out)

        payload = json.loads(out.getvalue())
        assert exc_info.value.returncode == 1
        assert payload["ok"] is False
        assert payload["unresolved"] is True
        assert payload["summary"]["path_conflicts"] == 1


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
