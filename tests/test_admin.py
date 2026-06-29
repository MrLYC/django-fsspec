from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from django_fsspec.admin import FileNodeAdmin
from django_fsspec.models import FileNode


class TestFileNodeAdmin(TestCase):
    def test_admin_registered(self):
        admin = FileNodeAdmin(FileNode, AdminSite())
        assert "namespace" in admin.list_display
        assert "path" in admin.list_display
        assert "size" in admin.list_display
        assert "block_size" in admin.list_display
        assert "version" in admin.list_display
        assert "updated_at" in admin.list_display

    def test_admin_list_filter(self):
        admin = FileNodeAdmin(FileNode, AdminSite())
        assert "namespace" in admin.list_filter

    def test_admin_search_fields(self):
        admin = FileNodeAdmin(FileNode, AdminSite())
        assert "path" in admin.search_fields

    def test_admin_readonly_fields(self):
        admin = FileNodeAdmin(FileNode, AdminSite())
        assert "checksum" in admin.readonly_fields
        assert "version" in admin.readonly_fields
