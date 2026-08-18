# Sirius 工作进度

> 本文档是**跨会话的状态锚点**：每轮工作结束时更新，新会话从这里恢复上下文。
> 设计内容不写这里（在 [sirius-design.md](../docs_human/sirius-design.md) / [sirius-technical.md](./sirius-technical.md)），这里只记"做到哪了、接下来干什么"。
> 最后更新：2026-08-19

## 当前阶段：M2（手）**全部完成**——含里程碑收官验收（纯脚本合成工作台）与权限分档实测；待启动 M3（会师）

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
- [x] **M2-A**：input.* 四原语完成并验收（事件层注入：KeyboardHandler.keyPress/MouseHandler.onMove/onPress/charTyped 反射直调；令牌桶限频 20/s；GUI 点击留证 logs/sirius_evidence/；键名→GLFW 映射表；smoke 111→119）
- [x] **M2-A2**：失焦不暂停补丁（keep_running_unfocused 默认 true，运行时直写 Options.pauseOnLostFocus=false——1.21.1 是 plain boolean 字段非 OptionInstance；绝不写 options.txt；失焦时 key/text/click 仍有效，视角转动失焦失效记为 M4 已知限制）
- [x] **M2-A 真机验证 PASS（2026-08-19）**：按 E 开背包→截图→mouseMove→按 E 关背包，VLM 双图确认开/关正确；screen_open 状态与 gui_scaled 坐标返回精确。**事件层注入保真度验证通过，项目最高风险点解除**
- [x] **文档基建轮（2026-08-18）**：双层文档体系落位——`docs_agent/`（原 docs_for_agents 改名归位 + 新增 DEVELOPMENT.md / dev-journey.md / session/）与 `docs_human/`（overall.md 全局技术文档，人读）；根 README 建立；工作方式固化为 `RULES.md`（开工必读唯一权威）+ 根 `AGENTS.md` 自动加载入口；同日双门禁全过（pytest 191 + gradlew smoke 45）
- [x] **design 文档归类调整（2026-08-19）**：sirius-design.md 移入 docs_human/（用户裁决：纯思路文档给人读，agent 读 sirius-technical.md 带技术路线版）；交叉引用与 RULES 文档地图同步
- [x] **PR #1 合并（2026-08-19）**：另一会话的双层文档重构经本地审查（191 测试/23 文档编码/路径残留零）后 merge 入 main（cf15a80）
- [x] **local.md 机制（2026-08-19）**：开发者本地备忘（环境路径 + 本机坑 + 个人特殊要求，结构不限自由编辑）gitignored 各开发者独立；模板 docs_agent/local.template.md 入库；RULES §2 第 0 步强制『拿到项目先建/核对』——开工环境自检制度化（当日由 ENV.local.md 更名放宽）

## 进行中

（无——M2 收口；下一步 M3：大脑最简版（单模型 截图→VLM→工具）+ NEKO 协议兼容层，见技术规格 §10.1）

## M2 完成记录（2026-08-19，D 盘机收口）

- [x] **M2-B 事件订阅推送**：EventPusher 单一事件入口（chat/gui_open/gui_close + 危险态 death/fire/health_low/drown 状态沿+5s 冷却）；events.subscribe per-connection 订阅（原子 seq）；截图推流（~1Hz 采样/6s 节流最新帧待发+边界补发/质量×边长双阶梯 100KB 硬预算/环形 3）；诚实丢弃计数。冒烟 119→175；真机 PASS（10 帧 seq 单调/预算内）。借鉴 N.E.K.O 生产管线（service.py:1037-1307）
- [x] **M2-C getGuiState**：widgets 递归树（512 截断/匿名类走具名超类/children+renderables 双注册表）+ 容器 slots（guiLeft+slot.x 公式 + Numen 式角色分类）；真机首验 2 缺陷（addSlot 覆写 Slot.index→改用 getContainerSlot；盔甲槽归 player 角色致收官首跑木板入头盔槽→armor/offhand 独立角色）回修后 PASS。冒烟 200
- [x] **M2-D look/lookAt+权限+command()**：vanilla Entity.lookAt 原式（setYRot/setXRot+yRotO/xRotO 同步，服务器自动跟随）；权限四级 observe/input_world/input_gui/full（默认 full 向后兼容，-32012+审计）；BridgeClient.command()（T→text→ENTER+500ms 沉降）。冒烟 241/pytest 193
- [x] **M2 里程碑收官验收 PASS**：`m2_final.py` 纯脚本零 LLM——/give→E→getGuiState 定位→坐标换算→拖拽合成→工作台入包；终态双通道确认（结构化断言 + qwen3.7-plus 高清识图独立定位）；证据 m2_final_1~4.jpg
- [x] **权限分档实测 PASS**（observe：感知照常/行为 -32012/审计留痕；验后已回 full）
- [x] **参考项目精读落地**（RULES §3 先找参考）：本机克隆 D:\AI\Project\references\（numen/N.E.K.O/mindcraft-ce）；技术规格 §8.3 已按 Numen 现行代码修正（竞价→序数分层）

## 接下来：M0 剩余任务

| # | 任务 | 依赖 | 验收标准 | 状态 |
|---|---|---|---|---|
| T1 | sirius-brain Python 仓库骨架（uv 工程、pydantic 协议模型、pytest） | 无 | pytest 绿；模型与技术规格 §8.2 一致 | 已完成 |
| T2 | mock bridge server（帧回放 + 可脚本化响应） | T1 | 与 T1 协议模型跑通 task→task_finished 往返 | 已完成 |
| T3 | 协议 JSON Schema 导出 + NEKO 兼容帧映射说明 | T1 | schema 可被 Java 侧直接消费 | 已完成 |
| T4 | sirius-bridge NeoForge MDK 骨架（仅工程搭建） | 无 | `gradlew build` 通过 | 已完成 |

## 工程约定

- **双层文档分工**：`docs_agent/` 给 agent 读（准确完备，本目录）；`docs_human/` 给人读（突出重点、可读性优先，内容不得与 docs_agent 权威冲突）。**开工先读 `docs_agent/RULES.md`**（工作方式唯一权威：流程/门禁/文档地图；仓库根 `AGENTS.md` 是 agent 自动加载的入口指针）。每轮方案确认后落 `session/YYYY-MM-DD.md`，收尾过 6 项门禁（全流程见 RULES.md）
- **子代理工作报告**：每个任务完成时在 `docs_agent/reports/<里程碑>-<任务>.md` 留报告（模板 template.md，索引 README.md）；主管验收后随代码提交。目的：任何开发者不看会话历史即可接手
- **环境自检**：开工第一步核对根 `local.md`（gitignored，本地备忘：路径/本机坑/个人要求；无则从 `docs_agent/local.template.md` 复制创建）——见 RULES §2 第 0 步
- **脚手架元数据逐字段核对**：模板默认值（license/author/url 等）不能只看构建通过（M0-T4 的 MIT 教训）

## 环境备忘（Windows）

- **修改中文文档严禁用 PowerShell Get-Content/Set-Content 不带编码参数**（曾致 PROGRESS.md GBK 双重编码乱码，2026-08-18 从会话上下文恢复）；统一用专用文件工具
- 网络代理：`localhost:9674`（HTTP）；gradle/联网下载需设置 `HTTPS_PROXY`/`HTTP_PROXY` 指向它（gradlew 直跑用 deploy.cmd 同款 `-Dhttps.proxyHost=localhost -Dhttps.proxyPort=9674 -Dhttp.proxyHost=localhost -Dhttp.proxyPort=9674`）
- **pip 大坑（2026-08-18 实录）**：Windows 注册表系统代理（127.0.0.1:9674）被 pip/urllib 自动读取，`env -u` 清环境变量没用、`--proxy ""` 也没用，而该代理 **403 清华源** → pip 一律报 "versions: none"。解法：命令前加 `NO_PROXY='*' no_proxy='*'`。curl/gradle 不走注册表代理不受影响
- uv 0.12.5（pip --user 安装在 Store Python 用户 Scripts：`C:\Users\Administrator\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.10_qbz5n2kfra8p0\LocalCache\local-packages\Python310\Scripts`，**不在 PATH**，用全路径或先 export PATH）。uv 联网需代理绕行 + 清华源：`env NO_PROXY='*' UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync`（2026-08-18 曾丢失重装，从零 sync + 191 测试全绿实证此流程）
- 系统 java 22 可直接跑 gradlew（toolchain 21 由 Gradle 自行解析，2026-08-18 实证 build + smokeTest 45 通过）

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
