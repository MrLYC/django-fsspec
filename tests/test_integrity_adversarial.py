import hashlib
import json
import threading
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.test import TestCase, override_settings

from django_fsspec.exceptions import DataIntegrityError, FileConflictError
from django_fsspec.fs import DjangoFileSystem
from django_fsspec.models import (
    NODE_TYPE_DIRECTORY,
    NODE_TYPE_FILE,
    FileBlock,
    FileNode,
    StorageBlock,
)
from django_fsspec.operations import (
    copy_file,
    create_file_exclusive,
    delete_file,
    file_exists,
    list_directory_detail,
    make_directory,
    move_directory,
    read_file,
    read_file_range,
    write_file,
)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _create_raw_file(path, data, namespace_id=1):
    node = FileNode.objects.create(
        namespace_id=namespace_id,
        path=path,
        node_type=NODE_TYPE_FILE,
        size=len(data),
        block_size=256 * 1024,
        checksum=_sha256(data),
    )
    block = StorageBlock.objects.create(
        data=data,
        size=len(data),
        checksum=_sha256(data),
        is_free=False,
    )
    FileBlock.objects.create(file=node, block=block, sequence=0)
    return node


def _create_path_conflict():
    write_file(1, "/conflict/child.txt", b"child")
    return _create_raw_file("/conflict", b"parent")


def _create_shared_block_graph():
    primary = write_file(1, "/primary.txt", b"shared")
    block = FileBlock.objects.get(file=primary).block
    duplicate = FileNode.objects.create(
        namespace_id=1,
        path="/duplicate.txt",
        node_type=NODE_TYPE_FILE,
        size=primary.size,
        block_size=primary.block_size,
        checksum=primary.checksum,
    )
    FileBlock.objects.create(file=duplicate, block=block, sequence=0)
    return primary, duplicate, block


class TestAdversarialReadIntegrity(TestCase):
    def test_invalid_read_integrity_policy_is_rejected(self):
        write_file(1, "/policy.txt", b"data")

        with override_settings(DJANGO_FSSPEC_READ_INTEGRITY="invalid"):
            with pytest.raises(ValueError, match="DJANGO_FSSPEC_READ_INTEGRITY"):
                read_file(1, "/policy.txt")

    def test_reading_directory_as_file_raises(self):
        make_directory(1, "/dir")

        with pytest.raises(IsADirectoryError):
            read_file(1, "/dir")

    def test_checksum_read_detects_block_tampering(self):
        write_file(1, "/tampered.txt", b"original")
        block = StorageBlock.objects.get(is_free=False)
        block.checksum = "bad"
        block.save(update_fields=["checksum"])

        with pytest.raises(DataIntegrityError, match="Block .* checksum mismatch"):
            read_file(1, "/tampered.txt", integrity="checksum")

    def test_metadata_read_detects_free_referenced_block(self):
        write_file(1, "/free-block.txt", b"data")
        block = StorageBlock.objects.get(is_free=False)
        block.is_free = True
        block.save(update_fields=["is_free"])

        with pytest.raises(DataIntegrityError, match="references free block"):
            read_file(1, "/free-block.txt", integrity="metadata")

    def test_metadata_read_detects_block_size_tampering(self):
        write_file(1, "/block-size.txt", b"data")
        block = StorageBlock.objects.get(is_free=False)
        block.size = 999
        block.save(update_fields=["size"])

        with pytest.raises(DataIntegrityError, match="Block .* size mismatch"):
            read_file(1, "/block-size.txt", integrity="metadata")

    @override_settings(DJANGO_FSSPEC_BLOCK_SIZE=4)
    def test_metadata_read_detects_non_contiguous_sequences(self):
        write_file(1, "/sequences.bin", b"abcdefgh")
        node = FileNode.objects.get(path="/sequences.bin")
        second = FileBlock.objects.get(file=node, sequence=1)
        second.sequence = 5
        second.save(update_fields=["sequence"])

        with pytest.raises(DataIntegrityError, match="non-contiguous"):
            read_file(1, "/sequences.bin", integrity="metadata")

    @override_settings(DJANGO_FSSPEC_READ_INTEGRITY="metadata")
    def test_default_metadata_policy_detects_file_metadata_tampering(self):
        write_file(1, "/metadata.txt", b"content")
        node = FileNode.objects.get(path="/metadata.txt")
        node.size = 999
        node.save(update_fields=["size"])

        with pytest.raises(DataIntegrityError, match="size mismatch"):
            read_file(1, "/metadata.txt")

    def test_range_read_metadata_policy_detects_structural_damage(self):
        write_file(1, "/range.txt", b"abcdefgh")
        node = FileNode.objects.get(path="/range.txt")
        node.size = 3
        node.save(update_fields=["size"])

        with pytest.raises(DataIntegrityError, match="size mismatch"):
            read_file_range(1, "/range.txt", 0, 4, integrity="metadata")

    def test_range_read_metadata_policy_returns_requested_slice(self):
        write_file(1, "/range-ok.txt", b"abcdefgh")

        assert read_file_range(
            1,
            "/range-ok.txt",
            2,
            6,
            integrity="metadata",
        ) == b"cdef"

    def test_range_read_detects_invalid_block_size(self):
        write_file(1, "/bad-block-size.txt", b"abcdefgh")
        node = FileNode.objects.get(path="/bad-block-size.txt")
        node.block_size = 0
        node.save(update_fields=["block_size"])

        with pytest.raises(DataIntegrityError, match="invalid block size"):
            read_file_range(1, "/bad-block-size.txt", 0, 4)

    def test_copy_default_preserves_compatibility_when_checksum_is_stale(self):
        write_file(1, "/source.txt", b"source")
        block = StorageBlock.objects.get(is_free=False)
        block.checksum = "bad"
        block.save(update_fields=["checksum"])

        copy_file(1, "/source.txt", "/backup.txt")

        assert read_file(1, "/backup.txt") == b"source"

    def test_copy_can_refuse_corrupted_source_with_explicit_checksum_policy(self):
        write_file(1, "/source.txt", b"source")
        block = StorageBlock.objects.get(is_free=False)
        block.checksum = "bad"
        block.save(update_fields=["checksum"])

        with pytest.raises(DataIntegrityError, match="checksum mismatch"):
            copy_file(1, "/source.txt", "/backup.txt", integrity="checksum")

        assert not file_exists(1, "/backup.txt")

    def test_exclusive_create_maps_integrity_race_to_file_exists(self):
        with patch(
            "django_fsspec.operations.FileNode.objects.create",
            side_effect=IntegrityError,
        ):
            with pytest.raises(FileExistsError, match="already exists"):
                create_file_exclusive(1, "/race.txt", b"data")


class TestAdversarialListings(TestCase):
    def test_default_listing_does_not_validate_corrupt_child(self):
        write_file(1, "/dir/good.txt", b"good")
        write_file(1, "/dir/bad.txt", b"bad")
        bad = FileNode.objects.get(path="/dir/bad.txt")
        bad.size = 999
        bad.save(update_fields=["size"])

        entries = {
            entry["name"]: entry
            for entry in list_directory_detail(1, "/dir")
        }

        assert entries["/dir/good.txt"]["type"] == "file"
        assert entries["/dir/bad.txt"]["type"] == "file"
        assert entries["/dir/bad.txt"]["size"] == 999

    def test_tolerant_listing_marks_corrupt_child_and_keeps_good_entries(self):
        write_file(1, "/dir/good.txt", b"good")
        write_file(1, "/dir/bad.txt", b"bad")
        bad = FileNode.objects.get(path="/dir/bad.txt")
        bad.size = 999
        bad.save(update_fields=["size"])

        entries = {
            entry["name"]: entry
            for entry in list_directory_detail(1, "/dir", tolerant=True)
        }

        assert entries["/dir/good.txt"]["type"] == "file"
        assert entries["/dir/bad.txt"]["type"] == "corrupt"
        assert "size mismatch" in entries["/dir/bad.txt"]["error"]

    def test_fsspec_tolerant_listing_exposes_corrupt_marker(self):
        fs = DjangoFileSystem(namespace_id=1)
        fs.pipe("/dir/good.txt", b"good")
        fs.pipe("/dir/bad.txt", b"bad")
        bad = FileNode.objects.get(path="/dir/bad.txt")
        bad.size = 999
        bad.save(update_fields=["size"])

        entries = {entry["name"]: entry for entry in fs.ls("/dir", tolerant=True)}

        assert entries["/dir/good.txt"]["type"] == "file"
        assert entries["/dir/bad.txt"]["type"] == "corrupt"

    def test_tolerant_listing_marks_child_that_disappears_after_enumeration(self):
        write_file(1, "/dir/race.txt", b"race")

        def fake_info(namespace_id, path):
            if path == "/dir":
                return {"name": path, "size": 0, "type": "directory"}
            if path == "/dir/race.txt":
                return {"name": path, "size": 4, "type": "file"}
            raise FileNotFoundError(path)

        with patch("django_fsspec.operations.get_file_info", side_effect=fake_info):
            with patch(
                "django_fsspec.operations.FileNode.objects.get",
                side_effect=FileNode.DoesNotExist,
            ):
                entries = list_directory_detail(1, "/dir", tolerant=True)

        assert entries[0]["type"] == "corrupt"
        assert "disappeared" in entries[0]["error"]

    def test_default_listing_does_not_requery_file_child_after_enumeration(self):
        write_file(1, "/dir/race.txt", b"race")

        def fake_info(namespace_id, path):
            if path == "/dir":
                return {"name": path, "size": 0, "type": "directory"}
            if path == "/dir/race.txt":
                return {"name": path, "size": 4, "type": "file"}
            raise FileNotFoundError(path)

        with patch("django_fsspec.operations.get_file_info", side_effect=fake_info):
            with patch(
                "django_fsspec.operations.FileNode.objects.get",
                side_effect=FileNode.DoesNotExist,
            ):
                entries = list_directory_detail(1, "/dir")

        assert entries == [{"name": "/dir/race.txt", "size": 4, "type": "file"}]

    def test_listing_handles_file_not_found_after_enumeration(self):
        write_file(1, "/dir/race.txt", b"race")

        def fake_info(namespace_id, path):
            if path == "/dir":
                return {"name": path, "size": 0, "type": "directory"}
            if path == "/dir/race.txt":
                raise FileNotFoundError("gone")
            raise AssertionError(path)

        with patch("django_fsspec.operations.get_file_info", side_effect=fake_info):
            strict_entries = list_directory_detail(1, "/dir")
            tolerant_entries = list_directory_detail(1, "/dir", tolerant=True)

        assert strict_entries[0]["type"] == "directory"
        assert tolerant_entries[0]["type"] == "corrupt"


class TestAdversarialDestructiveOperations(TestCase):
    def test_overwrite_and_delete_do_not_damage_shared_block_owner(self):
        _, _, shared_block = _create_shared_block_graph()

        write_file(1, "/primary.txt", b"replacement")

        assert read_file(1, "/primary.txt", verify_checksum=True) == b"replacement"
        assert read_file(1, "/duplicate.txt", verify_checksum=True) == b"shared"
        shared_block.refresh_from_db()
        assert shared_block.is_free is False

        delete_file(1, "/primary.txt")

        assert not file_exists(1, "/primary.txt")
        assert read_file(1, "/duplicate.txt", verify_checksum=True) == b"shared"
        shared_block.refresh_from_db()
        assert shared_block.is_free is False

    def test_delete_removes_file_with_stale_size_metadata(self):
        write_file(1, "/stale.txt", b"data")
        node = FileNode.objects.get(path="/stale.txt")
        node.size = 0
        node.save(update_fields=["size"])

        delete_file(1, "/stale.txt")

        assert not file_exists(1, "/stale.txt")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    def test_overwrite_replaces_file_with_stale_size_metadata(self):
        write_file(1, "/stale.txt", b"data")
        node = FileNode.objects.get(path="/stale.txt")
        node.size = 0
        node.save(update_fields=["size"])

        write_file(1, "/stale.txt", b"replacement")

        assert read_file(1, "/stale.txt", verify_checksum=True) == b"replacement"

    def test_overwrite_refuses_file_path_with_descendants(self):
        _create_path_conflict()

        with pytest.raises(DataIntegrityError, match="descendants"):
            write_file(1, "/conflict", b"replacement")

        assert read_file(1, "/conflict") == b"parent"
        assert read_file(1, "/conflict/child.txt", verify_checksum=True) == b"child"

    def test_strict_read_refuses_file_path_with_descendants(self):
        _create_path_conflict()

        with pytest.raises(DataIntegrityError, match="descendant paths"):
            read_file(1, "/conflict", integrity="metadata")


class TestAdversarialDirectoryMove(TestCase):
    def test_move_directory_same_path_is_noop(self):
        write_file(1, "/src/a.txt", b"a")

        move_directory(1, "/src", "/src")

        assert read_file(1, "/src/a.txt") == b"a"

    def test_move_directory_rejects_move_into_self(self):
        write_file(1, "/src/a.txt", b"a")

        with pytest.raises(ValueError, match="into itself"):
            move_directory(1, "/src", "/src/nested")

    def test_move_directory_rejects_file_source(self):
        write_file(1, "/file.txt", b"data")

        with pytest.raises(NotADirectoryError, match="not a directory"):
            move_directory(1, "/file.txt", "/dst")

    def test_move_directory_missing_source_raises(self):
        with pytest.raises(FileNotFoundError, match="Path not found"):
            move_directory(1, "/missing", "/dst")

    def test_move_directory_existing_destination_requires_overwrite(self):
        write_file(1, "/src/a.txt", b"a")
        write_file(1, "/dst/old.txt", b"old")

        with pytest.raises(FileExistsError, match="Destination already exists"):
            move_directory(1, "/src", "/dst")

    def test_move_directory_overwrite_replaces_destination_tree(self):
        write_file(1, "/src/a.txt", b"a")
        write_file(1, "/dst/old.txt", b"old")

        move_directory(1, "/src", "/dst", overwrite=True)

        assert read_file(1, "/dst/a.txt") == b"a"
        assert not file_exists(1, "/dst/old.txt")
        assert not file_exists(1, "/src")
        assert StorageBlock.objects.filter(is_free=True).count() == 1

    def test_move_directory_overwrite_empty_explicit_destination(self):
        write_file(1, "/src/a.txt", b"a")
        make_directory(1, "/dst")

        move_directory(1, "/src", "/dst", overwrite=True)

        assert read_file(1, "/dst/a.txt") == b"a"


class TestAdversarialFsspecHandles(TestCase):
    def test_open_read_handle_detects_mid_stream_overwrite(self):
        fs = DjangoFileSystem(namespace_id=1)
        fs.pipe("/stream.txt", b"abcdefgh")

        handle = fs.open("/stream.txt", "rb", block_size=4)
        try:
            assert handle._fetch_range(0, 4) == b"abcd"
            write_file(1, "/stream.txt", b"replacement")

            with pytest.raises(FileConflictError, match="modified while reading"):
                handle._fetch_range(4, 8)
        finally:
            handle.close()

    def test_transaction_state_is_thread_local(self):
        fs = DjangoFileSystem(namespace_id=1)
        seen = {}

        def worker():
            fs._intrans = True
            seen["worker"] = fs._intrans

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert seen["worker"] is True
        assert fs._intrans is False

    def test_makedirs_existing_path_respects_exist_ok(self):
        fs = DjangoFileSystem(namespace_id=1)
        fs.mkdir("/exists")

        with pytest.raises(FileExistsError):
            fs.makedirs("/exists", exist_ok=False)

        fs.makedirs("/exists", exist_ok=True)


class TestAdversarialFsckAndRepair(TestCase):
    def test_fsck_json_reports_invalid_path_and_node_type(self):
        FileNode.objects.create(
            namespace_id=1,
            path="not-absolute",
            node_type="unexpected",
            size=0,
            checksum="",
        )

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", "--json", stdout=out)

        codes = {finding["code"] for finding in json.loads(out.getvalue())["findings"]}
        assert "invalid_path" in codes
        assert "invalid_node_type" in codes

    def test_fsck_json_reports_corrupt_directory_node(self):
        directory = FileNode.objects.create(
            namespace_id=1,
            path="/bad-dir",
            node_type=NODE_TYPE_DIRECTORY,
            size=3,
            checksum="bad",
        )
        block = StorageBlock.objects.create(
            data=b"dir",
            size=3,
            checksum=_sha256(b"dir"),
            is_free=False,
        )
        FileBlock.objects.create(file=directory, block=block, sequence=0)

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", "--json", stdout=out)

        codes = {finding["code"] for finding in json.loads(out.getvalue())["findings"]}
        assert "directory_has_blocks" in codes
        assert "directory_size_mismatch" in codes
        assert "directory_checksum_mismatch" in codes

    def test_fsck_json_reports_shared_and_ownerless_blocks(self):
        _create_shared_block_graph()
        StorageBlock.objects.create(
            data=b"lost",
            size=4,
            checksum=_sha256(b"lost"),
            is_free=False,
        )

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", "--json", stdout=out)

        codes = {finding["code"] for finding in json.loads(out.getvalue())["findings"]}
        assert "shared_storage_block" in codes
        assert "used_block_without_owner" in codes

    def test_fsck_json_reports_path_conflict_with_severity(self):
        _create_path_conflict()

        out = StringIO()
        with pytest.raises(CommandError):
            call_command("fsspec_fsck", "--json", stdout=out)

        payload = json.loads(out.getvalue())
        path_conflicts = [
            finding
            for finding in payload["findings"]
            if finding["code"] == "path_conflict"
        ]
        assert payload["ok"] is False
        assert path_conflicts[0]["severity"] == "unresolved"
        assert path_conflicts[0]["path"] == "/conflict"

    def test_repair_dry_run_reports_path_conflict_without_modifying_data(self):
        _create_path_conflict()

        out = StringIO()
        call_command("fsspec_repair", "--dry-run", stdout=out)

        output = out.getvalue()
        assert "path_conflicts: 1" in output
        assert "moved_descendants: 0" in output
        assert "Unresolved structural damage remains" in output
        assert read_file(1, "/conflict") == b"parent"
        assert read_file(1, "/conflict/child.txt", verify_checksum=True) == b"child"

    def test_repair_recovery_moves_descendants_out_of_file_path_conflict(self):
        _create_path_conflict()

        out = StringIO()
        call_command("fsspec_repair", "--recover-path-conflicts", stdout=out)

        moved = FileNode.objects.get(
            path__startswith="/__django_fsspec_recovered__/conflicts/1/",
            path__endswith="/conflict/child.txt",
        )
        assert "moved_descendants: 1" in out.getvalue()
        assert read_file(1, "/conflict", verify_checksum=True) == b"parent"
        assert read_file(1, moved.path, verify_checksum=True) == b"child"
        assert not FileNode.objects.filter(path="/conflict/child.txt").exists()

        verify = StringIO()
        call_command("fsspec_fsck", stdout=verify)
        assert "No errors found" in verify.getvalue()

    def test_repair_reports_shared_blocks_as_unresolved_damage(self):
        _create_shared_block_graph()

        out = StringIO()
        with pytest.raises(CommandError, match="Unresolved structural damage"):
            call_command("fsspec_repair", stdout=out)

        output = out.getvalue()
        assert "shared_blocks: 1" in output
        assert "No repair actions needed" in output

    def test_repair_raises_after_safe_repairs_when_unresolved_damage_remains(self):
        primary, _, _ = _create_shared_block_graph()
        primary.size = 999
        primary.save(update_fields=["size"])

        out = StringIO()
        with pytest.raises(CommandError, match="Unresolved structural damage"):
            call_command("fsspec_repair", stdout=out)

        assert "Applied" in out.getvalue()
        assert "shared_blocks: 1" in out.getvalue()

    def test_repair_dry_run_reports_invalid_persisted_paths(self):
        FileNode.objects.create(
            namespace_id=1,
            path="not-absolute",
            node_type=NODE_TYPE_FILE,
            size=0,
            checksum=_sha256(b""),
        )

        out = StringIO()
        call_command("fsspec_repair", "--dry-run", stdout=out)

        assert "invalid_paths: 1" in out.getvalue()
        assert "Unresolved structural damage remains" in out.getvalue()
