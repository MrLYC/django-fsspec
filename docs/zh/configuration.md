# 配置说明

所有配置通过 Django `settings.py` 设置，使用 `getattr` 读取，有默认值。

## DJANGO_FSSPEC_BLOCK_SIZE

- **默认值**：`262144`（256KB）
- **说明**：文件分块存储的块大小
- **影响**：只影响新写入的文件。每个文件的 `FileNode.block_size` 记录了写入时的块大小，因此不同块大小的文件可以共存
- **建议**：小文件为主的场景可设为 `64 * 1024`（64KB）

## DJANGO_FSSPEC_MAX_FILE_SIZE

- **默认值**：`2097152`（2MB）
- **说明**：单个文件的大小上限
- **影响**：超过上限的写入会抛出 `FileTooLargeError`
- **与块大小的关系**：独立配置，互不影响

## 修改块大小后的处理

修改 `DJANGO_FSSPEC_BLOCK_SIZE` 后：

1. **已有文件不受影响**——每个文件记录了自己的块大小
2. **新文件使用新的块大小**
3. **两种块大小的文件可以共存**，读取时根据文件自身的 `block_size` 定位

如需全局统一块大小，使用 `RechunkOperation`，详见 [块大小迁移](migration-guide.md)。
