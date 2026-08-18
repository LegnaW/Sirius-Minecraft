# 子代理工作报告索引

> 约定：每个子代理完成任务时，必须在 `reports/` 下留一份工作报告（命名 `<里程碑>-<任务>.md`，如 `M1-C.md`）。
> 目的：任何开发者（人类或 AI）不看会话历史即可接手。报告是给人看的，代码注释不能替代它。

## 如何使用本目录

- 接手某模块 → 先读对应报告的「交接须知」，再看代码
- 模板见 [template.md](./template.md)
- 报告由任务执行的子代理在完成时写入；主管验收后随代码一起提交

## 报告索引

| 任务 | 报告 | 一句话 |
|---|---|---|
| M0-T1 | [M0-T1.md](./M0-T1.md) | sirius-brain 骨架 + pydantic 协议模型 |
| M0-T2 | [M0-T2.md](./M0-T2.md) | mock bridge server（假身体） |
| M0-T3 | [M0-T3.md](./M0-T3.md) | 协议 Schema 冻结 + NEKO 映射 |
| M0-T4 | [M0-T4.md](./M0-T4.md) | sirius-bridge NeoForge MDK 骨架 |
| M1-A | [M1-A.md](./M1-A.md) | 版本对齐 21.1.248 + 部署脚本 |
| M1-B | [M1-B.md](./M1-B.md) | Bridge WS 服务端（token/能力协商） |
| M1-C | [M1-C.md](./M1-C.md) | 三感知工具（含 1.21.1 API 坑记录） |
| M1-D | [M1-D.md](./M1-D.md) | Python BridgeClient |
| M1-E | [M1-E.md](./M1-E.md) | M1 真机集成验收 |
