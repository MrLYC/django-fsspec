# 管理命令

## fsspec_namespace — 命名空间管理

```bash
python manage.py fsspec_namespace list
python manage.py fsspec_namespace show default
python manage.py fsspec_namespace show --id 1
python manage.py fsspec_namespace create media --description "媒体文件"
python manage.py fsspec_namespace create media --read-group readers --write-group writers
python manage.py fsspec_namespace update media --description "更新后的描述"
python manage.py fsspec_namespace update media --clear-read-groups
python manage.py fsspec_namespace delete media
```

默认命名空间由迁移创建，`id=1`，`name=default`，该命令不允许删除默认命名空间。

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
python manage.py fsspec_fsck --namespace 1

# 输出机器可读结果
python manage.py fsspec_fsck --json
```

检查项目：
- 块级 checksum 是否匹配
- 块级 size 是否匹配
- 文件级 checksum 是否匹配
- 文件级 size 是否匹配
- 块序号是否连续
- 是否存在孤立的文件块（指向空闲存储块）
- 文件路径是否同时拥有子孙路径
- 目录节点是否带有块映射或 payload 元数据
- 持久化 path 和 node_type 是否有效
- 存储块是否被多个文件同时引用

示例输出：
```
Checking block checksums...
  Checked 150 blocks
Checking file checksums...
  Checked 50 files
Checking for orphaned blocks...

Filesystem check passed. No errors found.
```

JSON 输出形如 `{"ok": false, "findings": [...]}`。每个 finding 都包含
`severity`、`code`、`message` 和相关 id。severity 含义如下：

| Severity | 含义 |
|----------|------|
| `warning` | 可疑，但不一定影响当前读取 |
| `recoverable` | 存在可预览的安全修复 |
| `unresolved` | 需要显式恢复策略或从备份恢复 |
| `critical` | 应停止普通写入并先排查 |

## fsspec_repair — 尽力修复

通过一条命令修复仍可恢复的数据库破坏：

```bash
python manage.py fsspec_repair --dry-run
python manage.py fsspec_repair
python manage.py fsspec_repair --namespace 1
python manage.py fsspec_repair --recover-path-conflicts
```

建议的事故处理流程：

1. 先备份数据库，或先在恢复副本上执行。
2. 运行 `python manage.py fsspec_repair --dry-run` 查看计划修改。
3. 如果输出包含 `path_conflicts`，先检查结果，并判断是否接受把子孙路径移动到恢复前缀。
4. 运行 `python manage.py fsspec_repair` 应用安全修复。
5. 只有在已备份且明确希望移动冲突子孙路径时，才运行
   `python manage.py fsspec_repair --recover-path-conflicts`。
6. 运行 `python manage.py fsspec_fsck` 验证结果。

可修复的场景：

| 破坏场景 | 修复行为 |
|----------|----------|
| `StorageBlock.size` 或 `StorageBlock.checksum` 被异常改动 | 根据当前块字节重新计算两个字段 |
| `FileNode.size` 或 `FileNode.checksum` 被异常改动 | 用当前映射的块重组文件，并重新计算文件元数据 |
| 活动 `StorageBlock` 被错误标记为 `is_free=True` | 把仍被引用的块重新标记为已使用 |
| `FileBlock.sequence` 有缺口或不是从 0 开始 | 按现有映射顺序重编号为连续的 `0..N-1` |
| 目录记录带有块映射或 payload 元数据 | 删除不可能存在的映射，并重置目录 size/checksum |
| 已用块没有任何 `FileBlock` 所有者 | 全局修复时将其标记为空闲块 |
| 文件路径同时拥有子孙路径 | 报告 `path_conflicts`；使用 `--recover-path-conflicts` 时，把子孙路径移动到 `/__django_fsspec_recovered__/conflicts/<namespace>/<timestamp>/...` |

边界：

- 命令无法凭空恢复已从 `StorageBlock.data` 删除或覆盖的字节。
- 如果 `FileBlock` 行被删除，残留的孤立块字节已经没有可信的路径归属。修复会按仍存在的映射重算文件，并释放孤立块，让数据库回到一致状态。
- 如果块映射被调换但 sequence 仍保持连续，数据库里没有权威信号可推断原始顺序。需要保留原始字节顺序时，应从备份恢复。
- 共享存储块和非法持久化路径会作为 unresolved damage 报告，不会自动修改，因为仅凭损坏行无法证明正确所有权或合法目标路径。
- 使用 `--namespace` 可以只修复一个 namespace 的文件和映射。未指定 namespace 时才会执行全局孤立块清理。

示例输出：

```
Repairing filesystem metadata...

block_metadata: 1
free_referenced_blocks: 1
unreferenced_used_blocks: 1
directory_mappings: 0
directory_metadata: 0
file_sequences: 1
file_metadata: 2
path_conflicts: 0
moved_descendants: 0
shared_blocks: 0
invalid_paths: 0

Applied 6 repair actions. Run fsspec_fsck to verify.
```

## fsspec_stats — 统计信息

```bash
python manage.py fsspec_stats
python manage.py fsspec_stats --namespace 1
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
