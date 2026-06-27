import unicodedata

import pytest

from django_fsspec.exceptions import PathValidationError
from django_fsspec.validators import validate_path


class TestValidatePath:
    def test_valid_simple_path(self):
        assert validate_path("/foo.txt") == "/foo.txt"

    def test_valid_nested_path(self):
        assert validate_path("/a/b/c/file.txt") == "/a/b/c/file.txt"

    def test_valid_root(self):
        assert validate_path("/") == "/"

    def test_valid_unicode(self):
        assert validate_path("/日本語/ファイル.txt") == "/日本語/ファイル.txt"

    def test_valid_spaces(self):
        assert validate_path("/path with spaces/file.txt") == "/path with spaces/file.txt"

    def test_valid_dots_in_filename(self):
        assert validate_path("/file.name.ext") == "/file.name.ext"

    def test_valid_single_dot_segment(self):
        assert validate_path("/a/./b") == "/a/./b"

    def test_reject_empty(self):
        with pytest.raises(PathValidationError, match="must not be empty"):
            validate_path("")

    def test_reject_non_string(self):
        with pytest.raises(PathValidationError, match="must be a string"):
            validate_path(123)

    def test_reject_no_leading_slash(self):
        with pytest.raises(PathValidationError, match="must start with '/'"):
            validate_path("foo/bar")

    def test_reject_null_byte(self):
        with pytest.raises(PathValidationError, match="control characters"):
            validate_path("/foo\x00bar")

    def test_reject_control_chars(self):
        for c in ["\x01", "\x0a", "\x1f"]:
            with pytest.raises(PathValidationError, match="control characters"):
                validate_path(f"/foo{c}bar")

    def test_reject_consecutive_slashes(self):
        with pytest.raises(PathValidationError, match="consecutive slashes"):
            validate_path("/foo//bar")

    def test_reject_triple_slashes(self):
        with pytest.raises(PathValidationError, match="consecutive slashes"):
            validate_path("/foo///bar")

    def test_reject_trailing_slash(self):
        with pytest.raises(PathValidationError, match="must not end with '/'"):
            validate_path("/foo/bar/")

    def test_reject_dotdot_segment(self):
        with pytest.raises(PathValidationError, match="'..'"):
            validate_path("/foo/../bar")

    def test_reject_dotdot_at_start(self):
        with pytest.raises(PathValidationError, match="'..'"):
            validate_path("/../foo")

    def test_reject_dotdot_at_end(self):
        with pytest.raises(PathValidationError, match="'..'"):
            validate_path("/foo/..")

    def test_nfc_normalization(self):
        # NFD form of café (e + combining acute)
        nfd = "/caf\u0065\u0301.txt"
        # NFC form (é as single codepoint)
        nfc = "/caf\u00e9.txt"
        result = validate_path(nfd)
        assert result == nfc
        assert unicodedata.is_normalized("NFC", result)

    def test_already_nfc(self):
        path = "/café.txt"
        result = validate_path(path)
        assert result == path
        assert unicodedata.is_normalized("NFC", result)
