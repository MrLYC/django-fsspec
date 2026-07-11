import os
import subprocess
import sys
import textwrap


def run_python(code, *, settings_module=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
    if settings_module is None:
        env.pop("DJANGO_SETTINGS_MODULE", None)
    else:
        env["DJANGO_SETTINGS_MODULE"] = settings_module

    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fs_module_imports_without_configured_settings():
    result = run_python(
        """
        import django_fsspec.fs
        print("ok")
        """
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_fs_module_imports_before_django_setup():
    result = run_python(
        """
        import django_fsspec.fs
        print("ok")
        """,
        settings_module="demo.settings",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_filesystem_creation_without_settings_has_clear_error():
    result = run_python(
        """
        import fsspec
        from django_fsspec.fs import DjangoFileSystem

        fsspec.register_implementation("django", DjangoFileSystem, clobber=True)

        try:
            fsspec.filesystem("django", skip_instance_cache=True)
        except Exception as exc:
            print(type(exc).__name__)
            print(exc)
        else:
            raise AssertionError("filesystem creation should fail")
        """
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "ImproperlyConfigured"
    assert "django-fsspec requires configured Django settings" in result.stdout
    assert "DJANGO_SETTINGS_MODULE" in result.stdout


def test_filesystem_creation_before_django_setup_has_clear_error():
    result = run_python(
        """
        import fsspec
        from django_fsspec.fs import DjangoFileSystem

        fsspec.register_implementation("django", DjangoFileSystem, clobber=True)

        try:
            fsspec.filesystem("django", skip_instance_cache=True)
        except Exception as exc:
            print(type(exc).__name__)
            print(exc)
        else:
            raise AssertionError("filesystem creation should fail")
        """,
        settings_module="demo.settings",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "AppRegistryNotReady"
    assert "django-fsspec requires Django apps to be loaded" in result.stdout
    assert "django.setup()" in result.stdout
