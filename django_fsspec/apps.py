from django.apps import AppConfig


class DjangoFsspecConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_fsspec"
    verbose_name = "Django Fsspec"

    def ready(self):
        # Register system checks (check_block_size_consistency)
        from . import checks  # noqa: F401

        # Schedule startup warning via post_migrate signal
        # (avoids database access during app initialization)
        from django.db.models.signals import post_migrate

        post_migrate.connect(self._post_migrate_check, sender=self)

    @staticmethod
    def _post_migrate_check(sender, **kwargs):
        from .checks import check_block_size_on_startup

        check_block_size_on_startup()
