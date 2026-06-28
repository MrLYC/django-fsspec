from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .. import operations
from ..exceptions import FileConflictError, FileTooLargeError, PathValidationError
from ..fs import DjangoFileSystem
from . import responses
from .permissions import has_namespace_perm
from .utils import (
    ensure_namespace_exists,
    http_date,
    is_collection,
    make_etag,
    normalize_webdav_path,
    parse_destination,
    resolve_content_type,
)

_WRITE_METHODS = {"PUT", "DELETE", "COPY", "MOVE", "MKCOL", "PROPPATCH"}


@method_decorator(csrf_exempt, name="dispatch")
class WebDAVView(View):
    """A lightweight WebDAV endpoint for django-fsspec."""

    http_method_names = [
        "get",
        "head",
        "post",
        "put",
        "delete",
        "options",
        "trace",
        "propfind",
        "proppatch",
        "mkcol",
        "copy",
        "move",
        "lock",
        "unlock",
    ]

    def dispatch(self, request, namespace_id: int, webdav_path: str = "", **kwargs):
        try:
            path = normalize_webdav_path(webdav_path)
            ensure_namespace_exists(namespace_id)
        except FileNotFoundError as exc:
            return responses.error_response(404, "not_found", str(exc))
        except PathValidationError as exc:
            return responses.error_response(
                400, "invalid_path", str(exc), path=webdav_path
            )

        user = getattr(request, "user", None)
        require_write = request.method.upper() in _WRITE_METHODS
        if require_write and not getattr(
            request, "django_fsspec_webdav_basic_auth", False
        ):
            return self._unauthorized()
        if not has_namespace_perm(user, namespace_id, require_write):
            if not user or not getattr(user, "is_authenticated", False):
                return self._unauthorized()
            return responses.error_response(
                403, "permission_denied", "Access denied", path=path
            )

        method = request.method.upper()
        if method == "PROPFIND":
            handler = self.propfind
        elif method == "MKCOL":
            handler = self.mkcol
        elif method == "COPY":
            handler = self.copy
        elif method == "MOVE":
            handler = self.move
        else:
            handler = getattr(self, method.lower(), None)
            if handler is None:
                return self.http_method_not_allowed(request)

        try:
            return handler(request, namespace_id, path)
        except FileNotFoundError as exc:
            return responses.error_response(404, "not_found", str(exc), path=path)
        except FileExistsError as exc:
            return responses.error_response(409, "already_exists", str(exc), path=path)
        except (IsADirectoryError, NotADirectoryError) as exc:
            return responses.error_response(409, "is_directory", str(exc), path=path)
        except PathValidationError as exc:
            return responses.error_response(400, "invalid_path", str(exc), path=path)
        except FileTooLargeError as exc:
            return responses.error_response(
                413, "file_too_large", str(exc), path=path
            )
        except FileConflictError as exc:
            return responses.error_response(409, "conflict", str(exc), path=path)
        except ValueError as exc:
            return responses.error_response(
                400, "invalid_request", str(exc), path=path
            )

    def _unauthorized(self):
        response = HttpResponse(
            "Authentication required",
            status=401,
            content_type="text/plain; charset=utf-8",
        )
        response["WWW-Authenticate"] = 'Basic realm="django-fsspec"'
        return response

    def _href_prefix(self, request, path: str) -> str:
        """Compute the href prefix for PROPFIND responses."""
        if path == "/":
            prefix = request.path_info
        else:
            prefix = request.path_info[: -len(path)]
        if not prefix.endswith("/"):
            prefix += "/"
        return prefix

    # ------------------------------------------------------------------
    # WebDAV method handlers
    # ------------------------------------------------------------------

    def options(self, request, namespace_id: int, path: str):
        return responses.options_response()

    def propfind(self, request, namespace_id: int, path: str):
        depth_header = request.headers.get("Depth", "1").strip().lower()
        if depth_header == "0":
            depth = 0
        elif depth_header == "1":
            depth = 1
        elif depth_header == "infinity":
            return responses.error_response(
                403, "unsupported_depth", "Depth infinity is not supported", path=path
            )
        else:
            return responses.error_response(
                400, "invalid_depth", f"Unsupported Depth: {depth_header}", path=path
            )

        try:
            operations.get_file_info(namespace_id, path)
        except FileNotFoundError as exc:
            return responses.error_response(404, "not_found", str(exc), path=path)

        href_prefix = self._href_prefix(request, path)
        return responses.propfind_response(namespace_id, path, depth, href_prefix)

    def get(self, request, namespace_id: int, path: str):
        try:
            info = operations.get_file_info(namespace_id, path)
        except FileNotFoundError as exc:
            return responses.error_response(404, "not_found", str(exc), path=path)

        if info.get("type") == "directory":
            return responses.error_response(
                404, "not_found", "Path is a directory", path=path
            )

        data = operations.read_file(namespace_id, path)
        response = HttpResponse(
            data,
            content_type=info.get("content_type") or "application/octet-stream",
        )
        response["ETag"] = make_etag(info.get("checksum", ""))
        response["Last-Modified"] = http_date(info.get("updated"))
        return response

    def head(self, request, namespace_id: int, path: str):
        response = self.get(request, namespace_id, path)
        response.content = b""
        return response

    def put(self, request, namespace_id: int, path: str):
        if path == "/":
            return responses.error_response(
                405, "method_not_allowed", "Cannot write root collection", path=path
            )
        existed = operations.file_exists(namespace_id, path)

        if request.headers.get("If-None-Match") == "*" and existed:
            return responses.error_response(
                412, "precondition_failed", "File already exists", path=path
            )

        content_type = resolve_content_type(
            path, request.headers.get("Content-Type", "")
        )
        operations.write_file(
            namespace_id, path, request.body, content_type=content_type
        )
        return HttpResponse(status=204 if existed else 201)

    def delete(self, request, namespace_id: int, path: str):
        if path == "/":
            return responses.error_response(
                405, "method_not_allowed", "Cannot delete root collection", path=path
            )
        operations.delete_file(namespace_id, path, recursive=True)
        return HttpResponse(status=204)

    def mkcol(self, request, namespace_id: int, path: str):
        if path == "/":
            return responses.error_response(
                405, "method_not_allowed", "Cannot create root collection", path=path
            )

        if operations.file_exists(namespace_id, path):
            return responses.error_response(
                405, "method_not_allowed", "Collection already exists", path=path
            )

        fs = DjangoFileSystem(namespace=namespace_id)
        fs.mkdir(path, create_parents=False)
        return HttpResponse(status=201)

    def copy(self, request, namespace_id: int, path: str):
        return self._copy_or_move(request, namespace_id, path, is_copy=True)

    def move(self, request, namespace_id: int, path: str):
        return self._copy_or_move(request, namespace_id, path, is_copy=False)

    def _copy_or_move(
        self, request, namespace_id: int, path: str, *, is_copy: bool
    ):
        try:
            dest_path = parse_destination(request, namespace_id, path)
        except ValueError as exc:
            return responses.error_response(
                400, "invalid_destination", str(exc), path=path
            )

        if dest_path == path:
            return HttpResponse(status=204)

        if path == "/" or dest_path == "/":
            return responses.error_response(
                405,
                "method_not_allowed",
                "Cannot copy or move root collection",
                path=path,
            )

        try:
            source_info = operations.get_file_info(namespace_id, path)
        except FileNotFoundError as exc:
            return responses.error_response(404, "not_found", str(exc), path=path)

        if source_info.get("type") == "directory":
            return responses.error_response(
                409, "is_directory", "Source is a directory", path=path
            )

        overwrite = request.headers.get("Overwrite", "T").upper() != "F"
        dest_exists = operations.file_exists(namespace_id, dest_path)

        if dest_exists and not overwrite:
            return responses.error_response(
                412, "precondition_failed", "Destination already exists", path=dest_path
            )

        if is_copy:
            operations.copy_file(namespace_id, path, dest_path)
        else:
            operations.move_file(namespace_id, path, dest_path, overwrite=overwrite)

        return HttpResponse(status=204 if dest_exists else 201)
