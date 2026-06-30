import logging

from django.core.checks import Warning, register

logger = logging.getLogger("django_fsspec")

BLOCK_SIZE_MISMATCH_ID = "django_fsspec.W001"


@register()
def check_block_size_consistency(app_configs, **kwargs):
    """Django system check: warn if files exist with a different block_size
    than the current DJANGO_FSSPEC_BLOCK_SIZE setting.

    This means the setting was changed while existing files keep their stored
    block_size. Files still work, but the inconsistency may be unintentional.

    Silence with: SILENCED_SYSTEM_CHECKS = ["django_fsspec.W001"]
    """
    from .models import FileNode, get_block_size

    errors = []

    try:
        current_block_size = get_block_size()
        mismatched = (
            FileNode.objects.exclude(block_size=current_block_size)
            .values_list("block_size", flat=True)
            .distinct()
        )
        mismatched_sizes = list(mismatched)

        if mismatched_sizes:
            sizes_str = ", ".join(str(s) for s in sorted(mismatched_sizes))
            count = FileNode.objects.exclude(block_size=current_block_size).count()
            errors.append(
                Warning(
                    f"Found {count} file(s) with block_size ({sizes_str}) "
                    f"different from current setting ({current_block_size}). "
                    f"Run fsspec_rechunk to unify, or silence "
                    f"this check if intentional.",
                    hint=(
                        "Run 'python manage.py fsspec_rechunk "
                        f"--block-size {current_block_size} --dry-run' to "
                        "preview re-chunking existing files, or add "
                        "'django_fsspec.W001' to "
                        "SILENCED_SYSTEM_CHECKS."
                    ),
                    id=BLOCK_SIZE_MISMATCH_ID,
                )
            )
    except Exception:
        # Database may not be ready (e.g., before initial migration)
        pass

    return errors


def check_block_size_on_startup():
    """Called from AppConfig.ready() to log a warning if block sizes are
    inconsistent. Lighter than system check — just a log message."""
    from .models import FileNode, get_block_size

    try:
        current_block_size = get_block_size()
        count = FileNode.objects.exclude(block_size=current_block_size).count()
        if count > 0:
            sizes = sorted(
                FileNode.objects.exclude(block_size=current_block_size)
                .values_list("block_size", flat=True)
                .distinct()
            )
            logger.warning(
                "django-fsspec: %d file(s) have block_size %s, but current "
                "setting is %d. Consider running fsspec_rechunk or updating "
                "DJANGO_FSSPEC_BLOCK_SIZE.",
                count,
                sizes,
                current_block_size,
            )
    except Exception:
        pass
