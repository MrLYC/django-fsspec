# 块大小运维

## 共存机制

修改 `DJANGO_FSSPEC_BLOCK_SIZE` 后，新旧文件可以共存：

- 每个 `FileNode` 记录了写入时的 `block_size`
- 读取时使用文件自身的 `block_size` 定位块
- 不需要迁移也能正常工作

## 何时需要 Rechunk

如果你希望**全局统一**块大小（例如数据库大二进制字段开销较高，或修改配置后希望统一历史文件），可以使用 `fsspec_rechunk`。

默认值从 256KB 改为 32KB 不要求迁移。已有文件继续使用各自记录的 `FileNode.block_size`，新写入才使用当前配置。只有明确想重写旧文件时，才需要执行 rechunk。

## 使用 fsspec_rechunk

先预览：

```bash
python manage.py fsspec_rechunk --block-size 32768 --dry-run
```

数据量较大时分批运行：

```bash
python manage.py fsspec_rechunk --block-size 32768 --namespace 1 --prefix /uploads/ --limit 1000
```

需要备份级重写时启用 checksum 校验：

```bash
python manage.py fsspec_rechunk --block-size 32768 --verify checksum
```

## 执行流程

对每个被选中且 `block_size != --block-size` 的文件：

1. 默认校验元数据；`--verify checksum` 会额外校验 SHA-256
2. 按序读出所有块数据，拼接为完整内容
3. 按新 `block_size` 重新切块
4. 单文件事务内：写入新块和映射、删除旧映射、把无主旧块标记为空闲
5. 更新 `FileNode.block_size`、size、checksum 和 version

如果文件在重写期间被并发修改，或发现损坏元数据，命令默认跳过该文件。希望批次遇到第一个问题就停止时，使用 `--on-error abort`。

执行后建议运行：

```bash
python manage.py fsspec_fsck
python manage.py fsspec_gc --dry-run
```

## 耗时估算

3 万个小文件（< 256KB）也可能需要数分钟，因为每个被选中文件都会被读取并重写。使用 `--limit` 可以把大数据集拆成可重复执行的小批次。执行后建议运行 `fsspec_gc` 清理旧的空闲块。
