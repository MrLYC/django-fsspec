from django.urls import include, path

urlpatterns = [
    path("webdav/", include("django_fsspec.webdav.urls")),
]
