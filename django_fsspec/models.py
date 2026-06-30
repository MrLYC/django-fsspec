from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models

DEFAULT_BLOCK_SIZE = 32 * 1024  # 32KB

NODE_TYPE_FILE = "file"
NODE_TYPE_DIRECTORY = "directory"
NODE_TYPE_CHOICES = [
    (NODE_TYPE_FILE, "File"),
    (NODE_TYPE_DIRECTORY, "Directory"),
]


def get_block_size():
    return getattr(settings, "DJANGO_FSSPEC_BLOCK_SIZE", DEFAULT_BLOCK_SIZE)


def get_max_file_size():
    return getattr(settings, "DJANGO_FSSPEC_MAX_FILE_SIZE", 2 * 1024 * 1024)


class Namespace(models.Model):
    name = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True, default="")
    read_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="dav_read_namespaces",
        help_text="Groups that can read files in this namespace.",
    )
    write_groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="dav_write_namespaces",
        help_text="Groups that can write files in this namespace.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            ("read_namespace", "Can read all namespaces"),
            ("write_namespace", "Can write all namespaces"),
        ]
        verbose_name = "Namespace"
        verbose_name_plural = "Namespaces"

    def __str__(self):
        return f"Namespace({self.id}, {self.name})"


class FileNode(models.Model):
    namespace = models.ForeignKey(
        Namespace,
        on_delete=models.PROTECT,
        related_name="files",
    )
    path = models.CharField(max_length=700)
    node_type = models.CharField(
        max_length=16,
        choices=NODE_TYPE_CHOICES,
        default=NODE_TYPE_FILE,
    )
    size = models.BigIntegerField(default=0)
    block_size = models.IntegerField(default=DEFAULT_BLOCK_SIZE)
    checksum = models.CharField(max_length=64, blank=True, default="")
    content_type = models.CharField(max_length=256, blank=True, default="")
    version = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("namespace", "path")]

    def __str__(self):
        return f"FileNode(ns={self.namespace_id}, path={self.path})"


class StorageBlock(models.Model):
    data = models.BinaryField()
    size = models.IntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, default="")
    is_free = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"StorageBlock(id={self.pk}, size={self.size}, free={self.is_free})"


class FileBlock(models.Model):
    file = models.ForeignKey(
        FileNode, on_delete=models.CASCADE, related_name="blocks"
    )
    block = models.ForeignKey(
        StorageBlock, on_delete=models.PROTECT, related_name="file_blocks"
    )
    sequence = models.IntegerField()

    class Meta:
        unique_together = [("file", "sequence")]
        ordering = ["sequence"]

    def __str__(self):
        return f"FileBlock(file={self.file_id}, seq={self.sequence})"
