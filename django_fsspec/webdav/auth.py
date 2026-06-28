import base64
import binascii

from django.conf import settings
from django.contrib.auth import authenticate


class BasicAuthMiddleware:
    """HTTP Basic Auth middleware for WebDAV endpoints.

    Only intercepts requests whose path starts with
    ``DJANGO_FSSPEC_WEBDAV_PATH_PREFIX``. It is stateless: no session is
    created or consulted.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.path_prefix = getattr(
            settings, "DJANGO_FSSPEC_WEBDAV_PATH_PREFIX", "/webdav/"
        )
        if not self.path_prefix.endswith("/"):
            self.path_prefix += "/"

    def __call__(self, request):
        if not request.path_info.startswith(self.path_prefix):
            return self.get_response(request)

        user = self._authenticate(request)
        if user is None:
            return self._unauthorized()

        request.user = user
        request.django_fsspec_webdav_basic_auth = True
        return self.get_response(request)

    def _authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return None

        encoded = header[6:].strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None

        if ":" not in decoded:
            return None

        username, password = decoded.split(":", 1)
        return authenticate(request, username=username, password=password)

    def _unauthorized(self):
        from django.http import HttpResponse

        response = HttpResponse(
            "Authentication required",
            status=401,
            content_type="text/plain; charset=utf-8",
        )
        response["WWW-Authenticate"] = 'Basic realm="django-fsspec"'
        return response
