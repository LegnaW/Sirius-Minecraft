# 自研协议 ↔ NEKO game_agent 帧映射说明

> 面向 **M3「NEKO 协议兼容层」实现者**（sirius-bridge Java 侧）。
> 权威来源：[sirius-technical.md](./sirius-technical.md) §8.2（协议规格与 NEKO 帧定义）、`sirius-brain/sirius_brain/protocol/`（pydantic 模型）。
> 协议冻结产物：`sirius-brain/schema/`（每个文件自包含的 draft 2020-12 JSON Schema，协议版本 1.0）。
> NEKO 侧参考实现（本地副本）：`E:\minecraft-projects\N.E.K.O-main\plugin\plugins\game_agent_minecraft\`（`client.py` 帧定义 / `service.py` 状态消费与超时 / `__init__.py` 结果分类）。

---

## 0. 为什么要有这份映射

§8.2「协议兼容层（战略）」：sirius-bridge 实现 NEKO game_agent 协议作为**第二前端**，让 N.E.K.O（语音陪伴人格大脑）与 Sirius 大脑（分层任务大脑）驱动同一身体——"身体不绑死大脑"与"大脑不绑死身体"互为镜像。M3 验收标准之一就是"NEKO 也能驱动同一身体"。

两套协议的角色：

| | 自研协议（Sirius） | NEKO game_agent 协议 |
|---|---|---|
| 语义模型 | MCP 语义：`request`/`response`（id 配对）+ `notification`（事件一等公民）+ `task`/`task_finished`（NEKO 兼容帧） | 消息驱动：`task` 下发（fire-and-forget）+ 终态 `task_finished`；过程帧 `log`/`screenshot`/`alert` 等 |
| 能力发现 | `capabilities/list` 发现 + `protocol_version` 协商 | 无协商，能力硬编码在两侧 |
| 帧校验 | pydantic 模型 + JSON Schema（`schema/` 目录） | 隐式约定（源码即规格） |
| 安全模型 | localhost 默认 + token 握手 + 权限分级 | 无 |

**关键设计事实**：自研协议已把 `task`/`task_finished` 作为一等帧类型纳入（`TaskFrame`/`TaskFinishedFrame`），字段与 NEKO 完全同构——兼容层的核心工作不是"翻译"这两个帧，而是把 NEKO 的**其余帧**（log/screenshot/alert/…）翻译到自研协议的 notification/工具语义，并把自研协议的扩展能力（能力协商、事件订阅、权限）对 NEKO 前端做**降级或合成**。

---

## 1. NEKO game_agent 帧全清单

来自 `client.py` 模块 docstring 与 `_listen()` 分发逻辑（NEKO 大脑侧视角）：

| 帧 | 方向 | 字段 | 说明 |
|---|---|---|---|
| `task` | NEKO → 身体 | `{type, task, task_id?}` | 下发任务；`task_id` **可选**，缺省时插件按 FIFO 顺序兜底匹配 |
| `query_inventory` | NEKO → 身体 | `{type}` | 唯一的"类请求"帧：请求立即推一次 `inventory` |
| `log` | 身体 → NEKO | `{type, text}`（兼容读 `data`/`message` 键） | 过程叙述 |
| `screenshot` | 身体 → NEKO | `{type, image, encoding: "png"\|"jpeg"}` | 截图流（可 >1Hz，NEKO 侧 `max_size=None`） |
| `task_finished` | 身体 → NEKO | `{type, status, text, task_id?}` | 任务终态，五态 |
| `alert` | 身体 → NEKO | `{type, ...}` | 异步高危事件（受伤/死亡等） |
| `inventory` | 身体 → NEKO | `{type, ...}` | 背包快照（按需或周期推） |
| `bot_status_nl` | 身体 → NEKO | `{type, ...}` | 自然语言活动状态（🎯按指令/🤖自主 + skill/kind），nudge 循环据此停发 |
| `ingame_chat` | 身体 → NEKO | `{type, messages: [{player, text}]}` | 玩家聊天**批量**帧；结构化 `messages` 是权威，聚合 `text` 不消费 |
| `agent_status` | 身体 → NEKO | `{type, ...}` | 信息性状态，插件忽略 |

---

## 2. 逐条映射表

### 2.1 自研帧 → NEKO 帧（Bridge Mod 出方向）

| 自研帧/能力 | schema 产物 | → NEKO 翻译 | 规则 |
|---|---|---|---|
| `TaskFrame` `{type:"task", task, task_id}` | `schema/frames/TaskFrame.json` | `task` 帧 | **零翻译直通**（字段同名同义）。差异：自研 `task_id` 必填，NEKO 可选 |
| `TaskFinishedFrame` `{type:"task_finished", status, task_id, text}` | `schema/frames/TaskFinishedFrame.json` | `task_finished` 帧 | 零翻译直通；`status` 五态枚举两侧语义一致（见 §3）；`task_id` 原样回传 |
| `NotificationFrame`（`event="log"`，INFO） | `schema/frames/NotificationFrame.json` | `log` 帧 | `text` 取 `data.text`；NEKO 兼容读 `data`/`message` 键，建议 `text` |
| `NotificationFrame`（`event="screenshot"`） | 同上 | `screenshot` 帧 | `data.image_b64` → `image`，`data.encoding`（"jpeg"）→ `encoding`；按 §5.4 预算管线推流 |
| `NotificationFrame`（CRITICAL 级事件：溺水/着火/被攻击/死亡/断线） | 同上（`data.level="CRITICAL"`） | `alert` 帧 | 事件分级 CRITICAL → `alert`；`data` 原样透传 |
| `NotificationFrame`（`event="chat"`，INFO） | 同上 | `ingame_chat` 帧 | **逐条 → 批量聚合**：`data.player`/`data.text` 收进 `messages` 数组；NEKO 只认结构化 `messages` |
| `getStats` 工具响应 | `schema/tools/getStats.json`（请求参数） | （配 `query_inventory`）`inventory` 帧 | 自研按需拉（request/response），NEKO 期待推送；兼容层用 `getStats` 结果合成 `inventory` 帧 |
| `ToolCallResponse.error`（-32700/-32600/-32601/-32602） | `schema/frames/ToolCallResponse.json` | `log` 帧 | NEKO 无错误通道；错误码转人类可读文本走 `log`（如 `[error] 参数校验失败 -32602: ...`） |

### 2.2 NEKO 帧 → 自研帧（Bridge Mod 入方向）

| NEKO 帧 | → 自研翻译 | 规则 |
|---|---|---|
| `task` | `TaskFrame`（直通） | `task_id` 缺失时兼容层**生成并登记**（如 `neko-<uuid4>`）；见 §3.2 |
| `task_finished` | —（Mod 是发送方，不接收） | — |
| `query_inventory` | `ToolCallRequest(method="getStats")`（内部合成） | 兼容层收到后内部调 getStats，把结果包装成 `inventory` 帧回推；`id` 用内部生成的关联串 |
| `log` / `screenshot` / `alert` / `inventory` / `bot_status_nl` / `agent_status` / `ingame_chat` | —（NEKO 不发送，只接收） | — |

### 2.3 帧语义对应小结（任务要求的三组核心对应）

- **task → 自研任务**：`TaskFrame` 与 NEKO `task` 同构，且在自研协议里继续扮演"身体侧任务受理"入口（替换式受理见 §5.2）
- **log → INFO 事件**：NEKO 的过程叙述对应自研 `NotificationFrame` 的 INFO 级（缓冲）；WARNING（排队）/CRITICAL（反射层）是自研扩展
- **screenshot → screenshot 工具 + 事件**：自研既有拉模式（`screenshot` request/response，`schema/tools/screenshot.json`，参数 `tier:"full"|"crop"`、`bbox`、`quality`）也有推模式（screenshot 事件流）；NEKO 只消费推模式，兼容层负责把能力包装成流（§5.4）

---

## 3. 五态状态表

### 3.1 语义与转换规则

自研 `TaskFinishedStatus`（冻结于 `schema/frames/TaskFinishedFrame.json` 的 `$defs.TaskFinishedStatus`）与 NEKO 消费侧实际用法对照：

| status | 自研语义（spec §8.2） | NEKO 消费侧行为（源码证据） | 兼容层规则 |
|---|---|---|---|
| `ok` | 任务成功 | 正常路径；`smoke_local.py` 归入 rc=0 集合 | **失败绝不能报成 ok**（否则 NEKO 数字人格会把翻车当成功叙述） |
| `failed` | 执行失败 | 唯一"真失败"；rc=1；LLM 叙述走失败话术 | 工具链报错/任务前提不成立时用；`text` 带可读原因 |
| `interrupted` | 被中断：前端断连、关停、前端主动覆盖旧任务 | 与 `superseded` 同为中性非完成（`__init__.py` 归类 `is_interrupted`）；nudge 不追问 | 兼容层在前端断连/会话关闭时给 pending 任务回 `interrupted` |
| `superseded` | 被新任务顶替（中性，**不算失败**） | 同上中性；NEKO 源码注释明确这是身体侧"新任务顶替旧任务"的裁决 | 同前端新 `task` 到达且替换式受理顶掉旧任务时，旧任务回 `superseded` |
| `timeout` | 超时 | 干净的结构化结局（LLM 可见），非错误；rc=0 集合 | 超时裁决在**身体侧**计时（见 §3.3），别依赖 NEKO 计时 |

转换规则总结：

1. **自研 → NEKO：五态直通**。枚举值在两侧同义冻结，不映射、不改写。
2. **NEKO → 自研：同上**。NEKO 不发送 `task_finished`（它是接收方），无需反向转换。
3. **中性类合并语义**：NEKO 把 `interrupted`/`superseded` 当同一中性类处理（"任务没跑完但没出错，新任务已在途，不要再追问/不要切话锋"）。兼容层只要按上表触发条件选对状态即可，即使语义偶有交叉，NEKO 侧分类结果一致。
4. **禁止状态捏造**：不允许把未知内部结局四舍五入成 `ok`；宁可 `failed`（真坏）或 `timeout`（未证明完成）。

### 3.2 task_id 原样回传约束

- **自研侧**：`task_id` 必填（schema `required`），**必须原样回传**——不改写、不规范化、不 trim、疑似 uuid 变体字符串也不动（`tests/test_enums.py::TestNekoTaskId` 有专项用例）。原因：out-of-order 完成时按完成序匹配会错误归属（N.E.K.O 已知限制，spec §8.2 明确吸收此教训）。
- **NEKO 侧**：`task_id` 可选；缺省时插件退化为 FIFO 顺序匹配——仅在前端串行发任务时侥幸正确。
- **兼容层规则**：
  1. 收到带 `task_id` 的 `task`：登记 `{task_id → 内部任务}` 映射表，`task_finished` 原样带回。
  2. 收到**不带** `task_id` 的 `task`：兼容层生成一个（推荐 `neko-<uuid4>`，避免与 Sirius 大脑发的 id 撞语义），登记映射；回 `task_finished` 时**也带上这个 id**——NEKO 新版按 id 匹配，旧版按 FIFO 匹配，带上 id 两边都对。
  3. 任何层（含反射层抢占、替换式受理）终结任务时，必须通过该映射表找回原 `task_id` 回帧，不得发无 id 的 `task_finished`。

### 3.3 超时经验值

| 端 | 默认 | 来源/证据 |
|---|---|---|
| **自研（sirius-bridge）** | **复合任务 120s 默认** | spec §8.2：复合任务（合成→放置→补救缺料）合法耗时 60–90s，默认 120s；过早超时引发 pending 清理错乱 + 完成 echo 错 id |
| NEKO 大脑侧 | 90s（可配 `task_timeout_seconds`，钳制 1–295s） | `service.py:156,389`；LLM 工具包装上限 300s |

兼容层要点：**超时裁决放身体侧（120s 默认）**。若依赖 NEKO 的 90s 先到点，会出现"Mod 还在干活、NEKO 已判 timeout 并清理 pending"，随后真实 `task_finished` 到达无人认领。

---

## 4. 概念覆盖度：有对应 / 自研扩展 / NEKO 独有

### 4.1 NEKO 概念在自研协议中有直接对应

| NEKO 概念 | 自研对应 |
|---|---|
| `task` 任务 | `TaskFrame`（同构） |
| `task_finished` 终态 | `TaskFinishedFrame`（五态枚举冻结） |
| `log` 过程叙述 | INFO 级 `NotificationFrame`（`event="log"`） |
| `screenshot` 截图流 | `screenshot` 工具（拉）+ screenshot 事件（推） |
| `alert` 高危信号 | CRITICAL 级事件（`data.level="CRITICAL"`） |
| 玩家聊天 | `event="chat"` INFO 事件（兼容层聚合回 `ingame_chat`） |
| 断线重连（5s 间隔、ping 20s） | WebSocket 层实现细节，两侧各自处理，无协议级对应 |

### 4.2 自研扩展（NEKO 没有，对 NEKO 前端降级或隐藏）

| 自研能力 | schema 产物 | 对 NEKO 前端的处理 |
|---|---|---|
| `capabilities/list` 能力发现 + `protocol_version` 协商 | `schema/frames/CapabilitiesList{Request,Response}.json` | NEKO 不发起；兼容层不强制（内部仍维护能力清单） |
| `events.subscribe`（按类型/最低级别订阅） | `schema/tools/events.subscribe.json` | 隐式全订阅：NEKO 期待全部 `log`/`screenshot`/`alert` 都推 |
| `events.watch`（stat 条件触发 + 滞回 + 冷却） | `schema/tools/events.watch.json` | NEKO 无概念；兼容层可自行设置 watch 以合成 `alert` |
| 事件三级 CRITICAL/WARNING/INFO | `$defs.EventLevel`（`events.subscribe.json`） | 兼容层按级别路由：CRITICAL→`alert`、INFO→`log`/`ingame_chat` |
| `request`/`response` id 配对 + 错误码（-32700/-32600/-32601/-32602） | `ToolCall*.json` | NEKO 无请求-响应语义；错误降级为 `log` 文本 |
| 权限分级 `observe`/`input_world`/`input_gui` + token 握手 + 输入限频 + 审计 | （连接层约定，不在帧 schema） | **对 NEKO 前端同样强制**，见 §5.8 |
| 感知/输入原语（look/getGuiState/world.query/input.*） | `schema/tools/*.json` | NEKO 不直接调用；兼容层在执行 `task` 文本时内部编排使用 |

### 4.3 NEKO 独有（自研 M0 能力集无一一对应，兼容层需合成）

| NEKO 帧需求 | 兼容层合成方案 |
|---|---|
| `inventory` / `query_inventory` | 内部调 `getStats`（数值状态）合成；若需要逐格背包，M0 能力集需扩（候选：`getGuiState` 开背包读 widget 树，或未来加 `getInventory` 工具） |
| `bot_status_nl`（活动状态叙述，nudge 循环节流依据） | 兼容层按内部任务状态机合成（空闲/执行中/被中断 + 当前 skill 名） |
| `agent_status`（信息性） | 可不实现（NEKO 忽略）；实现则复用 `bot_status_nl` 数据源 |
| `ingame_chat` 批量结构 | 兼容层把自研逐条 `chat` 事件聚合成批量帧 |

---

## 5. M3 兼容层翻译要点清单（Bridge Mod 实现者）

「Bridge Mod 收到 NEKO 帧 → 翻译为内部处理 → 回 NEKO 帧」的完整路径：

1. **前端识别**：同一 Mod 同时服务两类前端——Sirius 大脑（MCP 语义全量）与 NEKO 大脑（game_agent 子集）。建议连接握手（token 校验时）声明前端类型；NEKO 前端跳过 `capabilities/list` 协商不影响其工作。
2. **task 收帧路径**：JSON 解析 → 用 `schema/frames/TaskFrame.json` 校验（type/task/task_id）→ `task_id` 缺失则生成并登记映射表（§3.2）→ **替换式受理**：新任务顶掉同前端旧任务，旧任务立即回 `task_finished{status:"superseded", task_id:<旧>}` → 交给内部执行管线（M3 最简版：截图→VLM→工具编排）。
3. **过程输出 → log**：执行过程中的叙述性文本一律转 INFO 事件，兼容层翻译为 `log` 帧（`text` 字段）。
4. **截图推流管线**（N.E.K.O 生产参数，spec §8.2）：推流 ~1Hz → 节流最小间隔 6s（窗口内折叠最新帧）→ 长边 1024px + JPEG q80 → 硬预算 100KB → 环形缓冲 3 帧。注意打包载荷 ≈ 2.3× 原始 JPEG（base64 +33% + 原始副本），**超 256KB 消息会被静默丢弃**——宁可降质量不可超预算。
5. **task_finished 回帧**：五态直通（§3.1）；`task_id` 从映射表取原值；`text` 必填且人类可读（NEKO 直接喂给对话 LLM）。
6. **事件 → 帧**：CRITICAL 事件 → `alert`；逐条 chat 事件 → 聚合 `ingame_chat`（只填结构化 `messages`，NEKO 不消费聚合 `text`）。
7. **错误与异常**：帧校验失败/未知方法（-32600/-32601）对 NEKO 无回包通道 → 转 `log` 文本；NEKO 前端断连 → 其名下 pending 任务回 `interrupted`（对端收不到也无害，本地清理账目）。
8. **安全不豁免**：token 握手、权限分级（建议 NEKO 前端默认 `input_world`，GUI 操作按需授权 `input_gui`）、输入限频 ~20 次/s、审计日志——对 NEKO 前端同样生效（对齐 Numen MCP 已验证实践）。
9. **大小与超时**：WebSocket 读侧不设小上限（NEKO 客户端 `max_size=None` 是它对我们发大帧的容忍，不代表我们可无限发，见第 4 条）；写侧遵守 256KB 上限。任务计时用身体侧 120s 默认（§3.3）。
10. **测试对齐**：`sirius-brain/sirius_brain/mock/` 已实现 `task → task_finished` 五态剧本（按 task 文本匹配状态/延迟）与事件推送回放；兼容层测试可直接复用同款剧本 JSON 思路（Java 侧），断言 task_id 原样回传与乱序完成归属。

---

## 6. 相关冻结 schema 产物索引

协议版本 **1.0**；每个文件自包含（`$defs` 内联、`$ref` 均为 `#/` 片段），Java 侧（everit-org/json-schema、networknt）可单文件加载。完整清单见 `sirius-brain/schema/index.json`。

| 映射相关产物 | 内容 |
|---|---|
| `schema/frames/TaskFrame.json` | NEKO `task` 帧契约（`task_id` 必填——自研侧强化的点） |
| `schema/frames/TaskFinishedFrame.json` | NEKO `task_finished` 契约 + 五态枚举（`$defs.TaskFinishedStatus`） |
| `schema/frames/NotificationFrame.json` | 事件帧（log/screenshot/alert/chat 的自研载体） |
| `schema/frames/ToolCallRequest.json` / `ToolCallResponse.json` / `ToolCallError.json` | MCP 语义请求-响应-错误对象 |
| `schema/tools/screenshot.json` | 截图工具参数（`tier:"full"\|"crop"`、`bbox`、`quality 0–100`） |
| `schema/tools/events.subscribe.json` | 事件订阅（`min_level` 三级枚举） |
| `schema/tools/getStats.json` | 状态查询（合成 `inventory` 帧的数据源） |
