from dataclasses import dataclass
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from django_fsspec.exceptions import DataIntegrityError
from django_fsspec.models import NODE_TYPE_FILE, FileBlock, FileNode, StorageBlock
from django_fsspec.operations import (
    INTEGRITY_CHECKSUM,
    INTEGRITY_METADATA,
    INTEGRITY_OFF,
    _chunk_data,
    _compute_checksum,
    _load_file_data,
)
from django_fsspec.validators import validate_path


EXIT_ATTENTION = 1
EXIT_FAILURE = 2


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


@dataclass
class RechunkResult:
    bytes_rewritten: int = 0
    old_blocks: int = 0
    new_blocks: int = 0
    data: bytes = b""
    skipped: bool = False
    reason: str = ""


class Command(BaseCommand):
    help = "Rewrite existing files to a target block size"

    def add_arguments(self, parser):
        parser.add_argument(
            "--block-size",
            type=positive_int,
            required=True,
            help="Target block size in bytes",
        )
        parser.add_argument(
            "--namespace",
            type=int,
            default=None,
            help="Rechunk only a specific namespace",
        )
        parser.add_argument(
            "--prefix",
            default=None,
            help="Rechunk only files at or below this path prefix",
        )
        parser.add_argument(
            "--source-block-size",
            type=positive_int,
            default=None,
            help="Rechunk only files currently using this block size",
        )
        parser.add_argument(
            "--limit",
            type=positive_int,
            default=None,
            help="Maximum number of files to inspect in this run",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview work without modifying the database",
        )
        parser.add_argument(
            "--verify",
            choices=[INTEGRITY_OFF, INTEGRITY_METADATA, INTEGRITY_CHECKSUM],
            default=INTEGRITY_METADATA,
            help="Integrity policy before rewriting each file",
        )
        parser.add_argument(
            "--on-error",
            choices=["skip", "abort"],
            default="skip",
            help="Whether to skip damaged files or abort the run",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable summary and skipped-file details",
        )

    def handle(self, *args, **options):
        target_block_size = options["block_size"]
        namespace = options["namespace"]
        prefix = self._normalize_prefix(options["prefix"])
        source_block_size = options["source_block_size"]
        limit = options["limit"]
        dry_run = options["dry_run"]
        verify = options["verify"]
        on_error = options["on_error"]
        json_output = options["json"]

        qs = self._candidate_queryset(
            target_block_size=target_block_size,
            namespace=namespace,
            prefix=prefix,
            source_block_size=source_block_size,
        )
        matching_count = qs.count()
        if limit is not None:
            qs = qs[:limit]
        file_ids = list(qs.values_list("id", flat=True))

        summary = {
            "files_matching": matching_count,
            "files_selected": len(file_ids),
            "files_rechunked": 0,
            "files_skipped": 0,
            "errors": 0,
            "bytes_rewritten": 0,
            "old_blocks": 0,
            "new_blocks": 0,
        }
        skipped = []

        if not json_output:
            action = "Previewing" if dry_run else "Rechunking"
            self.stdout.write(
                f"{action} files to block size {target_block_size} bytes..."
            )

        for file_id in file_ids:
            try:
                if dry_run:
                    result = self._inspect_file(
                        file_id=file_id,
                        target_block_size=target_block_size,
                        verify=verify,
                    )
                else:
                    with transaction.atomic():
                        result = self._rechunk_file(
                            file_id=file_id,
                            target_block_size=target_block_size,
                            verify=verify,
                        )
            except Exception as exc:
                summary["files_skipped"] += 1
                summary["errors"] += 1
                skipped.append(
                    {
                        "file_id": file_id,
                        "reason": str(exc),
                        "error": exc.__class__.__name__,
                    }
                )
                message = f"FileNode {file_id}: {exc}"
                if on_error == "abort":
                    if json_output:
                        self._write_json(summary, skipped, dry_run)
                    raise CommandError(
                        message,
                        returncode=EXIT_FAILURE,
                    ) from exc
                if not json_output:
                    self.stdout.write(self.style.WARNING(f"Skipped {message}"))
                continue

            if result.skipped:
                summary["files_skipped"] += 1
                skipped.append(
                    {
                        "file_id": file_id,
                        "reason": result.reason,
                        "error": "",
                    }
                )
                if result.reason:
                    if not json_output:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Skipped FileNode {file_id}: {result.reason}"
                            )
                        )
                continue

            summary["files_rechunked"] += 1
            summary["bytes_rewritten"] += result.bytes_rewritten
            summary["old_blocks"] += result.old_blocks
            summary["new_blocks"] += result.new_blocks

        ok = summary["files_skipped"] == 0 and summary["errors"] == 0
        if json_output:
            self._write_json(summary, skipped, dry_run)
        else:
            self._write_human_summary(summary, dry_run)

        if not ok:
            raise CommandError(
                "Rechunk completed with skipped files or errors",
                returncode=EXIT_ATTENTION,
            )

    def _write_json(self, summary, skipped, dry_run):
        self.stdout.write(
            json.dumps(
                {
                    "ok": summary["files_skipped"] == 0
                    and summary["errors"] == 0,
                    "dry_run": dry_run,
                    "summary": summary,
                    "skipped": skipped,
                },
                indent=2,
                sort_keys=True,
            )
        )

    def _write_human_summary(self, summary, dry_run):
        self.stdout.write("")
        for label, count in summary.items():
            self.stdout.write(f"{label}: {count}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"\nWould rechunk {summary['files_rechunked']} file(s)."
                )
            )
        elif summary["errors"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\nRechunked {summary['files_rechunked']} file(s); "
                    f"skipped {summary['files_skipped']} file(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nRechunked {summary['files_rechunked']} file(s)."
                )
            )

    def _candidate_queryset(
        self,
        *,
        target_block_size,
        namespace,
        prefix,
        source_block_size,
    ):
        qs = (
            FileNode.objects.filter(node_type=NODE_TYPE_FILE)
            .exclude(block_size=target_block_size)
            .order_by("id")
        )
        if namespace is not None:
            qs = qs.filter(namespace_id=namespace)
        if prefix and prefix != "/":
            descendant_prefix = prefix.rstrip("/") + "/"
            qs = qs.filter(Q(path=prefix) | Q(path__startswith=descendant_prefix))
        if source_block_size is not None:
            qs = qs.filter(block_size=source_block_size)
        return qs

    def _normalize_prefix(self, prefix):
        if prefix is None:
            return None
        try:
            return validate_path(prefix)
        except Exception as exc:
            raise CommandError(
                f"Invalid --prefix: {exc}",
                returncode=EXIT_FAILURE,
            ) from exc

    def _inspect_file(self, *, file_id, target_block_size, verify):
        file_node = FileNode.objects.get(pk=file_id)
        return self._prepare_rechunk(file_node, target_block_size, verify)

    def _rechunk_file(self, *, file_id, target_block_size, verify):
        file_node = FileNode.objects.select_for_update().get(pk=file_id)
        result = self._prepare_rechunk(file_node, target_block_size, verify)
        if result.skipped:
            return result

        original_version = file_node.version
        data = result.data
        chunks = _chunk_data(data, target_block_size)
        old_block_ids = list(
            FileBlock.objects.filter(file=file_node).values_list(
                "block_id",
                flat=True,
            )
        )

        new_blocks = [
            StorageBlock.objects.create(
                data=chunk,
                size=len(chunk),
                checksum=_compute_checksum(chunk),
                is_free=False,
            )
            for chunk in chunks
        ]

        FileBlock.objects.filter(file=file_node).delete()
        FileBlock.objects.bulk_create(
            [
                FileBlock(file=file_node, block=block, sequence=sequence)
                for sequence, block in enumerate(new_blocks)
            ]
        )

        if old_block_ids:
            StorageBlock.objects.filter(
                id__in=old_block_ids,
                file_blocks__isnull=True,
            ).update(is_free=True)

        updated = FileNode.objects.filter(
            pk=file_node.pk,
            version=original_version,
        ).update(
            block_size=target_block_size,
            size=len(data),
            checksum=_compute_checksum(data),
            version=original_version + 1,
            updated_at=timezone.now(),
        )
        if not updated:
            raise DataIntegrityError(
                f"File {file_node.path} changed while rechunking; retry later"
            )

        return result

    def _prepare_rechunk(self, file_node, target_block_size, verify):
        if file_node.node_type != NODE_TYPE_FILE:
            return RechunkResult(skipped=True, reason="not a file")
        if file_node.block_size == target_block_size:
            return RechunkResult(skipped=True, reason="already uses target block size")
        if file_node.block_size <= 0:
            raise DataIntegrityError(
                f"invalid source block size: {file_node.block_size}"
            )

        data = _load_file_data(
            file_node,
            integrity=verify,
            require_unshared=True,
        )
        chunks = _chunk_data(data, target_block_size)
        old_blocks = FileBlock.objects.filter(file=file_node).count()
        result = RechunkResult(
            bytes_rewritten=len(data),
            old_blocks=old_blocks,
            new_blocks=len(chunks),
            data=data,
        )
        return result
