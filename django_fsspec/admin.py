from django.contrib import admin

from .models import FileNode


@admin.register(FileNode)
class FileNodeAdmin(admin.ModelAdmin):
    list_display = ["namespace", "path", "size", "block_size", "version", "updated_at"]
    list_filter = ["namespace"]
    search_fields = ["path"]
    readonly_fields = ["checksum", "version", "created_at", "updated_at"]
