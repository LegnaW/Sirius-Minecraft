# Sirius 开发手册（DEVELOPMENT）

> 给开发 agent 的操作手册：环境怎么搭、命令怎么跑、流程怎么走、门禁怎么过。
> 设计原理不在本文（见 [sirius-design.md](./sirius-design.md)），接口规格不在本文（见 [sirius-technical.md](./sirius-technical.md)）。
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

## 5. 开发流程（每轮固定循环）

1. **开工前**：读本文 + [dev-journey.md](./dev-journey.md) + [session/](./session/) 最近日志 + PROGRESS.md，掌握现状。
2. **方案先行**：给出设计思路 + 核心接口/片段，与用户 brainstorm；**禁止未确认直接写码**。
3. **落 spec**：确认后的方案写进 `session/YYYY-MM-DD.md`（任务分解 + 文件清单 + 关键设计 + 核心片段）。
4. **实施**：严格按 spec 执行；未覆盖的情况先暂停问用户；偏离 spec 需先改本文/技术规格对应章节再写码。
5. **门禁**：见下节，全过才算完成。
6. **循环**：门禁过后按用户反馈开下一轮；用户明确说"完成/结束"才收工。

**与主管模式的关系**（技术规格 §10）：大任务仍可派发子代理实现（主会话拆解/验收）；无论谁写码，session 日志与门禁都照走。子代理任务另在 `reports/<里程碑>-<任务>.md` 留交接报告（模板 [reports/template.md](./reports/template.md)）。

## 6. 收尾门禁（6 项硬性检查）

| # | 检查 | 通过标准 |
|---|---|---|
| 1 | 代码与文档一致性 | spec/日志约定的接口、行为与实现逐项比对一致 |
| 2 | 跑通验证 | 从零按 README/本手册命令跑通；`uv run pytest` 全绿；`gradlew build`（含 smokeTest）通过；不破坏既有功能 |
| 3 | tmp 清理 | 验证用的临时环境/文件删除干净 |
| 4 | 当日日志 | session/YYYY-MM-DD.md 补全：每文件→改动→为什么、核心内容、问题与处理、未完成/下一步 |
| 5 | 改动摘要 + 提交 | 给用户摘要；小步提交，提交信息/注释写清"为什么这么改" |
| 6 | overall.md 维护 | 涉及技术细节变动时同步 [../docs_human/overall.md](../docs_human/overall.md)（人读全局文档，代码片段必须与真实文件对应） |

## 7. 文档地图（谁管什么、何时更新）

| 文档 | 受众 | 管什么 | 更新时机 |
|---|---|---|---|
| `docs_human/overall.md` | 人 | 全局技术细节：调用栈→细节设计，贴真实代码 | 每轮涉及技术变动时（门禁 6） |
| 根 `README.md` | 人/访客 | 项目一句话 + 文档入口 | 里程碑变更时 |
| `docs_agent/DEVELOPMENT.md`（本文） | agent | 怎么开发：环境/命令/流程/门禁 | 流程或环境变化时 |
| `docs_agent/dev-journey.md` | agent | 历史决策叙事：为什么走到今天 | 重大决策落地时追加 |
| `docs_agent/PROGRESS.md` | agent | 跨会话状态锚点：做到哪、接下去干什么 | **每轮结束必更** |
| `docs_agent/session/YYYY-MM-DD.md` | agent | 当轮 plan+spec + 收尾日志 | 每轮一份，开工写 plan、收尾补日志 |
| `docs_agent/sirius-design.md` | agent | 设计权威：理念与架构（为什么这样设计） | 设计变更时 |
| `docs_agent/sirius-technical.md` | agent | 技术规格权威：接口/数据结构/参数/路线图 | 接口规格变更时（改码前先改这里） |
| `docs_agent/protocol-neko-mapping.md` | agent | 自研协议 ↔ NEKO 帧映射（M3 兼容层依据） | 协议帧变更时 |
| `docs_agent/reports/<里程碑>-<任务>.md` | agent | 子代理任务交接报告 | 每个派发任务完成时 |
| `sirius-brain/README.md` / `sirius-bridge/README.md` | agent | 各子项目使用细节（mock 用法/帧行为/构建） | 子项目行为变更时 |

原则：设计/规格的权威在 docs_agent/ 既有文档；docs_human/ 是面向人的重述层（可读性优先，允许省略细节但不允许与权威冲突）。

## 8. 常见坑（历史教训汇总）

- **中文编码**：PowerShell 无编码参数读写中文文档 → GBK 乱码（见 §2 纪律）。
- **脚手架元数据**：模板默认值（license/author/url）不能只看构建通过，逐字段核对（M0-T4 的 MIT 教训）。
- **窗口最小化**：MC 渲染循环停止排空任务队列 → `Minecraft.execute` 的任务饿死 → 工具必须带 latch 超时（M1-C，10s）。
- **gradle 代理**：`gradlew` 联网需设 `HTTPS_PROXY=http://localhost:9674`（deploy.cmd 已内置；直接跑 gradlew 需自设）。
- **uv 联网**：与 gradle 相反，需代理置空 + 清华源。
- **本机端口未监听行为**：连未监听端口静默丢包不回 RST → 首连失败要等满 connect_timeout（BridgeClient 默认 10s），不是卡死。
- **客户端侧生物血量**：常未同步，只能 best-effort（world.query 的 health 字段）。
- **1.21.1 API 细节**：GUI 画进主渲染目标（截图天然含 GUI）；`NativeImage.getPixelRGBA` 是小端 ABGR；注册名用 `Holder.getRegisteredName()`。完整清单见 [reports/M1-C.md](./reports/M1-C.md)「1.21.1 API 笔记」。
