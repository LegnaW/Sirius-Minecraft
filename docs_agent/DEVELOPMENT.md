# Sirius 开发手册（DEVELOPMENT）

> 给开发 agent 的操作手册：环境怎么搭、命令怎么跑、流程怎么走、门禁怎么过。
> 设计原理不在本文（见 [docs_human/sirius-design.md](../docs_human/sirius-design.md)，人读纯思路版），接口规格不在本文（见 [sirius-technical.md](./sirius-technical.md)）。
> 最后更新：2026-08-18（文档基建轮）

## 1. 项目 30 秒

Sirius = Minecraft AI 陪玩，双星架构：

```
sirius-brain（Python）      大脑：规划器/执行器/记忆 —— 对 mock 身体开发，M3 切真身体
        ↕ WebSocket（localhost:8765，JSON，MCP 语义 + NEKO 兼容帧，协议 v1.0）
sirius-bridge（Java/NeoForge） 眼与手：跑在真 MC 客户端里的 Mod（截图/状态/世界查询，M2 加输入注入）
```

进度状态永远看 [PROGRESS.md](./PROGRESS.md)（跨会话状态锚点，每轮更新）。当前：**M0+M1 完成（真机验收通过），待启动 M2（手）**。

## 2. 环境要求（Windows）

| 项 | 值 |
|---|---|
| OS | Windows，Git Bash 执行命令 |
| JDK | 21（sirius-bridge 必需） |
| Python | 3.11+（本机 3.13），包管理用 uv（0.12.5，pip --user 安装） |
| 测试客户端 | HMCL 3.16.3 实例 `1.21.1-Sirius`（MC 1.21.1 / NeoForge 21.1.248 / MSA LegnaW9473 / 854x480），实例目录 `.minecraft/versions/1.21.1-Sirius/`（gameDir 在版本目录内，mods/config/logs 都在这里） |
| 网络代理 | `localhost:9674`（HTTP）；gradle 联网需 `HTTPS_PROXY`/`HTTP_PROXY` 指向它；uv 相反——需代理置空 + 清华源 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` |

**编码纪律（硬性）**：修改中文文档严禁用 PowerShell `Get-Content`/`Set-Content` 不带编码参数（曾致 PROGRESS.md GBK 双重编码乱码）。统一用专用文件工具（Read/Write/Edit）。

## 3. 命令速查

### sirius-brain（在 `sirius-brain/` 下）

```sh
uv sync                                    # 安装依赖（创建/更新 .venv）
uv run pytest                              # 全部测试（协议模型/mock/客户端/schema 同步性）
# 或直接：.venv\Scripts\python.exe -m pytest

.venv\Scripts\python.exe -m sirius_brain.mock                       # 起 mock 身体（ws://127.0.0.1:8765）
.venv\Scripts\python.exe -m sirius_brain.bridge --url ws://127.0.0.1:8765 [--token xxx]   # 客户端冒烟
.venv\Scripts\python.exe -m sirius_brain.protocol.export_schema    # 改了 pydantic 模型后必须重跑并提交 schema/
```

### sirius-bridge（在 `sirius-bridge/` 下）

```sh
gradlew build          # 构建（含 smokeTest 45 检查 + syncToolSchemas 从 ../sirius-brain/schema 同步）
gradlew smokeTest      # 仅进程内冒烟（纯逻辑，不起游戏）
gradlew runClient      # 开发用：起带 Mod 的开发客户端
deploy.cmd             # build + 部署 jar 到 HMCL 实例 mods/（幂等；内含代理参数）
```

### 真机验收脚本（仓库根，需先启动 HMCL 实例）

```sh
python m1e_acceptance.py    # M1-E 标题屏验收（token 自动从实例 config 读）
python m1e_ingame.py        # in-game 三连（需已进世界）
```

## 4. Schema 单一事实来源链（改协议必读）

协议的运行时权威是 Python 侧 pydantic 模型，Java 侧只消费导出产物，**永不手写两份**：

```
sirius_brain/protocol/*.py（pydantic 模型，权威）
  → export_schema.py 导出 → sirius-brain/schema/（27 个自包含 draft 2020-12 JSON Schema，需提交）
  → gradle syncToolSchemas 构建期单向同步 → jar 内 schema/ 资源
  → Capabilities.list() 运行时从 jar 资源组装 capabilities/list 响应
```

防漂移双保险：`tests/test_schema_export.py` 对比仓库 schema/ 与代码重导出结果（忘导出→pytest 红）；构建期 syncToolSchemas（忘同步→jar 内容过期）。

**改协议的完整步骤**：改 pydantic 模型 → 跑 export_schema → 提交 schema/ 产物 → Java 侧实现/调整工具 → 双侧测试绿。协议版本号按里程碑递增（当前 1.0）。

## 5. 开发流程与门禁（权威：[RULES.md](./RULES.md)）

每轮循环与 6 项收尾门禁的**唯一权威是 [RULES.md](./RULES.md)**（开工首读；本节只留摘要，避免双权威漂移）：

> 读规则/现状 → 出方案 brainstorm（禁止未确认写码）→ spec 落 `session/YYYY-MM-DD.md` → 按 spec 实施（未覆盖先问；偏离先改文档再写码）→ 门禁 6 项（①一致性 ②跑通 ③tmp 清理 ④当日日志 ⑤摘要+小步提交 ⑥overall.md 维护）→ 等用户反馈开下一轮。

大任务沿用主管模式（技术规格 §10）：主会话拆解/验收，实现派发子代理，任务另在 `reports/<里程碑>-<任务>.md` 留交接报告（模板 [reports/template.md](./reports/template.md)）。

## 6. 常见坑（历史教训汇总）

- **中文编码**：PowerShell 无编码参数读写中文文档 → GBK 乱码（见 §2 纪律）。
- **脚手架元数据**：模板默认值（license/author/url）不能只看构建通过，逐字段核对（M0-T4 的 MIT 教训）。
- **窗口最小化**：MC 渲染循环停止排空任务队列 → `Minecraft.execute` 的任务饿死 → 工具必须带 latch 超时（M1-C，10s）。
- **gradle 代理**：`gradlew` 联网需设 `HTTPS_PROXY=http://localhost:9674`（deploy.cmd 已内置；直接跑 gradlew 需自设）。
- **uv 联网**：与 gradle 相反，需代理置空 + 清华源。
- **本机端口未监听行为**：连未监听端口静默丢包不回 RST → 首连失败要等满 connect_timeout（BridgeClient 默认 10s），不是卡死。
- **客户端侧生物血量**：常未同步，只能 best-effort（world.query 的 health 字段）。
- **1.21.1 API 细节**：GUI 画进主渲染目标（截图天然含 GUI）；`NativeImage.getPixelRGBA` 是小端 ABGR；注册名用 `Holder.getRegisteredName()`。完整清单见 [reports/M1-C.md](./reports/M1-C.md)「1.21.1 API 笔记」。
