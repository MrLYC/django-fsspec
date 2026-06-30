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

## DJANGO_FSSPEC_READ_INTEGRITY

- **默认值**：`"off"`
- **可选值**：`"off"`、`"metadata"`、`"checksum"`
- **说明**：`read_file()` 未显式指定策略时使用的默认读完整性策略
- **影响**：
  - `"off"` 保持兼容性，直接读取当前映射字节
  - `"metadata"` 校验文件/块结构、大小、序号连续性和活动块标记
  - `"checksum"` 在 metadata 校验之外，再校验块和文件的 SHA-256

需要在数据库损坏时快速失败的任务，建议使用 `"metadata"` 或
`"checksum"`。即使全局读策略为 `"off"`，`copy_file()` 默认也会使用
checksum 完整性校验。

## 修改块大小后的处理

修改 `DJANGO_FSSPEC_BLOCK_SIZE` 后：

1. **已有文件不受影响**——每个文件记录了自己的块大小
2. **新文件使用新的块大小**
3. **两种块大小的文件可以共存**，读取时根据文件自身的 `block_size` 定位

如需全局统一块大小，使用 `RechunkOperation`，详见 [块大小迁移](migration-guide.md)。
