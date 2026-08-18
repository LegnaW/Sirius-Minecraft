# Sirius 工作进度

> 本文档是**跨会话的状态锚点**：每轮工作结束时更新，新会话从这里恢复上下文。
> 设计内容不写这里（在 [sirius-design.md](./sirius-design.md) / [sirius-technical.md](./sirius-technical.md)），这里只记"做到哪了、接下来干什么"。
> 最后更新：2026-08-18

## 当前阶段：M1（眼睛）已完成——全部验收通过，含真机集成验收；待启动 M2（手）

## 已完成

- [x] **设计定稿**：双星架构（规划器/执行器/反射层）、中断语义（CANCEL/DEFLECT，取消 PAUSE）、任务卡/报告协议、工具暴露（快/慢车道）
- [x] **记忆系统设计**：五类型（含玩家记忆）× 四层 × 双源（导入/习得）× 双模态（文本/图像）；证据数学/反思层/检索重排/说话者信任（吸收自 N.E.K.O）
- [x] **人设系统设计**：三层人设（底座/用户卡 protected/习得人格）、卡格式（兼容 Numen persona + SillyTavern 导入）、注入防护（技术规格 §6.5）
- [x] **身体选型裁决**：真客户端 + sirius-bridge（NeoForge，Java）；评估弯路记录（mineflayer → Numen 附属 → 真客户端）
- [x] **技术栈裁决**：sirius-brain = Python（MCP SDK/LanceDB/pydantic；代价：Mindcraft CE 只做逻辑移植、mineflayer 并行轨改 mock 优先）
- [x] **协议经验吸收**：NEKO game_agent 帧协议/task_id 回传/五态状态表/截图预算管线（技术规格 §8.2）
- [x] **里程碑计划**：M0-M9 双轨并行、验收标准、三个关键决策点（技术规格 §10.1）
- [x] **协作模式**：主管模式（主会话派发+验收，代码全走子代理，技术规格 §10）
- [x] **项目迁移**：根目录 `E:\minecraft-projects\`（原记录为 D 盘，已修正）；资源地图（技术规格 §8.6）；旧副本已删除
- [x] **命名**：项目 Sirius（双星隐喻）；GitHub 仓库 Sirius-Minecraft
- [x] **M0-T1**：sirius-brain 仓库骨架完成并验收（uv 工程 + pydantic 协议模型全套 + pytest 38 项全绿；模型对齐 §8.2/§5，interrupt_policy 已按 §8.4 去除 pause）
- [x] **M0-T4**：sirius-bridge NeoForge MDK 骨架完成并验收（MC 1.21.1 / NeoForge 21.1.233 / ModDevGradle 2.0.141 / Gradle 9.2.0 / JDK 21；`gradlew build` BUILD SUCCESSFUL，产物 `build/libs/sirius_bridge-0.1.0.jar`；mods.toml 用 templates+generateModMetadata 方式）
- [x] **M0-T2**：mock bridge server 完成并验收（`sirius_brain/mock/`：WebSocket 假身体，pydantic 帧校验、能力协商、task→task_finished 五态剧本、事件推送 seq 递增、JSONL 帧回放；pytest 60 项全绿 + 主管独立冒烟三连通过；T1 模型零改动）
- [x] **仓库**：GitHub `LegnaW/Sirius-Minecraft` 单仓建立（Apache-2.0；设计文档移入 agent 文档目录，2026-08-18 更名 `docs_agent/`）
- [x] **M0-T3**：协议 Schema 导出 + NEKO 映射完成并验收（`sirius_brain/protocol/export_schema.py` CLI；`schema/` 27 个 draft 2020-12 自包含产物 + index.json v1.0；Java 侧可单文件消费；`docs_agent/protocol-neko-mapping.md` 帧级双向映射 + 五态转换 + M3 翻译要点 10 条；pytest 162 项全绿，含 schema 与模型同步性防漂移测试；新增 dev 依赖 jsonschema）
- [x] **M1-A**：NeoForge 对齐 21.1.248 + deploy.cmd 幂等部署（mods 目录唯一 jar）
- [x] **M1-B**：Bridge WS 服务端（Java-WebSocket jarJar 内嵌；选型依据：生产客户端无 netty-codec-http）；token 握手（常数时间比较/10s 看门狗/首帧强制 hello）、loopback 绑定、能力协商从冻结 schema 单向同步（syncToolSchemas）、ToolRegistry 注册表、审计日志；进程内冒烟 19/19 + Python 客户端互通 9/9
- [x] **M1-C**：三感知工具（screenshot：渲染线程 framebuffer 抓取含 GUI/JPEG/2MB 预算降级阶梯；getStats：主线程玩家快照；world.query：blocks 立方扫描 512 截断/entities 128 上限）；冒烟 45/45 挂入 build
- [x] **M1-D**：Python BridgeClient（重连监督、hello 首帧保证、RPC uuid 配对、NEKO 帧回调、事件分发 seq 校验）；29 测试，累计 191 绿；CLI 对 mock 实测
- [x] **M1-E**：真机验收 PASS（HMCL 1.21.1-Sirius + sirius_bridge jar）：token 握手、12 能力协商、getStats/world.query 未进世界优雅降级 in_game:false、854x480 截图存盘（72KB JPEG，VLM 确认为完整标题画面）；证据 docs_agent/m1-evidence/m1e_screenshot.jpg
- [x] **1.21.1 API 坑记录**（M1-C 报告，M2 必读）：GUI 画进主渲染目标→Screenshot.takeScreenshot 即含 GUI；Minecraft.execute 任务帧首执行但最小化时饿死→latch 超时；NativeImage 小端 ABGR 转 ARGB；Holder.getRegisteredName() 拿注册名

## 进行中

（无——M1 收口，等待 M2 启动确认；可选：进世界补一轮 in-game 感知验证）

## 接下来：M0 剩余任务

| # | 任务 | 依赖 | 验收标准 | 状态 |
|---|---|---|---|---|
| T1 | sirius-brain Python 仓库骨架（uv 工程、pydantic 协议模型、pytest） | 无 | pytest 绿；模型与技术规格 §8.2 一致 | 已完成 |
| T2 | mock bridge server（帧回放 + 可脚本化响应） | T1 | 与 T1 协议模型跑通 task→task_finished 往返 | 已完成 |
| T3 | 协议 JSON Schema 导出 + NEKO 兼容帧映射说明 | T1 | schema 可被 Java 侧直接消费 | 已完成 |
| T4 | sirius-bridge NeoForge MDK 骨架（仅工程搭建） | 无 | `gradlew build` 通过 | 已完成 |

## 工程约定

- **子代理工作报告**：每个任务完成时在 `docs_agent/reports/<里程碑>-<任务>.md` 留报告（模板 template.md，索引 README.md）；主管验收后随代码提交。目的：任何开发者不看会话历史即可接手
- **脚手架元数据逐字段核对**：模板默认值（license/author/url 等）不能只看构建通过（M0-T4 的 MIT 教训）

## 环境备忘（Windows）

- **修改中文文档严禁用 PowerShell Get-Content/Set-Content 不带编码参数**（曾致 PROGRESS.md GBK 双重编码乱码，2026-08-18 从会话上下文恢复）；统一用专用文件工具
- 网络代理：`localhost:9674`（HTTP）；gradle/联网下载需设置 `HTTPS_PROXY`/`HTTP_PROXY` 指向它
- uv 已装（0.12.5，pip --user）；uv 联网需代理置空 + 清华源（`UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`）

## 决策记录（只记结论，论证在设计文档）

| 日期 | 决策 |
|---|---|
| 2026-08-17 | 身体 = 真客户端（非 mineflayer/Numen 服务端）；Bridge Mod 复活为主方案 |
| 2026-08-17 | 中断取消 PAUSE；恢复 = 重派重算（Numen 裁决） |
| 2026-08-18 | 项目命名 Sirius；GitHub 仓库 Sirius-Minecraft |
| 2026-08-18 | sirius-brain 用 Python；大脑轨 mock 优先（弃 mineflayer 并行） |
| 2026-08-18 | 主管模式：主会话不写代码，派发子代理 + 验收 |
| 2026-08-18 | 项目根确认 `E:\minecraft-projects\`（文档盘符已修正） |
| 2026-08-18 | sirius-bridge 目标版本：MC 1.21.1 / NeoForge 21.1.x（与本地 Numen 源码对齐） |
| 2026-08-18 | 仓库协议 Apache-2.0；协议冻结为 schema/ v1.0（draft 2020-12） |
| 2026-08-18 | M1 WS 依赖选型 Java-WebSocket（生产客户端无 netty-codec-http，手写 RFC6455 过重） |

## 遗留问题 / 待用户输入

- 旧 `mindcraft-ce-develop\` 本体（E 盘已有副本的原始位置）是否删除待定
- M4 前决策：Baritone 依赖 vs 自研寻路
- M5 前决策：执行器①是否引入 Numen 式确定性任务
- 模型选型（规划器/执行器具体型号）未定
