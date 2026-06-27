from django.core.management.base import BaseCommand

from django_fsspec.models import StorageBlock


class Command(BaseCommand):
    help = "Clean up free storage blocks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep",
            type=int,
            default=0,
            help="Number of free blocks to keep in the pool (default: 0)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without deleting",
        )

    def handle(self, *args, **options):
        keep = options["keep"]
        dry_run = options["dry_run"]

        free_blocks = StorageBlock.objects.filter(is_free=True).order_by("id")
        total_free = free_blocks.count()

        if total_free <= keep:
            self.stdout.write(
                f"Found {total_free} free blocks, keeping {keep}. Nothing to clean."
            )
            return

        to_delete = total_free - keep
        block_ids = list(free_blocks.values_list("id", flat=True)[:to_delete])

        if dry_run:
            self.stdout.write(
                f"Would delete {to_delete} free blocks (keeping {keep})"
            )
            return

        deleted_count, _ = StorageBlock.objects.filter(id__in=block_ids).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_count} free blocks (kept {keep})"
            )
        )
