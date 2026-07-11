# 快速入门

## 安装

```bash
pip install django-fsspec
```

## 配置

### 1. 添加到 INSTALLED_APPS

```python
INSTALLED_APPS = [
    ...
    "django_fsspec",
]
```

### 2. 运行数据库迁移

```bash
python manage.py migrate
```

### 3. 可选配置

在 Django `settings.py` 中：

```python
# 块大小（字节），默认 32KB
DJANGO_FSSPEC_BLOCK_SIZE = 32 * 1024

# 文件大小上限（字节），默认 2MB
DJANGO_FSSPEC_MAX_FILE_SIZE = 2 * 1024 * 1024
```

### 4. Namespace 使用建议

namespace 是整数，调用方自行管理。推荐在 settings 中定义常量：

```python
# settings.py
FSSPEC_NS_UPLOADS = 1
FSSPEC_NS_CONFIGS = 2
FSSPEC_NS_TEMPLATES = 3
```

## 第一个示例

```python
import fsspec

fs = fsspec.filesystem("django", namespace_id=1)

# 写入文件
with fs.open("/hello.txt", "wb") as f:
    f.write(b"Hello World")

# 读取文件
data = fs.cat("/hello.txt")
print(data)  # b"Hello World"

# 列目录
print(fs.ls("/"))  # ["/hello.txt"]

# 删除文件
fs.rm("/hello.txt")
```

如果在独立脚本、worker 或 notebook 中使用，请先初始化 Django：

```python
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "your_project.settings")
django.setup()
```
