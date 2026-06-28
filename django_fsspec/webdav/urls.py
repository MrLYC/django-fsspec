from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from . import views


urlpatterns = [
    path(
        "<int:namespace_id>/",
        csrf_exempt(views.WebDAVView.as_view()),
        name="webdav_root",
    ),
    path(
        "<int:namespace_id>/<path:webdav_path>",
        csrf_exempt(views.WebDAVView.as_view()),
        name="webdav",
    ),
]
