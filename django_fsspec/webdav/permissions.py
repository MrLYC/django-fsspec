from django.contrib.auth.models import AnonymousUser

from ..models import Namespace


def has_namespace_perm(user, namespace_id: int, require_write: bool = False) -> bool:
    """Check whether ``user`` has access to ``namespace_id``.

    Permission resolution order:
    1. Superusers are always allowed.
    2. Users with the global ``read_namespace`` / ``write_namespace`` permission
       are allowed for all namespaces.
    3. Otherwise, membership in the namespace's ``read_groups`` or
       ``write_groups`` is checked.
    """
    if user is None or isinstance(user, AnonymousUser) or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    global_perms = ["django_fsspec.write_namespace"]
    if not require_write:
        global_perms.append("django_fsspec.read_namespace")
    if any(user.has_perm(perm) for perm in global_perms):
        return True

    try:
        namespace = Namespace.objects.get(id=namespace_id)
    except Namespace.DoesNotExist:
        return False

    user_group_ids = list(user.groups.values_list("id", flat=True))
    if not user_group_ids:
        return False

    if require_write:
        return namespace.write_groups.filter(id__in=user_group_ids).exists()

    # Writers can read as well.
    return (
        namespace.read_groups.filter(id__in=user_group_ids).exists()
        or namespace.write_groups.filter(id__in=user_group_ids).exists()
    )
