import base64

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from django_fsspec.models import Namespace
from django_fsspec.operations import get_file_info, write_file


def _basic_auth(username: str, password: str) -> str:
    credentials = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(credentials).decode("ascii")


class WebDAVTestCase(TestCase):
    def setUp(self):
        self.client = Client()

        self.read_group = Group.objects.create(name="readers")
        self.write_group = Group.objects.create(name="writers")

        self.namespace = Namespace.objects.get(id=1)
        self.namespace.name = "test-ns"
        self.namespace.description = "Test namespace"
        self.namespace.save(update_fields=["name", "description"])
        self.namespace.read_groups.add(self.read_group)
        self.namespace.write_groups.add(self.write_group)

        self.reader = User.objects.create_user(
            username="reader", password="reader-pass"
        )
        self.reader.groups.add(self.read_group)

        self.writer = User.objects.create_user(
            username="writer", password="writer-pass"
        )
        self.writer.groups.add(self.write_group)

        self.superuser = User.objects.create_superuser(
            username="admin", password="admin-pass", email="admin@example.com"
        )

        self.stranger = User.objects.create_user(
            username="stranger", password="stranger-pass"
        )

    def _url(self, path: str = "") -> str:
        if path:
            return reverse("webdav", kwargs={"namespace_id": 1, "webdav_path": path})
        return reverse("webdav_root", kwargs={"namespace_id": 1})

    def _request(
        self,
        method: str,
        path: str = "",
        user=None,
        data=b"",
        content_type="application/octet-stream",
        **extra,
    ):
        url = self._url(path)
        if user is not None:
            extra["HTTP_AUTHORIZATION"] = _basic_auth(
                user.username, f"{user.username}-pass"
            )

        method = method.upper()
        if method in ("GET", "POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"):
            return getattr(self.client, method.lower())(
                url, data=data, content_type=content_type, **extra
            )
        return self.client.generic(
            method, url, data=data, content_type=content_type, **extra
        )

    @staticmethod
    def _response_content(response):
        """Read response body whether it is a regular or streaming response."""
        if getattr(response, "streaming", False):
            return b"".join(response.streaming_content)
        return response.content


class TestBasicAuth(WebDAVTestCase):
    def test_unauthenticated_request_returns_401(self):
        response = self.client.get(self._url("file.txt"))
        assert response.status_code == 401
        assert 'Basic realm="django-fsspec"' in response["WWW-Authenticate"]

    def test_invalid_credentials_return_401(self):
        response = self.client.get(
            self._url("file.txt"),
            HTTP_AUTHORIZATION=_basic_auth("reader", "wrong-pass"),
        )
        assert response.status_code == 401

    def test_valid_credentials_succeed(self):
        response = self._request("get", "file.txt", user=self.reader)
        assert response.status_code == 404


class TestAuthorization(WebDAVTestCase):
    def test_superuser_can_read_and_write(self):
        response = self._request("put", "admin.txt", user=self.superuser, data=b"hi")
        assert response.status_code == 201

        response = self._request("get", "admin.txt", user=self.superuser)
        assert response.status_code == 200
        assert self._response_content(response) == b"hi"

    def test_global_permission_user_can_access_all_namespaces(self):
        perm = Permission.objects.get(codename="write_namespace")
        self.stranger.user_permissions.add(perm)

        response = self._request("put", "global.txt", user=self.stranger, data=b"x")
        assert response.status_code == 201

    def test_reader_can_read_but_not_write(self):
        write_file(1, "/reader.txt", b"hello")

        response = self._request("get", "reader.txt", user=self.reader)
        assert response.status_code == 200

        response = self._request("put", "reader.txt", user=self.reader, data=b"x")
        assert response.status_code == 403

    def test_writer_can_read_and_write(self):
        response = self._request("put", "writer.txt", user=self.writer, data=b"data")
        assert response.status_code == 201

        response = self._request("get", "writer.txt", user=self.writer)
        assert response.status_code == 200

    def test_stranger_gets_403(self):
        write_file(1, "/secret.txt", b"secret")

        response = self._request("get", "secret.txt", user=self.stranger)
        assert response.status_code == 403
        assert response.json()["error"] == "permission_denied"


class TestOptions(WebDAVTestCase):
    def test_options_returns_dav_headers(self):
        response = self._request("options", "", user=self.reader)
        assert response.status_code == 204
        assert response["DAV"] == "1"
        assert "PROPFIND" in response["Allow"]
        assert response["MS-Author-Via"] == "DAV"


class TestPropfind(WebDAVTestCase):
    def test_propfind_file(self):
        write_file(1, "/prop.txt", b"content", content_type="text/plain")

        response = self._request(
            "propfind", "prop.txt", user=self.reader, HTTP_DEPTH="0"
        )
        assert response.status_code == 207
        body = response.content.decode("utf-8")
        assert "multistatus" in body
        assert "getcontentlength" in body
        assert "getetag" in body
        assert "text/plain" in body

    def test_propfind_directory_depth_one(self):
        write_file(1, "/dir/file.txt", b"nested")

        response = self._request("propfind", "dir", user=self.reader)
        assert response.status_code == 207
        body = response.content.decode("utf-8")
        assert "<D:collection" in body
        assert "file.txt" in body

    def test_propfind_missing_path_returns_404(self):
        response = self._request("propfind", "missing", user=self.reader)
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"


class TestGetAndHead(WebDAVTestCase):
    def test_get_file(self):
        write_file(1, "/get.txt", b"hello", content_type="text/plain")

        response = self._request("get", "get.txt", user=self.reader)
        assert response.status_code == 200
        assert self._response_content(response) == b"hello"
        assert response["Content-Type"] == "text/plain"
        assert response["ETag"]
        assert response["Last-Modified"]

    def test_get_directory_returns_404(self):
        write_file(1, "/dir/get.txt", b"x")

        response = self._request("get", "dir", user=self.reader)
        assert response.status_code == 404

    def test_head_has_headers_no_body(self):
        write_file(1, "/head.txt", b"hello", content_type="text/plain")

        response = self._request("head", "head.txt", user=self.reader)
        assert response.status_code == 200
        assert response.content == b""
        assert response["Content-Type"] == "text/plain"


class TestPut(WebDAVTestCase):
    def test_put_creates_file(self):
        response = self._request(
            "put", "put.txt", user=self.writer, data=b"created"
        )
        assert response.status_code == 201

        response = self._request("get", "put.txt", user=self.writer)
        assert self._response_content(response) == b"created"

    def test_put_overwrites_file(self):
        write_file(1, "/put.txt", b"old")

        response = self._request(
            "put", "put.txt", user=self.writer, data=b"new"
        )
        assert response.status_code == 204

        response = self._request("get", "put.txt", user=self.writer)
        assert self._response_content(response) == b"new"

    def test_put_if_none_match_star_prevents_overwrite(self):
        write_file(1, "/put.txt", b"old")

        response = self._request(
            "put",
            "put.txt",
            user=self.writer,
            data=b"new",
            HTTP_IF_NONE_MATCH="*",
        )
        assert response.status_code == 412

    def test_put_guesses_content_type(self):
        response = self._request(
            "put", "data.json", user=self.writer, data=b'{"a": 1}', content_type=""
        )
        assert response.status_code == 201

        response = self._request("get", "data.json", user=self.writer)
        assert response["Content-Type"] == "application/json"


class TestDelete(WebDAVTestCase):
    def test_delete_file(self):
        write_file(1, "/del.txt", b"x")

        response = self._request("delete", "del.txt", user=self.writer)
        assert response.status_code == 204

        response = self._request("get", "del.txt", user=self.writer)
        assert response.status_code == 404

    def test_delete_directory_recursively(self):
        write_file(1, "/del-dir/a.txt", b"a")
        write_file(1, "/del-dir/b.txt", b"b")

        response = self._request("delete", "del-dir", user=self.writer)
        assert response.status_code == 204

        response = self._request("get", "del-dir/a.txt", user=self.writer)
        assert response.status_code == 404


class TestMkcol(WebDAVTestCase):
    def test_mkcol_creates_directory(self):
        response = self._request("mkcol", "new-dir", user=self.writer)
        assert response.status_code == 201
        assert get_file_info(1, "/new-dir")["type"] == "directory"

        response = self._request("propfind", "", user=self.reader)
        assert response.status_code == 207
        assert "new-dir" in response.content.decode("utf-8")

    def test_mkcol_existing_collection_returns_405(self):
        write_file(1, "/existing-dir/file.txt", b"x")

        response = self._request("mkcol", "existing-dir", user=self.writer)
        assert response.status_code == 405

    def test_mkcol_on_file_returns_405(self):
        write_file(1, "/file.txt", b"x")

        response = self._request("mkcol", "file.txt", user=self.writer)
        assert response.status_code == 405

    def test_mkcol_nested_parent_missing_returns_404(self):
        response = self._request("mkcol", "missing/new-dir", user=self.writer)
        assert response.status_code == 404


class TestCopyAndMove(WebDAVTestCase):
    def test_copy_file(self):
        write_file(1, "/src.txt", b"source")

        response = self._request(
            "copy",
            "src.txt",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/dst.txt",
        )
        assert response.status_code == 201

        response = self._request("get", "dst.txt", user=self.writer)
        assert self._response_content(response) == b"source"

    def test_copy_overwrite_false_returns_412(self):
        write_file(1, "/src.txt", b"source")
        write_file(1, "/dst.txt", b"dest")

        response = self._request(
            "copy",
            "src.txt",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/dst.txt",
            HTTP_OVERWRITE="F",
        )
        assert response.status_code == 412

    def test_move_file(self):
        write_file(1, "/move-src.txt", b"move me")

        response = self._request(
            "move",
            "move-src.txt",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/move-dst.txt",
        )
        assert response.status_code == 201

        response = self._request("get", "move-src.txt", user=self.writer)
        assert response.status_code == 404

        response = self._request("get", "move-dst.txt", user=self.writer)
        assert self._response_content(response) == b"move me"

    def test_copy_cross_namespace_rejected(self):
        Namespace.objects.create(id=2, name="other-ns")
        write_file(1, "/src.txt", b"source")

        response = self._request(
            "copy",
            "src.txt",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/2/dst.txt",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_destination"

    def test_copy_directory_returns_409(self):
        response = self._request("mkcol", "copy-dir", user=self.writer)
        assert response.status_code == 201

        response = self._request(
            "copy",
            "copy-dir",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/copy-dir-dst",
        )
        assert response.status_code == 409

    def test_move_directory_returns_409(self):
        response = self._request("mkcol", "move-dir", user=self.writer)
        assert response.status_code == 201

        response = self._request(
            "move",
            "move-dir",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/move-dir-dst",
        )
        assert response.status_code == 409


class TestErrorFormat(WebDAVTestCase):
    def test_error_returns_json(self):
        response = self._request("get", "missing", user=self.reader)
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "not_found"
        assert "message" in data
        assert data["path"] == "/missing"


class TestBasicAuthEdgeCases(WebDAVTestCase):
    def test_middleware_ignores_non_webdav_paths(self):
        response = self.client.get("/not-webdav/")
        assert response.status_code == 404

    def test_non_basic_auth_header_returns_401(self):
        response = self.client.get(
            self._url("file.txt"),
            HTTP_AUTHORIZATION="Bearer token",
        )
        assert response.status_code == 401

    def test_invalid_base64_returns_401(self):
        response = self.client.get(
            self._url("file.txt"),
            HTTP_AUTHORIZATION="Basic not-base64!!!",
        )
        assert response.status_code == 401

    def test_missing_colon_returns_401(self):
        credentials = base64.b64encode(b"nocolon").decode("ascii")
        response = self.client.get(
            self._url("file.txt"),
            HTTP_AUTHORIZATION=f"Basic {credentials}",
        )
        assert response.status_code == 401


class TestPermissionsDirectly(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="testers")
        self.namespace = Namespace.objects.get(id=1)
        self.namespace.name = "test-ns"
        self.namespace.save(update_fields=["name"])
        self.namespace.read_groups.add(self.group)

        self.member = User.objects.create_user(username="member", password="pass")
        self.member.groups.add(self.group)

        self.loner = User.objects.create_user(username="loner", password="pass")

        self.superuser = User.objects.create_superuser(
            username="admin", password="pass", email="admin@example.com"
        )

    def test_superuser_has_access(self):
        from django_fsspec.webdav.permissions import has_namespace_perm

        assert has_namespace_perm(self.superuser, 1, require_write=True) is True

    def test_global_permission_grants_access(self):
        from django_fsspec.webdav.permissions import has_namespace_perm

        perm = Permission.objects.get(codename="read_namespace")
        self.loner.user_permissions.add(perm)
        assert has_namespace_perm(self.loner, 1) is True

    def test_group_member_has_access(self):
        from django_fsspec.webdav.permissions import has_namespace_perm

        assert has_namespace_perm(self.member, 1) is True

    def test_user_without_groups_is_denied(self):
        from django_fsspec.webdav.permissions import has_namespace_perm

        assert has_namespace_perm(self.loner, 1) is False

    def test_anonymous_user_is_denied(self):
        from django.contrib.auth.models import AnonymousUser
        from django_fsspec.webdav.permissions import has_namespace_perm

        assert has_namespace_perm(AnonymousUser(), 1) is False
        assert has_namespace_perm(None, 1) is False

    def test_missing_namespace_is_denied(self):
        from django_fsspec.webdav.permissions import has_namespace_perm

        assert has_namespace_perm(self.member, 999) is False


class TestWebDAVViewEdgeCases(WebDAVTestCase):
    def test_namespace_not_found(self):
        url = reverse("webdav", kwargs={"namespace_id": 999, "webdav_path": "file.txt"})
        response = self.client.get(
            url, HTTP_AUTHORIZATION=_basic_auth("reader", "reader-pass")
        )
        assert response.status_code == 404

    def test_invalid_path_characters(self):
        url = reverse(
            "webdav", kwargs={"namespace_id": 1, "webdav_path": "../etc/passwd"}
        )
        response = self.client.get(
            url, HTTP_AUTHORIZATION=_basic_auth("reader", "reader-pass")
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_path"

    def test_unsupported_method(self):
        response = self._request("lock", "file.txt", user=self.reader)
        assert response.status_code == 405

    def test_post_method_not_allowed(self):
        response = self._request("post", "file.txt", user=self.reader)
        assert response.status_code == 405

    def test_invalid_depth_header(self):
        write_file(1, "/depth.txt", b"x")

        response = self._request(
            "propfind", "depth.txt", user=self.reader, HTTP_DEPTH="invalid"
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_depth"

    def test_propfind_root(self):
        write_file(1, "/root-file.txt", b"x")

        response = self._request("propfind", "", user=self.reader)
        assert response.status_code == 207
        body = response.content.decode("utf-8")
        assert "root-file.txt" in body

    def test_head_existing_file(self):
        write_file(1, "/head.txt", b"hello", content_type="text/plain")

        response = self._request("head", "head.txt", user=self.reader)
        assert response.status_code == 200
        assert response.content == b""
        assert response["Content-Type"] == "text/plain"

    def test_mkcol_root(self):
        response = self._request("mkcol", "", user=self.writer)
        assert response.status_code == 405

    def test_mkcol_parent_is_file(self):
        write_file(1, "/parent", b"x")

        response = self._request("mkcol", "parent/child", user=self.writer)
        assert response.status_code == 409

    def test_copy_same_path(self):
        write_file(1, "/same.txt", b"x")

        response = self._request(
            "copy",
            "same.txt",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/same.txt",
        )
        assert response.status_code == 204

    def test_copy_source_directory(self):
        write_file(1, "/src-dir/file.txt", b"x")

        response = self._request(
            "copy",
            "src-dir",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/dst-dir",
        )
        assert response.status_code == 409
        assert response.json()["error"] == "is_directory"

    def test_move_source_missing(self):
        response = self._request(
            "move",
            "missing.txt",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/dst.txt",
        )
        assert response.status_code == 404

    def test_move_overwrite_existing(self):
        write_file(1, "/move-src.txt", b"new")
        write_file(1, "/move-dst.txt", b"old")

        response = self._request(
            "move",
            "move-src.txt",
            user=self.writer,
            HTTP_DESTINATION="http://testserver/webdav/1/move-dst.txt",
            HTTP_OVERWRITE="T",
        )
        assert response.status_code == 204

        response = self._request("get", "move-dst.txt", user=self.writer)
        assert self._response_content(response) == b"new"

    def test_put_file_too_large(self):
        with override_settings(DJANGO_FSSPEC_MAX_FILE_SIZE=10):
            response = self._request(
                "put", "big.txt", user=self.writer, data=b"x" * 11
            )
        assert response.status_code == 413
        assert response.json()["error"] == "file_too_large"

    def test_copy_invalid_destination(self):
        write_file(1, "/src.txt", b"x")

        response = self._request(
            "copy", "src.txt", user=self.writer, HTTP_DESTINATION="not-a-url"
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_destination"

    def test_unauthorized_view_without_middleware(self):
        """WebDAVView returns 401 when request.user is not authenticated."""
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from django_fsspec.webdav.views import WebDAVView

        factory = RequestFactory()
        request = factory.get("/webdav/1/file.txt")
        request.user = AnonymousUser()

        view = WebDAVView.as_view()
        response = view(request, namespace_id=1, webdav_path="file.txt")
        assert response.status_code == 401
        assert 'Basic realm="django-fsspec"' in response["WWW-Authenticate"]


class TestUtilsDirectly(TestCase):
    def test_http_date_with_naive_datetime(self):
        from datetime import datetime

        from django_fsspec.webdav.utils import http_date

        dt = datetime(2026, 6, 28, 12, 0, 0)
        assert http_date(dt) == "Sun, 28 Jun 2026 12:00:00 GMT"

    def test_resolve_content_type_guesses_from_path(self):
        from django_fsspec.webdav.utils import resolve_content_type

        assert resolve_content_type("file.json") == "application/json"
        assert resolve_content_type("file.unknown") == "application/octet-stream"

    def test_resolve_content_type_uses_provided_value(self):
        from django_fsspec.webdav.utils import resolve_content_type

        assert resolve_content_type("file.json", "text/plain") == "text/plain"

    def test_ensure_namespace_exists_raises_when_missing(self):
        from django_fsspec.webdav.utils import ensure_namespace_exists

        with pytest.raises(FileNotFoundError):
            ensure_namespace_exists(999)

    def test_is_collection(self):
        from django_fsspec.webdav.utils import is_collection

        Namespace.objects.get(id=1)
        write_file(1, "/dir/file.txt", b"x")

        assert is_collection("/dir", 1) is True
        assert is_collection("/dir/file.txt", 1) is False
        assert is_collection("/missing", 1) is False

    def test_parse_destination_missing_header(self):
        from django.test import RequestFactory

        from django_fsspec.webdav.utils import parse_destination

        factory = RequestFactory()
        request = factory.get("/webdav/1/src.txt")

        with pytest.raises(ValueError, match="Missing Destination"):
            parse_destination(request, 1, "/src.txt")

    def test_parse_destination_outside_mount(self):
        from django.test import RequestFactory

        from django_fsspec.webdav.utils import parse_destination

        factory = RequestFactory()
        request = factory.get("/webdav/1/src.txt")
        request.META["HTTP_DESTINATION"] = "http://testserver/webdav/2/dst.txt"

        with pytest.raises(ValueError, match="outside"):
            parse_destination(request, 1, "/src.txt")

    def test_parse_destination_wrong_host(self):
        from django.test import RequestFactory

        from django_fsspec.webdav.utils import parse_destination

        factory = RequestFactory()
        request = factory.get("/webdav/1/src.txt")
        request.META["HTTP_HOST"] = "testserver"
        request.META["HTTP_DESTINATION"] = "http://otherhost/webdav/1/dst.txt"

        with pytest.raises(ValueError, match="host"):
            parse_destination(request, 1, "/src.txt")


class TestResponsesEdgeCases(TestCase):
    def test_propfind_missing_child_is_ignored(self):
        from unittest.mock import patch

        from django_fsspec.webdav.responses import propfind_response

        Namespace.objects.get(id=1)
        write_file(1, "/parent/file.txt", b"x")

        def fake_get_file_info(ns, path):
            if path == "/parent":
                return {"name": "/parent", "size": 0, "type": "directory"}
            raise FileNotFoundError("gone")

        with patch(
            "django_fsspec.webdav.responses.operations.get_file_info",
            side_effect=fake_get_file_info,
        ):
            with patch(
                "django_fsspec.webdav.responses.operations.list_directory_detail",
                return_value=[{"name": "/parent/file.txt"}],
            ):
                response = propfind_response(1, "/parent", 1, "/webdav/1/")

        assert response.status_code == 207

    def test_options_response(self):
        from django_fsspec.webdav.responses import options_response

        response = options_response()
        assert response.status_code == 204
        assert response["DAV"] == "1"
        assert "MKCOL" in response["Allow"]
