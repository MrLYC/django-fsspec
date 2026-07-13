# 运维 Runbook

本文按真实运维场景组织。完整参数列表见
[管理命令](management-commands.md)。

## 第一响应流程

当怀疑数据库行被绕过正常 `django-fsspec` API 修改或删除时，先执行：

```bash
python manage.py fsspec_fsck --json
python manage.py fsspec_repair --dry-run --json
python manage.py fsspec_repair
python manage.py fsspec_fsck
```

如果事故还涉及 block size 策略调整，或某些数据库的大二进制字段退化为 text 后性能
很差，再把健康文件分批重写到当前目标 block size：

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run --json
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /uploads/ --limit 1000
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
python manage.py fsspec_gc
```

安全规则：

- 任何会修改行的命令前，先做数据库备份，或先在恢复副本上演练。
- 自动化里优先使用 `--dry-run --json`。退出码 `1` 表示命令完成但需要运维关注；
  退出码 `2` 表示参数错误或无法继续执行。
- repair、rechunk 或大规模迁移前后都运行 `fsspec_fsck`。
- 用 `--namespace`、`--prefix` 和 `--limit` 控制批次大小，适配数据库锁和维护窗口。

## 命令选择

| 场景 | 命令 |
|------|------|
| 检查数据库行是否内部一致 | `fsspec_fsck` |
| 修复派生元数据、空闲/使用标记、序号缺口和安全的目录元数据问题 | `fsspec_repair` |
| 明确批准后，把文件路径冲突下的子孙路径移到恢复前缀 | `fsspec_repair --recover-path-conflicts` |
| 把已有文件重写到当前 block size | `fsspec_rechunk --block-size 32768` |
| 验证和保留窗口结束后删除空闲块 | `fsspec_gc` |
| 在本地、Django 和其他 fsspec 文件系统之间复制数据 | `fsspec_migrate` |
| 查看整体存储计数 | `fsspec_stats` |

## 跨文件系统迁移

导入本地目录、导出到本地备份路径，或在 namespace 之间复制时使用
`fsspec_migrate`：

```bash
python manage.py fsspec_migrate file:///mnt/import/ django://1/imports/ --dry-run --json
python manage.py fsspec_migrate file:///mnt/import/ django://1/imports/ --manifest /var/log/django-fsspec/import.jsonl
python manage.py fsspec_migrate django://1/uploads/ file:///mnt/export/uploads/ --manifest /var/log/django-fsspec/export.jsonl
python manage.py fsspec_migrate django://1/uploads/ django://2/uploads-copy/ --limit 1000
```

限制和注意点：

- 命令只复制，不删除源文件。
- 默认冲突策略是 `skip`；目标已存在时命令以退出码 `1` 结束，方便脚本提示人工复核。
- `django://<namespace_id>/<path>` 只选择 namespace，不代表认证身份；调用方必须已经
  有权限执行该命令。
- 凭据通过环境变量或 `--source-options` / `--target-options` JSON 传入。这些 options
  不会写入 manifest 或 JSON summary。
- `django-fsspec` 不内置 S3 等可选 fsspec backend，需要在应用环境里自行安装。

## 修复损坏数据

当 `fsspec_fsck` 报告 recoverable finding 时，使用：

```bash
python manage.py fsspec_repair --dry-run --json
python manage.py fsspec_repair
python manage.py fsspec_fsck --json
```

该命令能修复可由当前行重新计算的元数据：`StorageBlock.size`、
`StorageBlock.checksum`、`FileNode.size`、`FileNode.checksum`、被误标记为空闲的
活动块、sequence 缺口、不可能存在的目录块映射，以及没有引用但仍标记为已用的块。

边界：

- 不能恢复已经从 `StorageBlock.data` 删除或覆盖的字节。
- 如果 `FileBlock` 行被删除，孤立块字节已经没有可信路径归属。repair 会按剩余映射
  让数据库回到一致状态，并把孤立块标记为空闲。
- 共享块、非法持久化路径和路径冲突需要显式运维处理或从备份恢复。
- 只有在查看 dry-run 并接受恢复前缀移动后，才使用 `--recover-path-conflicts`。

## Rechunk 和 GC

修改目标 block size 后，或数据库对大二进制/text 字段性能不好时，使用
`fsspec_rechunk`：

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run --json
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /media/ --limit 1000
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
python manage.py fsspec_gc
```

限制：

- Rechunk 不是 Django migration，可以重复执行；已是目标 block size 的文件会跳过。
- 每个文件独立事务处理。损坏文件默认跳过，不阻塞其他健康文件继续推进。
- 旧块只标记为空闲，不直接删除。`fsspec_gc` 只永久删除空闲块，因此应在验证和保留
  窗口结束后执行。

## 已覆盖的 E2E 场景

`benchmarks/e2e_test.py` 包含以下 runbook 级覆盖：

- `ops_runbook_migrate_roundtrip`：本地目录导入 `django://`，再导出回本地文件，
  最后执行 namespace 到 namespace 复制。
- `ops_runbook_repair_flow`：`fsck` 发现可修复元数据损坏，`repair --dry-run` 不改行，
  `repair` 修复后 `fsck` 变干净。
- `ops_runbook_rechunk_gc_flow`：旧 block size 文件经过 dry-run 检查、重写到 32KB、
  验证读取，再用 `gc` 删除空闲块。
- `ops_runbook_json_exit_codes`：JSON 输出可解析，`fsck` finding 和 migrate 冲突返回
  关注退出码 `1`，dry-run repair 不修改数据。
