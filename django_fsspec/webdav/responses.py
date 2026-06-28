import urllib.parse
from xml.etree import ElementTree as ET

ET.register_namespace("D", "DAV:")

from django.http import HttpResponse, JsonResponse

from .. import operations
from .utils import http_date, iso_date, make_etag

DAV_NS = "DAV:"
DAV_PREFIX = "{DAV:}"


def error_response(status: int, code: str, message: str, path: str = ""):
    """Return a JSON error body for WebDAV failures."""
    data = {"error": code, "message": message}
    if path:
        data["path"] = path
    return JsonResponse(data, status=status)


def options_response() -> HttpResponse:
    """Return an OPTIONS response advertising class-1 WebDAV support."""
    response = HttpResponse(status=204)
    response["DAV"] = "1"
    response["Allow"] = "OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, COPY, MOVE, MKCOL"
    response["MS-Author-Via"] = "DAV"
    return response


def propfind_response(
    namespace_id: int, path: str, depth: int, href_prefix: str
) -> HttpResponse:
    """Return a 207 multistatus XML response for PROPFIND.

    ``href_prefix`` must end with a slash, e.g. ``/webdav/1/``.
    """
    root = ET.Element(f"{DAV_PREFIX}multistatus")

    _append_item(root, namespace_id, path, href_prefix)

    if depth == 1:
        try:
            children = operations.list_directory_detail(namespace_id, path)
        except FileNotFoundError:
            children = []
        for child in children:
            _append_item(root, namespace_id, child["name"], href_prefix)

    xml_bytes = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )
    return HttpResponse(
        xml_bytes,
        content_type="text/xml; charset=utf-8",
        status=207,
    )


def _append_item(root: ET.Element, namespace_id: int, path: str, href_prefix: str):
    try:
        info = operations.get_file_info(namespace_id, path)
    except FileNotFoundError:
        return

    is_dir = info.get("type") == "directory"

    response = ET.SubElement(root, f"{DAV_PREFIX}response")

    href = ET.SubElement(response, f"{DAV_PREFIX}href")
    href.text = _build_href(href_prefix, path, is_dir)

    propstat = ET.SubElement(response, f"{DAV_PREFIX}propstat")
    prop = ET.SubElement(propstat, f"{DAV_PREFIX}prop")

    displayname = ET.SubElement(prop, f"{DAV_PREFIX}displayname")
    displayname.text = _display_name(path)

    resourcetype = ET.SubElement(prop, f"{DAV_PREFIX}resourcetype")
    if is_dir:
        ET.SubElement(resourcetype, f"{DAV_PREFIX}collection")
    else:
        length = ET.SubElement(prop, f"{DAV_PREFIX}getcontentlength")
        length.text = str(info.get("size", 0))

        etag = ET.SubElement(prop, f"{DAV_PREFIX}getetag")
        etag.text = make_etag(info.get("checksum", ""))

        content_type = info.get("content_type") or "application/octet-stream"
        ct = ET.SubElement(prop, f"{DAV_PREFIX}getcontenttype")
        ct.text = content_type

        updated = info.get("updated")
        if updated:
            lastmod = ET.SubElement(prop, f"{DAV_PREFIX}getlastmodified")
            lastmod.text = http_date(updated)

        created = info.get("created")
        if created:
            creation = ET.SubElement(prop, f"{DAV_PREFIX}creationdate")
            creation.text = iso_date(created)

    status = ET.SubElement(propstat, f"{DAV_PREFIX}status")
    status.text = "HTTP/1.1 200 OK"


def _build_href(href_prefix: str, path: str, is_dir: bool) -> str:
    relative = urllib.parse.quote(path.lstrip("/"))
    href = href_prefix + relative
    if is_dir and not href.endswith("/"):
        href += "/"
    return href


def _display_name(path: str) -> str:
    if path == "/":
        return ""
    return path.rstrip("/").split("/")[-1]


__all__ = [
    "error_response",
    "options_response",
    "propfind_response",
]
