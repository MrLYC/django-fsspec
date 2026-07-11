from django.apps import apps
from django.conf import settings
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured


def ensure_django_ready():
    try:
        settings.INSTALLED_APPS
    except ImproperlyConfigured as exc:
        raise ImproperlyConfigured(
            "django-fsspec requires configured Django settings. Set "
            "DJANGO_SETTINGS_MODULE or call django.conf.settings.configure() "
            "before using DjangoFileSystem or fsspec.filesystem('django')."
        ) from exc

    if not apps.ready:
        raise AppRegistryNotReady(
            "django-fsspec requires Django apps to be loaded. Call "
            "django.setup() before using DjangoFileSystem or "
            "fsspec.filesystem('django') outside a running Django application."
        )
