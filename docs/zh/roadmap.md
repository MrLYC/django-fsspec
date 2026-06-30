# 路标

本文档描述 `django-fsspec` 在 0.2.x 之后的演进方向。路标按“目标效果”组织，
而不是只列功能名称，方便判断每一项是否真的改善了使用和运维体验。

迁移历史兼容不作为后续目标。当前方向是保留已重置的迁移历史，移除旧的
`RechunkOperation` API，并把 `fsspec_rechunk` 作为显式的块大小重写工具。

## 审查视角

这份路标基于几个常见角色的对抗性审查整理：

- 维护者：release artifact、tag、文档和 CI 结果不能相互漂移。
- 运维：脏数据库状态不能影响正常流程，事故中使用的修复命令必须可预期。
- DBA：block size、二进制字段落库形态、事务行为和清理成本需要在部署前可见。
- 应用开发者：常见 fsspec 工作流要么稳定可用，要么以清晰的不支持行为失败。
- 安全审查者：namespace 和 auth 边界必须写清楚，不能暗示存储层没有实现的保证。

当多个角色暴露出同一个运维风险时，对应事项会被优先放入路标。

## 目标效果

| 领域 | 目标效果 | 完成证据 |
|------|----------|----------|
| 发布纪律 | 打 tag 时不会出现版本、包内容、文档或 changelog 漂移 | 每次 tag 前 release checklist 和 CI guard 都通过 |
| 运维闭环 | 运维可以用稳定命令检查、修复、重切块和清理数据 | JSON 输出、稳定退出码和事故 runbook 完整 |
| 数据库适配 | 用户能基于真实数据选择 block size 和数据库配置 | 发布 block-size 矩阵和数据库差异建议 |
| 数据完整性 | 脏数据能尽早暴露，不会被静默包装成健康数据 | fsck/repair/rechunk 覆盖常见破坏性场景 |
| 生态集成 | fsspec 和 Django 用户能理解支持行为和边界 | 兼容性测试和集成文档覆盖常见工作流 |

## P0：发布纪律

目标：让发布可复现，并且尽量阻止不一致的版本被发布。

计划工作：

- 增加 release checklist，覆盖版本 tag、changelog 段落、wheel 内容、
  migration 检查、e2e、benchmark smoke 和发布 workflow。
- 增加 CI 检查：tag 版本必须等于 wheel 版本；wheel 不能包含 `demo/`、
  顶层 `tests/` 和本地生成文件。
- 用户可见变更必须同步文档和 changelog 后再打 tag。
- 继续使用 `v*` tag 触发发布，并要求 tag 前 `main` CI 已通过。

目标效果：

- 维护者打 tag 后，可以确认生成包、文档和 CI 状态描述的是同一个版本。

## P1：运维闭环

目标：让 `fsck`、`repair`、`rechunk`、`gc` 和 `stats` 成为可用于生产事故处理
和日常维护的工具链。

计划工作：

- 给 `fsspec_rechunk` 增加 `--json` 输出，并和 `fsspec_fsck` /
  `fsspec_repair` 的 summary 形状对齐。
- 为运维命令定义稳定退出码：
  - `0`：完成且没有 finding 或 skipped file
  - `1`：完成但存在 finding、skipped file 或 unresolved damage
  - `2`：参数错误或命令无法继续执行
- 文档化事故处理顺序：
  `fsspec_fsck` -> `fsspec_repair --dry-run` -> 显式 repair flags ->
  `fsspec_rechunk --dry-run` -> `fsspec_rechunk` -> `fsspec_fsck` ->
  `fsspec_gc --dry-run` -> `fsspec_gc`。
- 保持 repair 和 rechunk 的职责分离：repair 处理损坏元数据；rechunk 只把健康
  文件重写到目标 block size。
- 改进命令输出，让批处理执行结果可以事后审计。

目标效果：

- 运维人员能在常见元数据损坏、块大小统一和空闲块清理场景下按固定流程操作，
  而不需要猜测下一条命令是否安全。

## P2：性能与数据库适配

目标：用真实数据替代泛化假设，形成按数据库和 workload 区分的 block-size 建议。

计划工作：

- 在 SQLite、MySQL、PostgreSQL、Oracle，以及维护者可获得的目标信创数据库环境中
  运行 block-size 矩阵。
- 对比 `32KB`、`64KB`、`128KB`、`256KB` 在小文件、大文件、range read、
  overwrite、铺底目录操作、rechunk 和 gc 上的表现。
- 重点衡量数据库把 Django `BinaryField` 落到 text/CLOB 类字段时的性能退化风险。
- 按 workload 发布建议：
  - 兼容性和小文件优先：保留 `32KB`
  - 常规服务端数据库：重点 benchmark `64KB` 和 `128KB`
  - 大文件吞吐优先：重点 benchmark `128KB` 和 `256KB`
- 扩展 large benchmark 报告，让普通读写场景和铺底元数据场景同时可见。

目标效果：

- 用户在部署前能预估常见操作耗时，并基于数据库和 workload 选择 block size，
  而不是依赖单一默认值。

## P3：生态与产品面

目标：在不提前扩大核心存储契约的前提下，让项目更容易接入现有 Django 和 fsspec
工作流。

计划工作：

- 扩展 fsspec 兼容性测试，覆盖常见文件/目录操作、transaction、append，以及明确
  不支持的 API。
- 持续明确 namespace 边界：namespace 只做路径分区，直接 fsspec API 调用者仍然是
  可信应用代码。
- 完善 WebDAV/Auth 集成文档，但不把 namespace 描述成独立授权边界。
- 在运维能力和 benchmark 数据稳定之后，再评估 Django `Storage` 后端。
- 设计备份/导出校验命令，支持生成 manifest、校验 checksum 和恢复演练。

目标效果：

- 应用开发者能以更少隐藏假设接入 `django-fsspec`；运维也能在 best-effort repair
  之外获得备份验证路径。

## 非目标

- 不恢复已移除的测试用 rechunk migration 兼容历史。
- 不在包自带 migration 中自动重写用户数据。
- 不让 `fsspec_repair` 发明或恢复已经失去可信归属的字节。
- 不把 namespace 当作直接 API 调用者的独立安全边界。
