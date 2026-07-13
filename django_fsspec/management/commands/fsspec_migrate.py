import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from fsspec.core import url_to_fs


EXIT_ATTENTION = 1
EXIT_FAILURE = 2
COPY_CHUNK_SIZE = 1024 * 1024
SUCCESS_STATUSES = {
    "copied",
    "created_dir",
    "dir_exists",
    "skipped_same_checksum",
}


def positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise CommandError(
            f"Expected a positive integer, got {value!r}",
            returncode=EXIT_FAILURE,
        )
    if number <= 0:
        raise CommandError(
            f"Expected a positive integer, got {value!r}",
            returncode=EXIT_FAILURE,
        )
    return number


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_json_options(value, label):
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise CommandError(
            f"{label} must be valid JSON: {exc}",
            returncode=EXIT_FAILURE,
        ) from exc
    if not isinstance(parsed, dict):
        raise CommandError(
            f"{label} must be a JSON object",
            returncode=EXIT_FAILURE,
        )
    return parsed


@dataclass
class MigrationEntry:
    source: str
    target: str
    type: str
    size: int | None = None


@dataclass
class MigrationResult:
    status: str
    size: int = 0
    sha256: str = ""
    error: str = ""


class MigrationConflict(Exception):
    pass


class Command(BaseCommand):
    help = "Copy files between fsspec-compatible filesystems"

    def add_arguments(self, parser):
        parser.add_argument("source_uri", help="Source fsspec URI")
        parser.add_argument("target_uri", help="Target fsspec URI")
        parser.add_argument(
            "--source-options",
            default="{}",
            help="JSON object passed to the source fsspec filesystem",
        )
        parser.add_argument(
            "--target-options",
            default="{}",
            help="JSON object passed to the target fsspec filesystem",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview migration without writing target files",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable summary",
        )
        parser.add_argument(
            "--limit",
            type=positive_int,
            default=None,
            help="Maximum number of entries to process",
        )
        parser.add_argument(
            "--on-error",
            choices=["skip", "abort"],
            default="skip",
            help="Whether to skip failed entries or abort the run",
        )
        parser.add_argument(
            "--conflict",
            choices=["skip", "fail", "overwrite", "checksum"],
            default="skip",
            help="How to handle existing target files",
        )
        parser.add_argument(
            "--verify",
            choices=["checksum", "size", "off"],
            default="checksum",
            help="Verification policy after copying each file",
        )
        parser.add_argument(
            "--manifest",
            default=None,
            help="Write JSONL migration manifest to this path",
        )
        parser.add_argument(
            "--resume",
            default=None,
            help="Resume from an existing manifest",
        )
        parser.add_argument(
            "--no-temp",
            action="store_true",
            help="Write directly to final target paths instead of temp paths",
        )

    def handle(self, *args, **options):
        source_uri = options["source_uri"]
        target_uri = options["target_uri"]
        dry_run = options["dry_run"]
        json_output = options["json"]
        limit = options["limit"]
        on_error = options["on_error"]
        conflict = options["conflict"]
        verify = options["verify"]
        no_temp = options["no_temp"]
        run_id = uuid.uuid4().hex

        source_options = parse_json_options(options["source_options"], "--source-options")
        target_options = parse_json_options(options["target_options"], "--target-options")

        try:
            source_fs, source_path = url_to_fs(source_uri, **source_options)
            target_fs, target_path = url_to_fs(target_uri, **target_options)
        except Exception as exc:
            raise CommandError(
                f"Could not resolve source or target URI: {exc}",
                returncode=EXIT_FAILURE,
            ) from exc

        resume_path = options["resume"]
        completed = set()
        resume_metadata = None
        if resume_path:
            resume_metadata, completed = self._read_resume_manifest(
                resume_path,
                source_uri=source_uri,
                target_uri=target_uri,
            )

        manifest_path = None
        manifest_file = None
        if not dry_run:
            manifest_path = (
                options["manifest"]
                or resume_path
                or self._default_manifest_path(run_id)
            )
            self._ensure_parent_dir(manifest_path)
            append = bool(resume_path and manifest_path == resume_path)
            manifest_file = open(manifest_path, "a" if append else "w", encoding="utf-8")
            if not append:
                self._write_manifest(
                    manifest_file,
                    {
                        "kind": "run",
                        "run_id": run_id,
                        "source_uri": source_uri,
                        "target_uri": target_uri,
                        "conflict": conflict,
                        "verify": verify,
                        "started_at": utc_now(),
                    },
                )

        try:
            summary = self._run_migration(
                source_fs=source_fs,
                source_path=source_path,
                target_fs=target_fs,
                target_path=target_path,
                target_uri=target_uri,
                target_is_dir_hint=target_uri.endswith("/"),
                limit=limit,
                dry_run=dry_run,
                conflict=conflict,
                verify=verify,
                on_error=on_error,
                use_temp=not no_temp,
                run_id=run_id,
                manifest_file=manifest_file,
                completed=completed,
                json_output=json_output,
            )
        finally:
            if manifest_file is not None:
                self._write_manifest(
                    manifest_file,
                    {
                        "kind": "run_finished",
                        "run_id": run_id,
                        "finished_at": utc_now(),
                    },
                )
                manifest_file.close()

        summary["manifest"] = manifest_path
        summary["resumed_from"] = resume_path
        summary["resume_entries_loaded"] = len(completed)
        summary["resume_run_id"] = (
            resume_metadata.get("run_id") if resume_metadata else None
        )
        ok = (
            summary["files_skipped"] == 0
            and summary["conflicts"] == 0
            and summary["errors"] == 0
        )

        if json_output:
            self.stdout.write(
                json.dumps(
                    {
                        "ok": ok,
                        "dry_run": dry_run,
                        "summary": summary,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            self._write_human_summary(summary, dry_run=dry_run)

        if not ok:
            raise CommandError(
                "Migration completed with skipped files, conflicts, or errors",
                returncode=EXIT_ATTENTION,
            )

    def _run_migration(
        self,
        *,
        source_fs,
        source_path,
        target_fs,
        target_path,
        target_uri,
        target_is_dir_hint,
        limit,
        dry_run,
        conflict,
        verify,
        on_error,
        use_temp,
        run_id,
        manifest_file,
        completed,
        json_output,
    ):
        entries = self._build_entries(
            source_fs,
            source_path,
            target_fs,
            target_path,
            target_is_dir_hint,
        )
        entries_found = len(entries)
        if limit is not None:
            entries = entries[:limit]

        summary = {
            "entries_found": entries_found,
            "entries_selected": len(entries),
            "entries_resumed": 0,
            "files_copied": 0,
            "dirs_created": 0,
            "files_skipped": 0,
            "conflicts": 0,
            "errors": 0,
            "bytes_copied": 0,
        }

        if not dry_run and not manifest_file:
            raise CommandError(
                "Manifest file was not initialized",
                returncode=EXIT_FAILURE,
            )

        if not dry_run and not json_output:
            target_label = target_uri
            self.stdout.write(f"Migrating to {target_label}...")
        elif dry_run and not json_output:
            self.stdout.write("Previewing migration...")

        for entry in entries:
            key = (entry.source, entry.target)
            if key in completed:
                summary["entries_resumed"] += 1
                continue

            started_at = utc_now()
            try:
                result = self._process_entry(
                    source_fs=source_fs,
                    target_fs=target_fs,
                    entry=entry,
                    dry_run=dry_run,
                    conflict=conflict,
                    verify=verify,
                    use_temp=use_temp,
                    run_id=run_id,
                )
            except MigrationConflict as exc:
                result = MigrationResult(status="conflict", error=str(exc))
                summary["conflicts"] += 1
                if conflict == "fail":
                    self._record_manifest(
                        manifest_file,
                        entry,
                        result,
                        started_at,
                        dry_run=dry_run,
                    )
                    raise CommandError(str(exc), returncode=EXIT_ATTENTION) from exc
            except Exception as exc:
                result = MigrationResult(
                    status="error",
                    error=f"{exc.__class__.__name__}: {exc}",
                )
                summary["errors"] += 1
                if on_error == "abort":
                    self._record_manifest(
                        manifest_file,
                        entry,
                        result,
                        started_at,
                        dry_run=dry_run,
                    )
                    raise CommandError(str(exc), returncode=EXIT_FAILURE) from exc

            if result.status in {"copied", "would_copy"}:
                if result.status == "copied":
                    summary["files_copied"] += 1
                    summary["bytes_copied"] += result.size
            elif result.status in {"created_dir", "would_create_dir"}:
                if result.status == "created_dir":
                    summary["dirs_created"] += 1
            elif result.status.startswith("skipped"):
                if result.status != "skipped_same_checksum":
                    summary["files_skipped"] += 1
            elif result.status == "dir_exists":
                pass

            self._record_manifest(
                manifest_file,
                entry,
                result,
                started_at,
                dry_run=dry_run,
            )

        return summary

    def _build_entries(
        self,
        source_fs,
        source_path,
        target_fs,
        target_path,
        target_is_dir_hint,
    ):
        try:
            source_info = source_fs.info(source_path)
        except FileNotFoundError as exc:
            raise CommandError(
                f"Source does not exist: {source_path}",
                returncode=EXIT_FAILURE,
            ) from exc

        if source_info.get("type") == "file":
            target_file = self._target_for_single_file(
                source_path,
                target_fs,
                target_path,
                target_is_dir_hint,
            )
            return [
                MigrationEntry(
                    source=source_path,
                    target=target_file,
                    type="file",
                    size=source_info.get("size"),
                )
            ]

        root = source_path.rstrip("/") or "/"
        target_root = target_path.rstrip("/") or "/"
        entries = {
            root: MigrationEntry(
                source=root,
                target=target_root,
                type="directory",
                size=0,
            )
        }
        found = source_fs.find(root, withdirs=True, detail=True)
        if isinstance(found, dict):
            iterable = found.items()
        else:
            iterable = [(path, {}) for path in found]

        for path, info in iterable:
            entry_type = info.get("type")
            if entry_type is None:
                try:
                    entry_type = source_fs.info(path).get("type")
                except FileNotFoundError:
                    continue
            relative = self._relative_path(root, path)
            target = self._join_path(target_root, relative)
            entries[path] = MigrationEntry(
                source=path,
                target=target,
                type=entry_type,
                size=info.get("size"),
            )

        return sorted(
            entries.values(),
            key=lambda item: (item.source.count("/"), item.type != "directory", item.source),
        )

    def _target_for_single_file(
        self,
        source_path,
        target_fs,
        target_path,
        target_is_dir_hint,
    ):
        if target_is_dir_hint or self._is_existing_dir(target_fs, target_path):
            return self._join_path(target_path, source_path.rstrip("/").rsplit("/", 1)[-1])
        return target_path

    def _process_entry(
        self,
        *,
        source_fs,
        target_fs,
        entry,
        dry_run,
        conflict,
        verify,
        use_temp,
        run_id,
    ):
        if entry.type == "directory":
            return self._process_directory(target_fs, entry, dry_run=dry_run)
        return self._process_file(
            source_fs=source_fs,
            target_fs=target_fs,
            entry=entry,
            dry_run=dry_run,
            conflict=conflict,
            verify=verify,
            use_temp=use_temp,
            run_id=run_id,
        )

    def _process_directory(self, target_fs, entry, *, dry_run):
        exists, info = self._target_info(target_fs, entry.target)
        if exists and info.get("type") != "directory":
            raise MigrationConflict(f"Target exists and is not a directory: {entry.target}")
        if exists:
            return MigrationResult(status="dir_exists")
        if dry_run:
            return MigrationResult(status="would_create_dir")
        self._makedirs(target_fs, entry.target)
        return MigrationResult(status="created_dir")

    def _process_file(
        self,
        *,
        source_fs,
        target_fs,
        entry,
        dry_run,
        conflict,
        verify,
        use_temp,
        run_id,
    ):
        exists, info = self._target_info(target_fs, entry.target)
        if exists and info.get("type") == "directory":
            raise MigrationConflict(f"Target exists and is a directory: {entry.target}")
        if exists:
            if conflict == "skip":
                return MigrationResult(status="skipped_exists")
            if conflict == "fail":
                raise MigrationConflict(f"Target already exists: {entry.target}")
            if conflict == "checksum":
                source_hash, source_size = self._hash_file(source_fs, entry.source)
                target_hash, target_size = self._hash_file(target_fs, entry.target)
                if source_size == target_size and source_hash == target_hash:
                    return MigrationResult(
                        status="skipped_same_checksum",
                        size=source_size,
                        sha256=source_hash,
                    )
                raise MigrationConflict(
                    f"Target checksum differs from source: {entry.target}"
                )

        if dry_run:
            return MigrationResult(status="would_copy", size=entry.size or 0)

        return self._copy_file(
            source_fs=source_fs,
            source_path=entry.source,
            target_fs=target_fs,
            target_path=entry.target,
            overwrite=exists and conflict == "overwrite",
            verify=verify,
            use_temp=use_temp,
            run_id=run_id,
        )

    def _copy_file(
        self,
        *,
        source_fs,
        source_path,
        target_fs,
        target_path,
        overwrite,
        verify,
        use_temp,
        run_id,
    ):
        parent = self._parent_path(target_path)
        self._makedirs(target_fs, parent)
        if use_temp:
            temp_path = self._temp_path(target_path, run_id)
            self._makedirs(target_fs, self._parent_path(temp_path))
            try:
                size, source_hash = self._stream_copy(source_fs, source_path, target_fs, temp_path)
                self._move_temp(target_fs, temp_path, target_path, overwrite=overwrite)
                self._cleanup_temp_path(target_fs, temp_path)
            except NotImplementedError:
                self._safe_rm(target_fs, temp_path)
                self._cleanup_temp_path(target_fs, temp_path)
                size, source_hash = self._stream_copy(
                    source_fs,
                    source_path,
                    target_fs,
                    target_path,
                )
        else:
            size, source_hash = self._stream_copy(source_fs, source_path, target_fs, target_path)

        self._verify_file(
            target_fs,
            target_path,
            expected_size=size,
            expected_hash=source_hash,
            verify=verify,
        )
        return MigrationResult(status="copied", size=size, sha256=source_hash)

    def _stream_copy(self, source_fs, source_path, target_fs, target_path):
        digest = hashlib.sha256()
        size = 0
        with source_fs.open(source_path, "rb") as src:
            with target_fs.open(target_path, "wb") as dst:
                while True:
                    chunk = src.read(COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    dst.write(chunk)
        return size, digest.hexdigest()

    def _verify_file(
        self,
        target_fs,
        target_path,
        *,
        expected_size,
        expected_hash,
        verify,
    ):
        if verify == "off":
            return
        if verify == "size":
            actual_size = self._target_size(target_fs, target_path)
            if actual_size != expected_size:
                raise ValueError(
                    f"Target size mismatch for {target_path}: "
                    f"expected {expected_size}, got {actual_size}"
                )
            return

        actual_hash, actual_size = self._hash_file(target_fs, target_path)
        if actual_size != expected_size or actual_hash != expected_hash:
            raise ValueError(
                f"Target checksum mismatch for {target_path}: "
                f"expected {expected_hash}/{expected_size}, "
                f"got {actual_hash}/{actual_size}"
            )

    def _hash_file(self, fs, path):
        digest = hashlib.sha256()
        size = 0
        with fs.open(path, "rb") as f:
            while True:
                chunk = f.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    def _target_info(self, fs, path):
        try:
            return True, fs.info(path)
        except FileNotFoundError:
            return False, {}

    def _target_size(self, fs, path):
        try:
            info = fs.info(path)
        except FileNotFoundError:
            raise
        if "size" in info:
            return info["size"]
        _, size = self._hash_file(fs, path)
        return size

    def _is_existing_dir(self, fs, path):
        exists, info = self._target_info(fs, path)
        return exists and info.get("type") == "directory"

    def _makedirs(self, fs, path):
        if not path or path == "/":
            return
        fs.makedirs(path, exist_ok=True)

    def _move_temp(self, fs, temp_path, target_path, *, overwrite):
        try:
            fs.mv(temp_path, target_path, recursive=False, overwrite=overwrite)
        except TypeError:
            fs.mv(temp_path, target_path)
        except NotImplementedError:
            raise

    def _safe_rm(self, fs, path):
        try:
            if path and fs.exists(path):
                fs.rm(path)
        except Exception:
            pass

    def _cleanup_temp_path(self, fs, temp_path):
        run_dir = self._parent_path(temp_path)
        tmp_dir = self._parent_path(run_dir)
        self._safe_rmdir(fs, run_dir)
        self._safe_rmdir(fs, tmp_dir)

    def _safe_rmdir(self, fs, path):
        try:
            if path and path != "/" and fs.exists(path):
                fs.rmdir(path)
        except Exception:
            pass

    def _parent_path(self, path):
        path = path.rstrip("/")
        if "/" not in path.strip("/"):
            return "/"
        return path.rsplit("/", 1)[0] or "/"

    def _temp_path(self, target_path, run_id):
        parent = self._parent_path(target_path)
        name = target_path.rstrip("/").rsplit("/", 1)[-1]
        return self._join_path(
            self._join_path(parent, ".django-fsspec-migrate-tmp"),
            self._join_path(run_id, name).lstrip("/"),
        )

    def _relative_path(self, root, path):
        root = root.rstrip("/") or "/"
        if root == "/":
            return path.lstrip("/")
        if path == root:
            return ""
        return path[len(root):].lstrip("/")

    def _join_path(self, root, relative):
        root = root.rstrip("/")
        relative = relative.strip("/")
        if not relative:
            return root or "/"
        if not root or root == "/":
            return "/" + relative
        return root + "/" + relative

    def _write_manifest(self, manifest_file, payload):
        manifest_file.write(json.dumps(payload, sort_keys=True) + "\n")
        manifest_file.flush()

    def _record_manifest(self, manifest_file, entry, result, started_at, *, dry_run):
        if dry_run or manifest_file is None:
            return
        self._write_manifest(
            manifest_file,
            {
                "kind": "entry",
                "source": entry.source,
                "target": entry.target,
                "type": entry.type,
                "size": result.size,
                "sha256": result.sha256,
                "status": result.status,
                "error": result.error,
                "started_at": started_at,
                "finished_at": utc_now(),
            },
        )

    def _read_resume_manifest(self, path, *, source_uri, target_uri):
        if not os.path.exists(path):
            raise CommandError(
                f"Resume manifest does not exist: {path}",
                returncode=EXIT_FAILURE,
            )

        run_metadata = None
        completed = set()
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if payload.get("kind") == "run" and run_metadata is None:
                        run_metadata = payload
                    elif payload.get("kind") == "entry":
                        status = payload.get("status")
                        if status in SUCCESS_STATUSES:
                            completed.add((payload.get("source"), payload.get("target")))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(
                f"Could not read resume manifest: {exc}",
                returncode=EXIT_FAILURE,
            ) from exc

        if run_metadata is None:
            raise CommandError(
                f"Resume manifest has no run metadata: {path}",
                returncode=EXIT_FAILURE,
            )
        if (
            run_metadata.get("source_uri") != source_uri
            or run_metadata.get("target_uri") != target_uri
        ):
            raise CommandError(
                "Resume manifest does not match source and target URI",
                returncode=EXIT_FAILURE,
            )
        return run_metadata, completed

    def _default_manifest_path(self, run_id):
        base_dir = getattr(settings, "DJANGO_FSSPEC_MIGRATE_MANIFEST_DIR", None)
        if base_dir is None:
            base_dir = os.path.join(
                getattr(settings, "BASE_DIR", os.getcwd()),
                ".django-fsspec-migrate",
            )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return os.path.join(base_dir, f"migrate-{timestamp}-{run_id}.jsonl")

    def _ensure_parent_dir(self, path):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)

    def _write_human_summary(self, summary, *, dry_run):
        self.stdout.write("")
        for label, count in summary.items():
            if count is not None:
                self.stdout.write(f"{label}: {count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\nWould inspect {summary['entries_selected']} migration entries."
                )
            )
        elif summary["errors"] or summary["conflicts"] or summary["files_skipped"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\nCopied {summary['files_copied']} file(s); "
                    f"skipped {summary['files_skipped']} file(s); "
                    f"conflicts {summary['conflicts']}; "
                    f"errors {summary['errors']}."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nCopied {summary['files_copied']} file(s); "
                    f"created {summary['dirs_created']} directories."
                )
            )
