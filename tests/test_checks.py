import logging

from django.test import TestCase, override_settings

from django_fsspec.checks import (
    BLOCK_SIZE_MISMATCH_ID,
    check_block_size_consistency,
    check_block_size_on_startup,
)
from django_fsspec.operations import write_file


class TestBlockSizeCheck(TestCase):
    def test_no_warning_when_consistent(self):
        write_file(1, "/check/a.txt", b"data")
        errors = check_block_size_consistency(None)
        assert len(errors) == 0

    def test_no_warning_when_empty(self):
        errors = check_block_size_consistency(None)
        assert len(errors) == 0

    def test_warning_when_mismatch(self):
        write_file(1, "/check/a.txt", b"data")
        # Change the setting to a different block size
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=64 * 1024):
            errors = check_block_size_consistency(None)
        assert len(errors) == 1
        assert errors[0].id == BLOCK_SIZE_MISMATCH_ID
        assert "RechunkOperation" in errors[0].hint

    def test_warning_includes_count(self):
        write_file(1, "/check/a.txt", b"a")
        write_file(1, "/check/b.txt", b"b")
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=64 * 1024):
            errors = check_block_size_consistency(None)
        assert "2 file(s)" in errors[0].msg

    def test_warning_includes_sizes(self):
        write_file(1, "/check/a.txt", b"data")
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=99999):
            errors = check_block_size_consistency(None)
        assert "262144" in errors[0].msg  # default block size

    def test_multiple_block_sizes(self):
        write_file(1, "/check/a.txt", b"a")
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/check/b.txt", b"b")
        # Now set to a third value
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=200):
            errors = check_block_size_consistency(None)
        assert len(errors) == 1
        msg = errors[0].msg
        assert "100" in msg
        assert "262144" in msg

    def test_no_warning_after_rechunk(self):
        """If all files have the same block_size as the setting, no warning."""
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=100):
            write_file(1, "/check/a.txt", b"a")
            write_file(1, "/check/b.txt", b"b")
            errors = check_block_size_consistency(None)
        assert len(errors) == 0


class TestStartupWarning(TestCase):
    def test_startup_logs_warning_on_mismatch(self, caplog=None):
        write_file(1, "/startup/a.txt", b"data")
        with override_settings(DJANGO_FSSPEC_BLOCK_SIZE=64 * 1024):
            with self.assertLogs("django_fsspec", level="WARNING") as cm:
                check_block_size_on_startup()
        assert any("block_size" in msg for msg in cm.output)
        assert any("RechunkOperation" in msg for msg in cm.output)

    def test_startup_silent_when_consistent(self):
        write_file(1, "/startup/a.txt", b"data")
        # Should not log anything — use assertNoLogs if available (Django 4.2+)
        logger = logging.getLogger("django_fsspec")
        with self.assertRaises(AssertionError):
            # assertLogs raises AssertionError if no logs are emitted
            with self.assertLogs("django_fsspec", level="WARNING"):
                check_block_size_on_startup()

    def test_startup_silent_when_empty(self):
        with self.assertRaises(AssertionError):
            with self.assertLogs("django_fsspec", level="WARNING"):
                check_block_size_on_startup()
