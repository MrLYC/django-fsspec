from django.core.management.base import BaseCommand
from django.db.models import Sum

from django_fsspec.models import FileBlock, FileNode, StorageBlock


class Command(BaseCommand):
    help = "Display filesystem statistics"

    def add_arguments(self, parser):
        parser.add_argument(
            "--namespace",
            type=int,
            default=None,
            help="Show stats for a specific namespace only",
        )

    def handle(self, *args, **options):
        namespace = options["namespace"]

        files_qs = FileNode.objects.all()
        if namespace is not None:
            files_qs = files_qs.filter(namespace=namespace)

        file_count = files_qs.count()
        total_file_size = files_qs.aggregate(total=Sum("size"))["total"] or 0

        total_blocks = StorageBlock.objects.count()
        free_blocks = StorageBlock.objects.filter(is_free=True).count()
        used_blocks = total_blocks - free_blocks

        block_data_size = (
            StorageBlock.objects.filter(is_free=False).aggregate(total=Sum("size"))[
                "total"
            ]
            or 0
        )

        namespaces = (
            FileNode.objects.values_list("namespace", flat=True).distinct().count()
        )

        self.stdout.write("Django-fsspec Statistics")
        self.stdout.write("=" * 40)

        if namespace is not None:
            self.stdout.write(f"Namespace:        {namespace}")

        self.stdout.write(f"Namespaces:       {namespaces}")
        self.stdout.write(f"Files:            {file_count}")
        self.stdout.write(f"Total file size:  {_format_size(total_file_size)}")
        self.stdout.write(f"Storage blocks:   {total_blocks}")
        self.stdout.write(f"  Used:           {used_blocks}")
        self.stdout.write(f"  Free:           {free_blocks}")
        self.stdout.write(f"Block data size:  {_format_size(block_data_size)}")
        self.stdout.write(
            f"File-block maps:  {FileBlock.objects.count()}"
        )


def _format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
