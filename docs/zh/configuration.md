# 配置说明

所有配置通过 Django `settings.py` 设置，使用 `getattr` 读取，有默认值。

## DJANGO_FSSPEC_BLOCK_SIZE

- **默认值**：`32768`（32KB）
- **说明**：文件分块存储的块大小
- **影响**：只影响新写入的文件。每个文件的 `FileNode.block_size` 记录了写入时的块大小，因此不同块大小的文件可以共存
- **建议**：小文件占多数，或数据库的大二进制字段可能退化为 text/CLOB 时，保留 32KB 默认值。大文件吞吐优先且数据库二进制字段表现稳定时，再通过 benchmark 对比 `64 * 1024`、`128 * 1024` 和 `256 * 1024` 后覆盖。

## DJANGO_FSSPEC_MAX_FILE_SIZE

- **默认值**：`2097152`（2MB）
- **说明**：单个文件的大小上限
- **影响**：超过上限的写入会抛出 `FileTooLargeError`
- **与块大小的关系**：独立配置，互不影响

## DJANGO_FSSPEC_READ_INTEGRITY

- **默认值**：`"off"`
- **可选值**：`"off"`、`"metadata"`、`"checksum"`
- **说明**：`read_file()` 未显式指定策略时使用的默认读完整性策略
- **影响**：
  - `"off"` 保持兼容性，直接读取当前映射字节
  - `"metadata"` 校验文件/块结构、大小、序号连续性和活动块标记
  - `"checksum"` 在 metadata 校验之外，再校验块和文件的 SHA-256

需要在数据库损坏时快速失败的任务，建议使用 `"metadata"` 或
`"checksum"`。`copy_file()` 默认保持低开销复制路径；需要备份级复制时请传入
`integrity="checksum"`。

## DJANGO_FSSPEC_MIGRATE_MANIFEST_DIR

- **默认值**：`<BASE_DIR>/.django-fsspec-migrate`
- **说明**：自动生成的 `fsspec_migrate` JSONL manifest 存放目录
- **影响**：只影响真实执行的 `fsspec_migrate`。dry-run 不会创建默认 manifest。

单次执行可以用 `fsspec_migrate --manifest` 覆盖输出位置。

## 修改块大小后的处理

修改 `DJANGO_FSSPEC_BLOCK_SIZE` 后：

1. **已有文件不受影响**——每个文件记录了自己的块大小
2. **新文件使用新的块大小**
3. **两种块大小的文件可以共存**，读取时根据文件自身的 `block_size` 定位

正确读取不要求迁移。如需把已有文件全局统一到某个块大小，使用 `fsspec_rechunk`，详见 [块大小运维](block-size.md)。
