# 管理命令

本文是命令参考。按场景组织的运维流程、事故处理顺序和边界见
[运维 Runbook](operations-runbook.md)。

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

## 运维退出码

`migrate`、`fsck`、`repair` 和 `rechunk` 使用稳定退出码，方便脚本调用：

| 退出码 | 含义 |
|--------|------|
| `0` | 命令完成，且没有需要运维关注的 finding |
| `1` | 命令完成，但 `migrate` 有跳过/冲突、`fsck` 发现损坏、`rechunk` 跳过文件，或 `repair` 仍有未解决损坏 |
| `2` | 参数错误或命令无法继续执行，例如 migrate options 非法或 `--on-error abort` |

`fsspec_repair` 成功应用安全修复且没有 unresolved damage 时，退出码仍为 `0`。

建议的完整事故处理流程：

```bash
python manage.py fsspec_fsck
python manage.py fsspec_repair --dry-run
python manage.py fsspec_repair
python manage.py fsspec_rechunk --block-size 32768 --dry-run
python manage.py fsspec_rechunk --block-size 32768
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
python manage.py fsspec_gc
```

## fsspec_migrate — 跨文件系统复制

在 fsspec 兼容文件系统之间复制文件：

```bash
python manage.py fsspec_migrate file:///mnt/import/ django://1/imports/
python manage.py fsspec_migrate django://1/uploads/ file:///mnt/export/uploads/
python manage.py fsspec_migrate django://1/a/ django://2/a-copy/ --limit 1000
python manage.py fsspec_migrate django://1/a/ file:///tmp/a/ --dry-run --json
python manage.py fsspec_migrate django://1/a/ file:///tmp/a/ --resume .django-fsspec-migrate/run.jsonl
```

`django://<namespace_id>/<path>` 可用于本包支持的 fsspec URL 场景。
namespace id 必须是整数。URL host 只用于选择 namespace，不表示认证身份。

重要参数：

```bash
python manage.py fsspec_migrate SOURCE_URI TARGET_URI \
  --source-options '{"anon": false}' \
  --target-options '{}' \
  --conflict skip \
  --verify checksum
```

- `--source-options` 和 `--target-options` 是传给
  `fsspec.core.url_to_fs()` 的 JSON object。它们不会写入 manifest 或 JSON
  输出，避免凭据进入命令产物。
- 默认是 copy-only：永远不删除源文件。
- 默认冲突策略是 `skip`；目标已存在时报告并以退出码 `1` 结束。
- `--conflict checksum` 只在源和目标字节一致时跳过已有目标。
- `--conflict overwrite` 会替换已有目标文件。
- 默认校验是 `checksum`；只有额外读取成本过高时才使用 `--verify size` 或
  `--verify off`。
- 真实执行默认生成 JSONL manifest。可用
  `DJANGO_FSSPEC_MIGRATE_MANIFEST_DIR` 或 `--manifest` 指定位置。
- 使用 `--resume <manifest>` 可跳过上一次成功的条目，并重试失败或冲突条目。

命令优先写入目标临时路径，再通过 `mv` 切换到最终路径；当文件系统不支持
move/rename 时回退为直接写目标。命令不会安装可选 fsspec backend；迁移到 S3 等
协议时，需要用户自行安装 `s3fs` 等包。

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
python manage.py fsspec_repair --dry-run --json
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

JSON 输出形如 `{"ok": true, "dry_run": false, "summary": {...}, "unresolved": false}`。
安全修复会体现在 `summary` 中；如果仍有未解决结构损坏，`unresolved` 为 `true`，
命令以退出码 `1` 结束。

## fsspec_rechunk — 块大小重写

把已有文件重写到目标块大小。这是可重复执行的运维命令，不是 Django migration。

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /uploads/ --limit 1000
python manage.py fsspec_rechunk --block-size 32768 --verify checksum
python manage.py fsspec_rechunk --block-size 32768 --json
```

重要行为：

- 只重写 `FileNode.block_size` 与 `--block-size` 不一致的文件。
- 每个文件独立事务处理。
- 已有文件不 rechunk 也能继续正常读取；只有想统一历史数据时才运行。
- 损坏文件和并发版本冲突默认跳过。使用 `--on-error abort` 可在第一个问题处停止。
- 旧块只标记为空闲，不直接删除。验证后再运行 `fsspec_gc` 清理。

JSON 输出形如 `{"ok": true, "dry_run": false, "summary": {...}, "skipped": []}`。
如果存在跳过文件，`ok` 为 `false`，每个跳过文件都会包含原因，命令以退出码 `1` 结束。

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
