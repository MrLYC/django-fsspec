# 块大小迁移

## 共存机制

修改 `DJANGO_FSSPEC_BLOCK_SIZE` 后，新旧文件可以共存：

- 每个 `FileNode` 记录了写入时的 `block_size`
- 读取时使用文件自身的 `block_size` 定位块
- 不需要迁移也能正常工作

## 何时需要迁移

如果你希望**全局统一**块大小（例如为了简化运维或优化存储），可以使用 `RechunkOperation`。

## 使用 RechunkOperation

### 1. 创建迁移文件

```bash
python manage.py makemigrations django_fsspec --empty -n rechunk_to_64k
```

### 2. 编辑迁移

```python
from django.db import migrations
from django_fsspec.migrations_ops import RechunkOperation

class Migration(migrations.Migration):
    dependencies = [
        ("django_fsspec", "0001_initial"),
    ]

    operations = [
        RechunkOperation(new_block_size=64 * 1024),
    ]
```

### 3. 执行迁移

```bash
python manage.py migrate
```

## 迁移流程

对每个 `block_size != new_block_size` 的文件：

1. 按序读出所有块数据，拼接为完整内容
2. 按新 `block_size` 重新切块
3. 单事务内：旧块标记 `is_free`，删除旧映射，写入新块和映射
4. 更新 `FileNode.block_size`

## 耗时估算

3 万个小文件（< 256KB），迁移通常在几分钟内完成。迁移后建议运行 `fsspec_gc` 清理旧的空闲块。
