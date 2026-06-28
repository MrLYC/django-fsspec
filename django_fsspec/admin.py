from django.contrib import admin

from .models import FileNode, Namespace


@admin.register(FileNode)
class FileNodeAdmin(admin.ModelAdmin):
    list_display = [
        "namespace",
        "path",
        "node_type",
        "size",
        "block_size",
        "version",
        "updated_at",
    ]
    list_filter = ["namespace", "node_type"]
    search_fields = ["path"]
    readonly_fields = ["checksum", "version", "created_at", "updated_at"]


@admin.register(Namespace)
class NamespaceAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "description", "created_at"]
    search_fields = ["name"]
    filter_horizontal = ["read_groups", "write_groups"]
