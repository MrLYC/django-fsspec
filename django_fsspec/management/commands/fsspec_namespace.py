from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from django_fsspec.models import Namespace


class Command(BaseCommand):
    help = "Manage django-fsspec namespaces"

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="subcommand", required=True)

        subparsers.add_parser("list", help="List namespaces")

        show_parser = subparsers.add_parser("show", help="Show namespace details")
        show_parser.add_argument("name", nargs="?", help="Namespace name")
        show_parser.add_argument("--id", type=int, dest="namespace_id", help="Namespace ID")

        create_parser = subparsers.add_parser("create", help="Create a namespace")
        create_parser.add_argument("name", help="Namespace name")
        create_parser.add_argument("--description", default="", help="Namespace description")
        create_parser.add_argument("--read-group", action="append", default=[], help="Read group name")
        create_parser.add_argument("--write-group", action="append", default=[], help="Write group name")

        update_parser = subparsers.add_parser("update", help="Update a namespace")
        update_parser.add_argument("name", help="Namespace name")
        update_parser.add_argument("--description", help="Namespace description")
        update_parser.add_argument("--read-group", action="append", default=[], help="Read group name")
        update_parser.add_argument("--write-group", action="append", default=[], help="Write group name")
        update_parser.add_argument("--clear-read-groups", action="store_true", help="Clear read groups")
        update_parser.add_argument("--clear-write-groups", action="store_true", help="Clear write groups")

        delete_parser = subparsers.add_parser("delete", help="Delete an empty namespace")
        delete_parser.add_argument("name", help="Namespace name")

    def handle(self, *args, **options):
        subcommand = options["subcommand"]
        if subcommand == "list":
            self._handle_list()
        elif subcommand == "show":
            self._handle_show(options["name"], options["namespace_id"])
        elif subcommand == "create":
            self._handle_create(options)
        elif subcommand == "update":
            self._handle_update(options)
        elif subcommand == "delete":
            self._handle_delete(options["name"])

    def _handle_list(self):
        namespaces = Namespace.objects.order_by("id")
        if not namespaces.exists():
            self.stdout.write("No namespaces found.")
            return

        self.stdout.write(f"{'ID':<5} {'Name':<20} Description")
        for namespace in namespaces:
            self.stdout.write(
                f"{namespace.id:<5} {namespace.name:<20} {namespace.description}"
            )

    def _handle_show(self, name, namespace_id):
        namespace = self._get_namespace(name, namespace_id)
        self.stdout.write(f"Namespace {namespace.id}")
        self.stdout.write(f"Name:          {namespace.name}")
        self.stdout.write(f"Description:   {namespace.description or '-'}")
        self.stdout.write(f"Read groups:   {self._format_groups(namespace.read_groups.all())}")
        self.stdout.write(f"Write groups:  {self._format_groups(namespace.write_groups.all())}")
        self.stdout.write(f"Created at:    {namespace.created_at}")

    def _handle_create(self, options):
        name = options["name"]
        if Namespace.objects.filter(name=name).exists():
            raise CommandError(f"Namespace already exists: {name}")

        with transaction.atomic():
            read_groups = self._resolve_groups(options["read_group"])
            write_groups = self._resolve_groups(options["write_group"])
            namespace = Namespace(name=name, description=options["description"])
            self._validate_namespace(namespace)
            namespace.save()
            namespace.read_groups.set(read_groups)
            namespace.write_groups.set(write_groups)

        self.stdout.write(self.style.SUCCESS(f"Created namespace {namespace.id}: {namespace.name}"))

    def _handle_update(self, options):
        if options["read_group"] and options["clear_read_groups"]:
            raise CommandError("Use either --read-group or --clear-read-groups, not both")
        if options["write_group"] and options["clear_write_groups"]:
            raise CommandError("Use either --write-group or --clear-write-groups, not both")

        has_changes = (
            options["description"] is not None
            or bool(options["read_group"])
            or bool(options["write_group"])
            or options["clear_read_groups"]
            or options["clear_write_groups"]
        )
        if not has_changes:
            raise CommandError("No changes specified.")

        namespace = self._get_namespace_by_name(options["name"])
        with transaction.atomic():
            if options["description"] is not None:
                namespace.description = options["description"]
                self._validate_namespace(namespace)
                namespace.save(update_fields=["description"])
            if options["read_group"]:
                namespace.read_groups.set(self._resolve_groups(options["read_group"]))
            elif options["clear_read_groups"]:
                namespace.read_groups.clear()
            if options["write_group"]:
                namespace.write_groups.set(self._resolve_groups(options["write_group"]))
            elif options["clear_write_groups"]:
                namespace.write_groups.clear()

        self.stdout.write(self.style.SUCCESS(f"Updated namespace {namespace.id}: {namespace.name}"))

    def _handle_delete(self, name):
        namespace = self._get_namespace_by_name(name)
        if namespace.id == 1:
            raise CommandError("Cannot delete the default namespace.")
        if namespace.files.exists():
            raise CommandError("Namespace contains files and cannot be deleted.")

        namespace_id = namespace.id
        namespace_name = namespace.name
        namespace.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted namespace {namespace_id}: {namespace_name}"))

    def _get_namespace(self, name, namespace_id):
        if name and namespace_id is not None:
            raise CommandError("Use either namespace name or --id, not both")
        if not name and namespace_id is None:
            raise CommandError("Specify namespace name or --id")

        qs = Namespace.objects.prefetch_related("read_groups", "write_groups")
        try:
            if namespace_id is not None:
                return qs.get(id=namespace_id)
            return qs.get(name=name)
        except Namespace.DoesNotExist:
            if namespace_id is not None:
                raise CommandError(f"Namespace not found: {namespace_id}")
            raise CommandError(f"Namespace not found: {name}")

    def _get_namespace_by_name(self, name):
        try:
            return Namespace.objects.get(name=name)
        except Namespace.DoesNotExist:
            raise CommandError(f"Namespace not found: {name}")

    def _resolve_groups(self, group_names):
        if not group_names:
            return []

        groups = list(Group.objects.filter(name__in=group_names).order_by("name"))
        found = {group.name for group in groups}
        missing = sorted(set(group_names) - found)
        if missing:
            raise CommandError(f"Group not found: {', '.join(missing)}")
        return groups

    def _format_groups(self, groups):
        names = [group.name for group in sorted(groups, key=lambda group: group.name)]
        return ", ".join(names) if names else "-"

    def _validate_namespace(self, namespace):
        try:
            namespace.full_clean()
        except ValidationError as exc:
            messages = []
            for field_messages in exc.message_dict.values():
                messages.extend(field_messages)
            raise CommandError("; ".join(messages))
