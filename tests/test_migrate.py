from io import StringIO
import json
import os
import tempfile
from unittest.mock import patch

import fsspec
import pytest
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from fsspec.core import url_to_fs

from django_fsspec.models import FileNode, Namespace
from django_fsspec.management.commands.fsspec_migrate import (
    parse_json_options,
    positive_int,
)
from django_fsspec.operations import read_file, write_file


class TestDjangoUrlSupport(TestCase):
    def test_url_to_fs_uses_namespace_host_and_strips_path(self):
        fs, path = url_to_fs("django://1/url/file.txt")

        assert fs.namespace == 1
        assert path == "/url/file.txt"

    def test_fsspec_open_supports_django_namespace_url(self):
        Namespace.objects.create(id=2, name="tenant-2")

        with fsspec.open("django://2/url/file.txt", "wb") as f:
            f.write(b"tenant")
        with fsspec.open("django://2/url/file.txt", "rb") as f:
            assert f.read() == b"tenant"

        assert read_file(2, "/url/file.txt") == b"tenant"

    def test_django_url_without_namespace_uses_default_namespace(self):
        with fsspec.open("django:///url/default.txt", "wb") as f:
            f.write(b"default")

        assert read_file(1, "/url/default.txt") == b"default"

    def test_django_url_rejects_non_integer_namespace(self):
        with pytest.raises(ValueError, match="namespace must be an integer"):
            url_to_fs("django://default/url/file.txt")


class TestFsspecMigrateCommand(TestCase):
    def test_option_helpers_validate_inputs(self):
        assert parse_json_options("", "--source-options") == {}
        assert positive_int("3") == 3

        with pytest.raises(CommandError) as exc_info:
            positive_int("0")
        assert exc_info.value.returncode == 2

        with pytest.raises(CommandError) as exc_info:
            parse_json_options("[]", "--source-options")
        assert exc_info.value.returncode == 2

    def test_local_to_django_copies_tree_and_generates_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            manifest_dir = os.path.join(tmp, "manifests")
            os.makedirs(os.path.join(src, "nested"))
            os.makedirs(os.path.join(src, "empty"))
            with open(os.path.join(src, "root.txt"), "wb") as f:
                f.write(b"root")
            with open(os.path.join(src, "nested", "child.txt"), "wb") as f:
                f.write(b"child")

            with override_settings(DJANGO_FSSPEC_MIGRATE_MANIFEST_DIR=manifest_dir):
                call_command(
                    "fsspec_migrate",
                    f"file://{src}/",
                    "django://1/imports/",
                    stdout=StringIO(),
                )

            assert read_file(1, "/imports/root.txt") == b"root"
            assert read_file(1, "/imports/nested/child.txt") == b"child"
            assert FileNode.objects.get(path="/imports/empty").node_type == "directory"

            manifests = os.listdir(manifest_dir)
            assert len(manifests) == 1
            manifest_path = os.path.join(manifest_dir, manifests[0])
            lines = [
                json.loads(line)
                for line in open(manifest_path, encoding="utf-8")
                if line.strip()
            ]
            assert lines[0]["kind"] == "run"
            assert "source_options" not in lines[0]
            assert "target_options" not in lines[0]
            assert any(
                line.get("status") == "copied"
                and line.get("target") == "/imports/root.txt"
                for line in lines
            )

    def test_django_to_local_copies_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "dst")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/exports/root.txt", b"root")
            write_file(1, "/exports/nested/child.txt", b"child")

            call_command(
                "fsspec_migrate",
                "django://1/exports/",
                f"file://{dst}/",
                "--manifest",
                manifest,
                stdout=StringIO(),
            )

            with open(os.path.join(dst, "root.txt"), "rb") as f:
                assert f.read() == b"root"
            with open(os.path.join(dst, "nested", "child.txt"), "rb") as f:
                assert f.read() == b"child"
            assert not os.path.exists(os.path.join(dst, ".django-fsspec-migrate-tmp"))

    def test_existing_target_is_skipped_by_default_and_reported_as_attention(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target.txt")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/source.txt", b"new")
            with open(target, "wb") as f:
                f.write(b"old")

            out = StringIO()
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{target}",
                    "--json",
                    "--manifest",
                    manifest,
                    stdout=out,
                )

            assert exc_info.value.returncode == 1
            payload = json.loads(out.getvalue())
            assert payload["ok"] is False
            assert payload["summary"]["files_skipped"] == 1
            with open(target, "rb") as f:
                assert f.read() == b"old"

    def test_checksum_conflict_policy_skips_identical_target_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target.txt")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/source.txt", b"same")
            with open(target, "wb") as f:
                f.write(b"same")

            out = StringIO()
            call_command(
                "fsspec_migrate",
                "django://1/source.txt",
                f"file://{target}",
                "--conflict=checksum",
                "--json",
                "--manifest",
                manifest,
                stdout=out,
            )

            payload = json.loads(out.getvalue())
            assert payload["ok"] is True
            assert payload["summary"]["files_skipped"] == 0
            lines = [
                json.loads(line)
                for line in open(manifest, encoding="utf-8")
                if line.strip()
            ]
            assert any(line.get("status") == "skipped_same_checksum" for line in lines)

    def test_overwrite_replaces_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target.txt")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/source.txt", b"new")
            with open(target, "wb") as f:
                f.write(b"old")

            call_command(
                "fsspec_migrate",
                "django://1/source.txt",
                f"file://{target}",
                "--conflict=overwrite",
                "--manifest",
                manifest,
                stdout=StringIO(),
            )

            with open(target, "rb") as f:
                assert f.read() == b"new"

    def test_dry_run_writes_nothing_and_no_default_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "dst")
            manifest_dir = os.path.join(tmp, "manifests")
            write_file(1, "/source.txt", b"data")

            with override_settings(DJANGO_FSSPEC_MIGRATE_MANIFEST_DIR=manifest_dir):
                out = StringIO()
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{dst}/",
                    "--dry-run",
                    "--json",
                    stdout=out,
                )

            payload = json.loads(out.getvalue())
            assert payload["dry_run"] is True
            assert not os.path.exists(dst)
            assert not os.path.exists(manifest_dir)

    def test_resume_skips_successful_manifest_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "dst")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/source.txt", b"data")

            call_command(
                "fsspec_migrate",
                "django://1/source.txt",
                f"file://{dst}/",
                "--manifest",
                manifest,
                stdout=StringIO(),
            )
            out = StringIO()
            call_command(
                "fsspec_migrate",
                "django://1/source.txt",
                f"file://{dst}/",
                "--resume",
                manifest,
                "--json",
                stdout=out,
            )

            payload = json.loads(out.getvalue())
            assert payload["ok"] is True
            assert payload["summary"]["entries_resumed"] == 1
            assert payload["summary"]["files_skipped"] == 0

    def test_invalid_options_json_returns_failure_code(self):
        out = StringIO()
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "fsspec_migrate",
                "django://1/source.txt",
                "file:///tmp/target.txt",
                "--source-options",
                "{bad",
                stdout=out,
            )

        assert exc_info.value.returncode == 2

    def test_invalid_url_returns_failure_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://bad/source.txt",
                    f"file://{tmp}/target.txt",
                    stdout=StringIO(),
                )

        assert exc_info.value.returncode == 2

    def test_missing_source_returns_failure_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/missing.txt",
                    f"file://{tmp}/target.txt",
                    stdout=StringIO(),
                )

        assert exc_info.value.returncode == 2

    def test_no_temp_verify_size_and_existing_target_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = os.path.join(tmp, "target")
            manifest = os.path.join(tmp, "manifest.jsonl")
            os.makedirs(target_dir)
            write_file(1, "/source.txt", b"data")

            call_command(
                "fsspec_migrate",
                "django://1/source.txt",
                f"file://{target_dir}",
                "--verify=size",
                "--no-temp",
                "--manifest",
                manifest,
                stdout=StringIO(),
            )

            with open(os.path.join(target_dir, "source.txt"), "rb") as f:
                assert f.read() == b"data"

    def test_verify_off_uses_default_manifest_under_base_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target.txt")
            write_file(1, "/source.txt", b"data")

            with override_settings(BASE_DIR=tmp):
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{target}",
                    "--verify=off",
                    stdout=StringIO(),
                )

            assert os.path.exists(os.path.join(tmp, ".django-fsspec-migrate"))
            with open(target, "rb") as f:
                assert f.read() == b"data"

    def test_conflict_fail_aborts_with_attention_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target.txt")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/source.txt", b"new")
            with open(target, "wb") as f:
                f.write(b"old")

            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{target}",
                    "--conflict=fail",
                    "--manifest",
                    manifest,
                    stdout=StringIO(),
                )

            assert exc_info.value.returncode == 1
            lines = [
                json.loads(line)
                for line in open(manifest, encoding="utf-8")
                if line.strip()
            ]
            assert any(line.get("status") == "conflict" for line in lines)

    def test_checksum_conflict_policy_reports_different_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target.txt")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/source.txt", b"new")
            with open(target, "wb") as f:
                f.write(b"old")

            out = StringIO()
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{target}",
                    "--conflict=checksum",
                    "--json",
                    "--manifest",
                    manifest,
                    stdout=out,
                )

            assert exc_info.value.returncode == 1
            payload = json.loads(out.getvalue())
            assert payload["summary"]["conflicts"] == 1

    def test_directory_target_conflict_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source")
            target = os.path.join(tmp, "target")
            manifest = os.path.join(tmp, "manifest.jsonl")
            os.makedirs(source)
            with open(os.path.join(source, "child.txt"), "wb") as f:
                f.write(b"child")
            with open(target, "wb") as f:
                f.write(b"not a directory")

            out = StringIO()
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    f"file://{source}/",
                    f"file://{target}/",
                    "--json",
                    "--manifest",
                    manifest,
                    stdout=out,
                )

            assert exc_info.value.returncode == 1
            payload = json.loads(out.getvalue())
            assert payload["summary"]["conflicts"] == 1

    def test_resume_manifest_must_exist_and_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing.jsonl")
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{tmp}/target.txt",
                    "--resume",
                    missing,
                    stdout=StringIO(),
                )
            assert exc_info.value.returncode == 2

            manifest = os.path.join(tmp, "manifest.jsonl")
            with open(manifest, "w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "run", "source_uri": "django://1/a", "target_uri": "file:///a"}) + "\n")
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{tmp}/target.txt",
                    "--resume",
                    manifest,
                    stdout=StringIO(),
                )
            assert exc_info.value.returncode == 2

    def test_resume_manifest_requires_run_metadata_and_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            no_run = os.path.join(tmp, "no-run.jsonl")
            with open(no_run, "w", encoding="utf-8") as f:
                f.write(json.dumps({"kind": "entry", "status": "copied"}) + "\n")
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{tmp}/target.txt",
                    "--resume",
                    no_run,
                    stdout=StringIO(),
                )
            assert exc_info.value.returncode == 2

            invalid = os.path.join(tmp, "invalid.jsonl")
            with open(invalid, "w", encoding="utf-8") as f:
                f.write("{bad\n")
            with pytest.raises(CommandError) as exc_info:
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{tmp}/target.txt",
                    "--resume",
                    invalid,
                    stdout=StringIO(),
                )
            assert exc_info.value.returncode == 2

    def test_move_unsupported_falls_back_to_direct_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target.txt")
            manifest = os.path.join(tmp, "manifest.jsonl")
            write_file(1, "/source.txt", b"data")

            with patch(
                "django_fsspec.management.commands.fsspec_migrate.Command._move_temp",
                side_effect=NotImplementedError,
            ):
                call_command(
                    "fsspec_migrate",
                    "django://1/source.txt",
                    f"file://{target}",
                    "--manifest",
                    manifest,
                    stdout=StringIO(),
                )

            with open(target, "rb") as f:
                assert f.read() == b"data"
