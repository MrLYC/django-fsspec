# Getting Started

## Installation

```bash
pip install django-fsspec
```

## Configuration

### 1. Add to INSTALLED_APPS

```python
INSTALLED_APPS = [
    ...
    "django_fsspec",
]
```

### 2. Run migrations

```bash
python manage.py migrate
```

### 3. Optional settings

In your Django `settings.py`:

```python
# Block size in bytes (default: 256KB)
DJANGO_FSSPEC_BLOCK_SIZE = 64 * 1024

# Max file size in bytes (default: 2MB)
DJANGO_FSSPEC_MAX_FILE_SIZE = 2 * 1024 * 1024
```

### 4. Namespace recommendations

Namespaces are integers managed by the caller. Define constants in settings:

```python
FSSPEC_NS_UPLOADS = 1
FSSPEC_NS_CONFIGS = 2
FSSPEC_NS_TEMPLATES = 3
```

## First example

```python
import fsspec

fs = fsspec.filesystem("django", namespace=0)

# Write
with fs.open("/hello.txt", "wb") as f:
    f.write(b"Hello World")

# Read
data = fs.cat("/hello.txt")
print(data)  # b"Hello World"

# List
print(fs.ls("/"))  # ["/hello.txt"]

# Delete
fs.rm("/hello.txt")
```
