# 管理命令

## fsspec_gc — 清理空闲块

```bash
# 清理所有空闲块
python manage.py fsspec_gc

# 保留 100 个空闲块用于检查
python manage.py fsspec_gc --keep 100

# 预览，不实际删除
python manage.py fsspec_gc --dry-run
```

示例输出：
```
Deleted 42 free blocks (kept 0)
```

## fsspec_fsck — 文件系统一致性检查

```bash
# 检查所有文件
python manage.py fsspec_fsck

# 只检查特定命名空间
python manage.py fsspec_fsck --namespace 0
```

检查项目：
- 块级 checksum 是否匹配
- 块级 size 是否匹配
- 文件级 checksum 是否匹配
- 文件级 size 是否匹配
- 块序号是否连续
- 是否存在孤立的文件块（指向空闲存储块）

示例输出：
```
Checking block checksums...
  Checked 150 blocks
Checking file checksums...
  Checked 50 files
Checking for orphaned blocks...

Filesystem check passed. No errors found.
```

## fsspec_stats — 统计信息

```bash
python manage.py fsspec_stats
python manage.py fsspec_stats --namespace 0
```

示例输出：
```
Django-fsspec Statistics
========================================
Namespaces:       3
Files:            1250
Total file size:  15.2 MB
Storage blocks:   1300
  Used:           1250
  Free:           50
Block data size:  15.4 MB
File-block maps:  1250
```
