from django.test import TestCase
from django.urls import reverse

from django_fsspec.models import Namespace
from django_fsspec.operations import write_file
from tests.test_webdav import WebDAVTestCase, _basic_auth


class TestWebDAVStreaming(WebDAVTestCase):
    def test_get_large_file_is_streaming(self):
        write_file(1, "/large.txt", b"x" * 100)
        response = self._request("get", "large.txt", user=self.reader)
        assert response.status_code == 200
        assert response.streaming is True
        assert b"".join(response.streaming_content) == b"x" * 100
        assert response["Accept-Ranges"] == "bytes"

    def test_head_does_not_read_body(self):
        write_file(1, "/head.txt", b"body-content")
        response = self._request("head", "head.txt", user=self.reader)
        assert response.status_code == 200
        assert response["Content-Length"] == "12"
        assert response.content == b""

    def test_get_range_single_block(self):
        write_file(1, "/range.txt", b"hello world")
        response = self._request(
            "get", "range.txt", user=self.reader, HTTP_RANGE="bytes=0-4"
        )
        assert response.status_code == 206
        assert response["Content-Range"] == "bytes 0-4/11"
        assert b"".join(response.streaming_content) == b"hello"

    def test_get_range_cross_block(self):
        write_file(1, "/range2.txt", b"0123456789abcdef")
        response = self._request(
            "get", "range2.txt", user=self.reader, HTTP_RANGE="bytes=5-10"
        )
        assert response.status_code == 206
        assert b"".join(response.streaming_content) == b"56789a"

    def test_get_range_open_ended(self):
        write_file(1, "/range3.txt", b"0123456789")
        response = self._request(
            "get", "range3.txt", user=self.reader, HTTP_RANGE="bytes=5-"
        )
        assert response.status_code == 206
        assert b"".join(response.streaming_content) == b"56789"

    def test_get_range_out_of_range_returns_416(self):
        write_file(1, "/range4.txt", b"short")
        response = self._request(
            "get", "range4.txt", user=self.reader, HTTP_RANGE="bytes=10-20"
        )
        assert response.status_code == 416

    def test_put_large_file_streaming(self):
        data = b"y" * 10_000
        response = self._request(
            "put", "upload.txt", user=self.writer, data=data
        )
        assert response.status_code == 201

        response = self._request("get", "upload.txt", user=self.reader)
        assert b"".join(response.streaming_content) == data

    def test_put_empty_file(self):
        response = self._request(
            "put", "empty-put.txt", user=self.writer, data=b""
        )
        assert response.status_code == 201
        response = self._request("get", "empty-put.txt", user=self.reader)
        assert b"".join(response.streaming_content) == b""

    def test_get_range_invalid_headers_return_416(self):
        write_file(1, "/range-invalid.txt", b"0123456789")
        bad_headers = [
            "text=0-5",       # wrong unit
            "bytes=0-5,8-9",  # multi-range
            "bytes=-5",       # suffix range
            "bytes=5",        # missing dash
            "bytes=foo-bar",  # non-integer
        ]
        for header in bad_headers:
            response = self._request(
                "get", "range-invalid.txt", user=self.reader, HTTP_RANGE=header
            )
            assert response.status_code == 416, header

    def test_head_not_found_returns_404(self):
        response = self._request("head", "missing-head.txt", user=self.reader)
        assert response.status_code == 404

    def test_head_directory_returns_404(self):
        write_file(1, "/head-dir/file.txt", b"x")
        response = self._request("head", "head-dir", user=self.reader)
        assert response.status_code == 404

    def test_put_root_returns_405(self):
        url = reverse("webdav_root", kwargs={"namespace_id": 1})
        response = self.client.put(
            url, data=b"x", content_type="application/octet-stream",
            HTTP_AUTHORIZATION=_basic_auth("writer", "writer-pass"),
        )
        assert response.status_code == 405

    def test_delete_root_returns_405(self):
        url = reverse("webdav_root", kwargs={"namespace_id": 1})
        response = self.client.delete(
            url, HTTP_AUTHORIZATION=_basic_auth("writer", "writer-pass")
        )
        assert response.status_code == 405

    def test_propfind_depth_infinity_returns_403(self):
        response = self._request(
            "propfind", "", user=self.reader, HTTP_DEPTH="infinity"
        )
        assert response.status_code == 403

    def test_copy_root_returns_405(self):
        response = self._request(
            "copy", "", user=self.writer, HTTP_DESTINATION="http://testserver/webdav/1/dst"
        )
        assert response.status_code == 405

    def test_move_root_returns_405(self):
        response = self._request(
            "move", "", user=self.writer, HTTP_DESTINATION="http://testserver/webdav/1/dst"
        )
        assert response.status_code == 405

    def test_write_without_auth_returns_401(self):
        response = self.client.put(
            self._url("no-auth.txt"), data=b"x", content_type="application/octet-stream"
        )
        assert response.status_code == 401

    def test_invalid_path_returns_400(self):
        response = self._request("get", "../escape.txt", user=self.reader)
        assert response.status_code == 400


class TestWebDAVStreamingNamespace(TestCase):
    """Smoke test that the WebDAV endpoint can stream with a fresh namespace."""

    def setUp(self):
        self.client = __import__("django.test", fromlist=["Client"]).Client()
        from django.contrib.auth.models import User

        self.ns = Namespace.objects.create(name="stream-ns")
        self.user = User.objects.create_superuser(
            username="stream-admin", password="stream-pass", email="s@example.com"
        )

    def test_streaming_put_and_get(self):
        from django.urls import reverse

        url = reverse("webdav", kwargs={"namespace_id": self.ns.id, "webdav_path": "x.bin"})
        auth = _basic_auth("stream-admin", "stream-pass")
        data = b"\x00" * 5000 + b"\xff" * 5000
        response = self.client.put(url, data=data, content_type="application/octet-stream", HTTP_AUTHORIZATION=auth)
        assert response.status_code == 201

        response = self.client.get(url, HTTP_AUTHORIZATION=auth)
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == data
