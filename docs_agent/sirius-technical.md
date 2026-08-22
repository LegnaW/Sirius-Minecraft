# Sirius · 天狼星 —— 分层 Agent 架构技术规格

> **Sirius** = 双星系统：天狼星 A（明，规划器/大模型）+ 天狼星 B（白矮星，暗而致密，执行器/小模型）——一明一暗，都是恒星。
> 子项目命名：**sirius-bridge**（NeoForge 客户端 Mod，Java，眼与手）· **sirius-brain**（**Python** 后端大脑）
> GitHub 仓库名：**Sirius-Minecraft**（避免与其他 Sirius 重名项目撞车；内部文档沿用 Sirius 称谓）

> 状态：实现推进中（实时更新） · 最后更新：2026-08-21（M4.1 修复轮收尾）
> 设计理念与动机见姐妹篇：[sirius-design.md](../docs_human/sirius-design.md)（人读的纯思路版本）
> 本文档面向实现：代码落点、接口定义、数据结构、参数与边界条件。

---

## 1. 现状代码盘点

| 模块 | 位置 | 现状 |
|---|---|---|
| 多模型 | `src/models/prompter.js:59-75` | 已支持按功能分模型（chat/code/vision/embedding），但非层级分工 |
| 命令 | `src/agent/commands/actions.js` + `queries.js` | 35+ 命令（动作类/查询类） |
| 反射层 | `src/agent/modes.js` | 10 个每 tick 模式，`interrupts: ['all']` 抢占一切 |
| 自驱循环 | `src/agent/self_prompter.js` | "必须输出命令"的机械循环，无任务分解 |
| 中断 | `src/agent/action_manager.js` | 单槽队列，`stop()` 300ms 自旋 + 10s `cleanKill` 强杀；协作式（`interrupt_code` 标志） |
| 视觉 | `src/agent/vision/` | prismarine-viewer + three.js 离屏渲染，`camera.capture()` 截图 |
| 记忆 | `src/agent/memory_bank.js` + `prompter.js:167` | 仅命名地点字典 + `$MEMORY` 自由文本 |
| 代码执行 | `src/agent/coder.js` + `library/skill_library.js` | `!newAction` SES 沙箱，一次性、不沉淀 |
| 通信 | `src/process/` | MindServer（Express + socket.io，8080） |

---

## 2. 分层架构与新建部件

```
第3层 规划器 Planner（大模型）：社交/意图/任务分解/监督
第2层 执行器 Executor（小模型）：任务卡 → 命令循环 → 报告
第1层 反射层 Reflex（无LLM）：modes.js，不动
```

| 新建部件 | 作用 | 复用基础 |
|---|---|---|
| `TaskManager` | 任务卡队列、状态机（pending/running/done/failed）、中断分发 | `action_manager.js` 队列模式（逻辑移植） |
| 规划器/执行器 prompt 模板 | 各自视角 | profiles 模板体系（逻辑移植） |
| 双 history | 规划器（社交+摘要）/ 执行器（任务+观测） | `history.js` ×2 实例（逻辑移植） |
| 汇报通道 | 执行器 → 规划器事件流 | asyncio 事件总线 |

### 2.1 技术栈裁决：sirius-brain 用 Python

**理由**：
- N.E.K.O 参考代码同语言（证据数学/反思层/检索管线/nudge 循环可直接对照移植）
- MCP 官方 Python SDK 为参考实现；LanceDB 原生 Python 客户端；pydantic 做协议契约校验
- 与 Mindcraft CE 的 Python 评估脚本、MineCollab 工具链同语言
- 团队维护成本（决定性因素）

**代价与对策**：
- Mindcraft CE（JS）代码无法直接复用 → 降级为"读逻辑、Python 重写"（M5 本来就要大改，损失可控）
- mineflayer（纯 JS）并行轨失效 → **mock 优先**：M0 的 Python mock body 做厚（回放录制协议帧 + 可脚本化响应），大脑全部逻辑对 mock 开发；可选百行 Node 垫片把 Mindcraft CE 包成"sirius 协议身体"供早期真机联调
- 协议跨语言（Java↔Python）→ JSON Schema 冻结 + 双侧 pydantic/代码生成校验，M0 契约优先的重要性上升

**栈**：Python 3.11+ · asyncio + websockets · `mcp` SDK（协议层）· pydantic（schema）· LanceDB · pytest（含协议帧录制重放）· uv（打包）

---

## 3. 中断机制

### 3.1 优先级

```
L0 反射层（保命） > L1 规划器中断 > L2 执行器自查/超时 > L3 正常执行
```

### 3.2 三档语义

| 档位 | 实现 |
|---|---|
| DEFLECT | 下轮 LLM 迭代前注入 system 消息 |
| CANCEL | `requestInterrupt()`（现有）+ `self_prompter.interrupt` + 清执行器 history |
| PAUSE | 同 CANCEL，先写检查点 `{已完成子步骤, history快照, 背包快照}`，resume 时重注入 |

### 3.3 边界条件

- LLM 调用中不可中断 → 检查点：每 LLM 迭代开头 + 每条命令之间
- 中断防抖：200ms 窗口取最后一条
- 中断风暴：`action_manager.js:72-80` 快速循环检测（5 次杀进程）会误伤"被中断"，需区分被中断/自发循环
- 10s 强杀一致性：`cleanKill` 前须确保任务状态机同步就范

---

## 4. 工具暴露

> **M3.5 落地注记（2026-08-20）**：本节规划器/执行器命令体系是 M5 蓝图（Mindcraft 命令风格）。M3.5 已按"操作型功能入 bridge、任务级组合在 brain"先例落地执行器侧对应物——VLM 工具表 14 项中的 walkTo/digBlock/collectBlock 即任务级原语（`sirius-brain/sirius_brain/agent/tools.py`，契约描述见 §8.2），M5 分层时执行器直接复用这套原语作动作底座。

### 4.1 规划器

```jsonc
delegateTask({ goal, success_criteria, constraints, tools_allowlist,
               interrupt_policy, priority, timeout })
interruptTask({ task_id, mode: "cancel"|"pause"|"deflect", reason })
queryTaskStatus({ task_id? })     // 无参 = 全部
reprioritize({ task_ids_ordered })
saveMemory / searchMemory
worldSummary()                    // L1 记忆 + 执行器报告聚合
```

**快车道**（准入：原子 + ≤5s + 无需观测迭代）：
`!givePlayer !consume !equip !discard !stats !inventory !viewChest !goToPlayer(近) !stay !placeHere(单方块) !showVillagerTrades`

快车道执行：执行器空闲→直接执行；忙→DEFLECT 让路后执行再恢复。规划器发出后不阻塞，完成走事件回调。结果走 `openChat` 旁白，不进规划器决策循环。

**禁止**：多步/长时命令（`!collectBlocks !craftRecipe !attack !searchFor* !followPlayer !newAction` 蓝图建造）；`!nearbyBlocks` 等大输出观测（防上下文膨胀）。

### 4.2 执行器

```jsonc
// 观测：现有 queries.js 全量
!stats !inventory !nearbyBlocks !entities !craftable
// 动作：现有 actions.js，按任务卡 tools_allowlist 过滤
// 汇报（新增）：
reportDone({ result, evidence })
reportBlocked({ reason, observation })
requestDecision({ question, options, default, timeout })  // 默认30s超时后按default继续
reportProgress({ step, done, total })
requestMemory(query)   // 限频；主通道是派单注入
```

**禁止**：直接和玩家聊天 / delegateTask / 无限制 `!newAction` / `!setMode`

`success_criteria` 必须可机检（如 `"inventory.diamond >= 3"`），完成判定不消耗大模型。

---

## 5. 通信协议

### 任务卡（规划器 → 执行器）

```jsonc
{
  "task_id": "T-42",
  "goal": "清理矿井入口的骷髅群",
  "success_criteria": "nearest skeleton within 32 blocks == null && health > 10",
  "constraints": ["不许破坏方块", "血量<8立即撤退"],
  "tools_allowlist": ["!attack", "!goToCoordinates", "!stats", "!inventory"],
  "interrupt_policy": "deflect",
  "timeout_mins": 10,
  "context": ["<相关记忆/知识/skill 检索 top-3 注入>"]
}
```

### 报告（执行器 → 规划器）

```jsonc
{ "task_id": "T-42", "type": "blocked",
  "reason": "骷髅在岩浆后，无法近战",
  "observation": "<!stats + !nearbyBlocks 输出>" }
```

---

## 6. 记忆系统

### 6.1 四类型 × 四层

```
L0 工作记忆：执行器 history（上下文内）
L1 活跃记忆：上下文内注入卡片，硬预算 ~10卡/2000token
L2 长期记忆：向量库（episodic + procedural）
L3 知识库：向量库（世界/Mod 知识，imported|learned|player 三源）
```

L1 得分：`score = w1·recency + w2·importance + w3·frequency`，超预算最低分降级，同场景再现强制晋升。

### 6.1.1 证据数学（吸收自 N.E.K.O，替换朴素置信度）

知识卡片的置信度不用 `verified_count ±1`，改用证据分：

```
evidence_score = effective_reinforcement − effective_disputation
effective_reinforcement = r × 0.5^(age/REIN_HALF_LIFE)
effective_disputation  = d × 0.5^(age/DISP_HALF_LIFE)
```

- **强化/反驳独立衰减时钟**（"好事渐渐淡忘、教训记忆犹新"可分别调参）
- **读时计算，无状态转换**：衰减在读取时算出，无后台定时任务
- **状态分层**：score ≥ promoted阈值 → 固化；≤ 负阈值持续 N 天 → 归档（遗忘制度化）
- **protected 条目**（玩家直教/角色卡来源）= 无限分，永不淘汰
- 来源：[N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) `memory/evidence.py`（Apache-2.0，思路借鉴）

### 6.1.2 反思层：事实 → 反思 → 人格（吸收自 N.E.K.O）

```
L2 事实（被动积累）→ 反思（规划器主动提炼的假设，pending）
                  → 人格（被玩家确认后固化的稳定认知）
```

- **对话式验证**：反思在闲聊时自然说出（"我发现你好像喜欢晚上下矿？"），用玩家反应验证——社交即实验
- **3 天未被否认自动转正**；被否认则降为反驳信号
- 人格层是**活的**：通过确认的反思逐渐演化（一致性 ≠ 不变性）——解决"人格一致性"待决项的一半

### 6.1.3 检索管线与说话者信任（吸收自 N.E.K.O）

- **向量粗排 ×3 超采样 + 小 LLM 精排**：余弦分不清"喜欢猫/讨厌猫"（≈0.78），语义近立场反的记忆需 LLM 裁决
- **speaker_trust**：知识卡片记录陈述者，多人服务器中管理员/熟客/路人的陈述权重不同

### 6.2 卡片结构

记忆卡片：`{content, type, importance, tags, created, last_accessed, source}`

知识卡片（L3）：
```jsonc
{
  "content": "创意矿石需要铁镐以上才能挖掘",
  "mod": "create", "namespace": "ores",
  "source": "imported",          // imported=1.0 | learned=0.5起 | player=1.0
  "confidence": 0.9,
  "verified_count": 2,           // 印证+1
  "contradicted_count": 0        // 反驳+1，超阈值降级/删除
}
```

规则：冲突时 imported/player 赢过 learned；learned 需连续 2 次独立印证才升 0.8+；矛盾累积可派实验任务验证；注入时附置信度。

**图像附件**：
```jsonc
"image": {
  "raw": ".../full.jpg",    // 存档，永不注入
  "crop": ".../crop.jpg",   // 默认注入档（视觉LLM提炼时输出bbox裁剪）
  "thumb": ".../thumb.jpg", // 需环境上下文时
  "caption": "...",          // 唯一参与向量检索的文本
}
```

配置：
```js
"memory_image": { "enabled": true, "inject_tier": "crop",
  "max_images_per_task_card": 1, "raw_quota_gb": 2, "vlm_required": true }
```

规则：图像=证据，caption=假说（置信度以文字结论为准）；非 VLM 执行器自动降级纯文本；仅入库卡片截图保留，过程截图清理。

### 6.3 读写路径

- 写：仅规划器（记忆管理员）提炼写入；执行器原始日志不入库
- 读：规划器 `searchMemory`；执行器主通道=派单时 top-3 注入 `context`；`requestImage(card_id, tier)` 主动升级
- L3 触发式检索：观测到未知方块/物品 → 查 L3 → 命中注入工作记忆；未命中 `!searchWiki` 兜底

### 6.4 技术选型

向量库 LanceDB（`agent-system` 分支已验证）；embedding 复用 `embedding_model`（Andy API `/embeddings`）；L1 进程内 JSON，30s 持久化 `bots/<name>/memory_active.json`。

### 6.5 人设系统（persona customization）

**三层人设结构**（对齐记忆系统）：
```
第1层 底座（不可自定义）   出厂设定 + 安全底线 + 架构指令
第2层 用户人设卡（protected，证据分无限） 身份/性格/说话风格/示例对话/底线
第3层 习得人格（可演化）   反思层确认的人格类卡片
```

- 用户卡**不能覆盖第1层安全底线**；玩家口头修订人设 → 存 protected 玩家记忆（不偷改用户文件），可建议导出进卡
- 卡与习得人格冲突（卡"高冷" vs 反思"粘人"）→ 双层同注提示词，规划器自然融合

**人设卡格式**（自有为超集 + 三家兼容导入）：
```markdown
---
name: 小焰
compat: sirius-persona/1
appearance: { skin, voice }        # 皮肤/TTS 音色引用
behavior: { proactive, chattiness, familiar }   # 行为偏好参数
memory_seeds: [ ... ]              # 初始玩家记忆（可选）
---
## 身份 / ## 性格 / ## 说话风格 / ## 示例对话 / ## 底线
```
- 兼容导入器：Numen persona 格式（结构一致直接映射）/ SillyTavern 角色卡 / 自有格式
- **示例对话 = few-shot**，比形容词更能约束风格（Numen 卡实证）
- `说话风格` 节同时编译为**旁白短语模板集**（反射层/bridge 自动旁白零 LLM 成本出人味）

**系统提示词组装管线（规划器）**：
```
[底座：架构指令+安全底线] → [用户人设卡全文] → [习得人格卡片]
→ [L1活跃记忆+玩家记忆精选] → [现实时间+语言+反射总览]
→ [工具表（按快/慢车道过滤）] → [安全底线重申（recency 优先）]
```

**注入防护**（用户内容进 system prompt 的必然代价）：
1. 权限与人格彻底分离：人设卡无法授予任何工具权限（权限只来自 settings/任务卡）
2. 安全底线在人设卡之后**再注入一次**并声明覆盖关系
3. 人设卡 lint：加载时扫描改写系统指令的模式（"ignore previous"类），警告用户
4. `config/personas/*.md` 文件监听热重载；每 bot 实例绑一个人设

---

## 7. 底层交互与 Skill 沉淀

### 7.1 「点击屏幕」两种实现

- **点击世界**：屏幕坐标 → 相机反投影 → 世界射线 → raycast → `lookAt` + 攻击/使用/放置。纯 mineflayer，无需客户端
- **点击 GUI**：必须真客户端输入注入（mineflayer 只认原版 `clickWindow`，Mod GUI 走私有包）→ 依赖 Bridge Mod `/input`

规划器亲手模式 = 应急（触发：执行器上报工具不足/预判无工具）。安全栏：注入前校验任务卡 `constraints`；关键 GUI 点击前截图留证。

### 7.2 Skill 卡片

```jsonc
{
  "name": "operate_create_mixer",
  "description": "操作创意Mod动力搅拌机",     // 进向量库，派单检索
  "params": { "items": "ItemName[]", "speed": "int" },
  "precondition": "nearbyBlocks contains 'create:mixer'",
  "success_criteria": "mixer GUI closed && items consumed",
  "body": "<JS：命令序列 / API / 输入注入宏>",
  "author": "planner",
  "stats": { "runs": 12, "success_rate": 0.92 },
  "status": "draft" | "verified" | "deprecated",
  "created_from": "task T-107 轨迹"
}
```

生命周期：轨迹完整记录 → 规划器复盘提炼 → 草稿独立成功 2 次转正 → success_rate 跌破阈值转 deprecated → 上报重学。

执行器工具三级：原子命令（天生）→ 已验证 skill（学会）→ 上报规划器（开新路）。

---

## 8. 身体层选型：真客户端 + Bridge Mod（最终裁决）

### 8.1 选型结论（含评估弯路记录）

三具身体完整评估（本地 Numen 源码 `E:\minecraft-projects\minecraft-numen-1.21.1`）：

| 维度 | mineflayer | Numen 服务端假玩家 | **真客户端（选定）** |
|---|---|---|---|
| Mod 内容渲染 | ✗ 离屏不认识 | ✗ **无像素** | ✅ 客户端装同款 Mod，天然正确 |
| 陪玩共视觉（皮肤/boss/风景） | ✗ | ✗ 制度性缺失 | ✅ 截图即亲眼所见 |
| Mod GUI 操作 | ✗ 私有包不可见 | ✗ 假玩家无客户端 | ✅ **GUI 在它屏幕上，可看可点** |
| 进任意服务器 | ✅ | ✗ 服务端必须装 Numen | ✅ 对服务器=普通玩家 |
| 世界数据深度 | 协议全量 | capability 深读 | 客户端数据（够用，稍浅） |
| 资源开销 | 极低 | 低 | 高（每bot一客户端，可无头化） |
| 寻路 | pathfinder 现成 | 服务端引擎现成 | 需自研（Baritone 已证明可行） |

**决定性论据**（都指向陪玩产品本质）：
1. 共同视觉体验：陪玩 bot 必须看得到玩家皮肤、感受得到 mod boss 的视觉冲击——服务端假玩家制度性做不到
2. 可携带性："带 AI 朋友进别人的服务器"要求服务端零安装

**架构**：机器人拥有独立 Minecraft 客户端（装与服务器一致的 Mod）+ 我们的 Bridge Mod（眼与手）+ 后端大脑（本文档全部设计）。

### 8.2 Bridge Mod（`sirius-bridge`）规格（原设计复活，升级为正式方案）

**双向消息协议**（WebSocket，MCP 语义）：
- 后端 → Mod：工具调用（请求-响应，JSON Schema 参数 + `capabilities/list` 发现 + 版本协商）
- Mod → 后端：事件推送（一等公民，主动唤醒 agent，`{type:"notification", event, data, timestamp, seq}`）

**能力集**（v1.3 实现态；新增参数/工具标注引入版本，缺省参数行为与 v1.0 字节级兼容）：
```jsonc
// 感知（原语，不含加工）
screenshot({ tier: "full"|"crop", bbox?, quality })   // 它亲眼所见
look({ yaw, pitch }) / lookAt({ x, y, z, turn_speed_deg_s? })
      // turn_speed_deg_s 30..720（v1.2）：固定角速度平滑转头，两轴同速推进、<1° 收口；
      // 缺省=瞬间转（M2-D 行为不变）。新 turn 替换旧（supersede），旧等待者 converged:false 返回
getGuiState()      // widget 树：standard（结构化）/ fallback（矩形+贴图名）
world.query({ type: "blocks"|"entities", range, filter? })
      // filter（v1.1）：1..16 条 registry id（短名自动补 minecraft:）或 #tag（展开 tag 成员）；
      // blocks 命中按与玩家距离升序返回 cap 32 + truncated；entities 按实体 type 过滤（拒 #tag）
      // entities 载荷（v1.2）：minecraft:item 实体条目带 item（ItemStack 注册名）与 count——注册名可匹配，
      // name 是客户端本地化中文名不可用于匹配。M4 起条目另带 category（MobCategory 小写注册表名，
      // monster=敌对——模组怪自动归队）与 width（碰撞箱宽，flee 危险半径输入）——纯增响应字段，不 bump
getStats()
      // v1.3 起响应增报 yaw/pitch（StatsSnapshot 两字段）：MovementLook 诊断数据源 +
      // unstuck 扇形爆发基准（旧 bridge 无字段时 brain 回落 0°）——纯增响应字段随 1.3 一并升版

// 动作层操作原语（v1.2；无可靠事件路径时的执行原语，见 §8.3 分工三层）
dig({ x, y, z, timeout_ms? })
      // 智能挖掘：触及≤4.5 校验（超距 -32602 教学文案）→ 300deg/s 平滑瞄准 → 动作层监视按住
      // （startDestroyBlock/continueDestroyBlock，焦点免疫、遮挡穿透）→ 安全前置（邻格液体/上方落块拒绝）
      // → result: broken/already_air/timeout/not_digging/blocked_liquid/blocked_falling + block/elapsed_ms/
      // broken_via_occluder?/reason?
chat.send({ string: 1..256 })    // v1.3（M4.1）
      // 进程内 ClientPacketListener.sendChat 直发聊天——vanilla 聊天屏 ENTER 的同款入口，
      // 死亡屏等任何 GUI 无法屏蔽（T 键物理管道表达不了的动作在 vanilla 自己的层做，look/dig 同例）。
      // 守卫与 input.* 同款（-32011/-32012/-32010 各一次计费，PermissionContracts.Action.CHAT 仅 observe 拒绝；
      // 屏幕状态刻意不参与判定——屏幕免疫就是它的存在意义）。brain 侧消费方是 LoopClient.say 与反射直发
      // （死亡/低血播报），不进 VLM 工具表；旧 jar 回 -32601 时 say 自动回落 GUI command 路径

// 输入（MCP 式 schema + 校验）
input.mouseMove({ x, y })
input.click({ button, count, hold_ms? })
      // hold_ms 0..10000（v1.1）：PRESS 主线程内联 + RELEASE 延迟调度；与显式 count 互斥（-32602）。
      // 注意：事件层长按受 vanilla 焦点双门控（§8.3），挖掘场景应改用 dig
input.key({ code, duration_ms, modifiers })
input.text({ string })

// 事件
events.subscribe({ types: [...], min_level })
events.watch({ stat, condition, hysteresis, cooldown_ms })
```

**协议版本演进**（三处同步点：`sirius-brain/schema/index.json`（export_schema.py 导出）/ `sirius-bridge` `Capabilities.java` / `sirius-brain` `bridge/config.py` 默认 hello 版本）：

| 版本 | 轮次 | 内容 |
|---|---|---|
| 1.0 | M0 冻结 | 基础感知/输入/事件/任务帧（27 个 schema） |
| 1.1 | M3.5 T1 | world.query 加 `filter`；input.click 加 `hold_ms`；entities 结果补 `truncated` |
| 1.2 | M3.5 T6/T7 | 新工具 `dig`；lookAt 加 `turn_speed_deg_s`；entities item 载荷加 `item`/`count`（纯增响应字段，协议层面无需 bump 的边界情况，随 T6 一并升版） |
| 1.3 | M4.1 | 新工具 `chat.send`（进程内直发聊天，GUI 免疫）；getStats 响应增 `yaw`/`pitch`（纯增响应字段随本轮一并升版）。三处同步：`bridge/config.py` / `export_schema.py`（schema 重导出含 chat.send.json）/ `Capabilities.java` |
| —（不 bump） | M3.6 / M4 | M3.6：dig 响应加 `drops`（经验掉落报告）；M4：entities 载荷纯增 `category`/`width`——均同 v1.2 item/count 的纯增边界情况，版本维持 1.2 |

**brain 侧 VLM 工具表（15 项，M3.5 定 14 + M3.6 pickup；`sirius-brain/sirius_brain/agent/tools.py` default_registry）**：9 个 bridge 工具直通（getStats/getGuiState/world.query/screenshot/lookAt/input.*×4，参数 schema 读冻结产物）+ 4 个任务级原语（walkTo/digBlock/collectBlock——handler 包装 `agent/primitives.py`，描述写 Numen 式契约：受理即执行/失败读建议/同参数重发续做；pickup——M3.6 注册，Numen collect_items 语义+多人服礼仪话术）+ command + finish（agent 层定义）。walkTo/collectBlock/pickup 是 brain 组合原语（Baritone #goto 走 command 聊天通道），不是 bridge 工具。**M4.1 注记**：v1.3 的 chat.send 不进 VLM 工具表（表维持 15）——消费方是 `LoopClient.say` 与反射直发（死亡/低血播报），属 brain 内部播报通道，模型无需（也不应）直接选择通道。

**事件分级**：CRITICAL（溺水/着火/被攻击/死亡/断线→反射层立即或 L1 中断）、WARNING（饥饿/GUI变化→排队）、INFO（聊天/天气→缓冲）。

**任务帧协议与状态分类学**（吸收自 N.E.K.O `game_agent_minecraft` 插件，含 task/log/screenshot/task_finished 帧）：
```jsonc
// 后端 → Mod：{ "type": "task", "task": "...", "task_id": "<uuid>" }
// Mod → 后端：{ "type": "task_finished", "status": "ok|failed|interrupted|superseded|timeout",
//               "task_id": "<原样回传>", "text": "..." }
```
- **task_id 必须原样回传**（否则退化为按完成序匹配，out-of-order 完成会错误归属——N.E.K.O 已知限制）
- `superseded`（被新任务顶替）是**中性**状态，不算失败；**失败绝不能报成 ok**（否则上层数字人格会把翻车当成功叙述）
- 超时经验值：复合任务（合成→放置→补救缺料）合法耗时 60-90s，默认 120s；过早超时引发 pending 清理错乱 + 完成 echo 错 id

**截图流预算管线**（N.E.K.O 生产参数）：
```
推流 ~1Hz → 节流最小间隔 6s（窗口内折叠最新帧）
→ 长边 1024px + JPEG q80 → 硬预算 100KB
（打包载荷 ≈ 2.3×原始 JPEG：base64 +33% + 原始副本；超 256KB 消息上限会被静默丢弃）
→ 环形缓冲 3 帧
```

**协议兼容层（已取消，2026-08-19 用户裁决）**：原计划 Bridge Mod 实现 N.E.K.O game_agent 协议作为第二前端——裁决 N.E.K.O 是独立项目（自有感知与思维链路），兼容它意义不大，**降级为纯设计参考**。已冻结的任务帧（task/task_finished）保留在协议中（mock/客户端在用），不再作为兼容承诺。

**安全模型（内建）**：默认 localhost；token 握手；权限分级 `observe`/`input_world`/`input_gui`；输入限频 ~20次/s；审计日志。（对齐 Numen MCP 服务端已验证的安全实践）

**分工原则（2026-08-20 M3.5 修订：原"哑管道"两层升三层）**：
1. **感知原语化**：Mod 上报未加工的世界数据（screenshot/world.query/getStats/getGuiState），不做语义加工；
2. **输入标准化**：一切注入走可校验的 schema 化原语（input.*）与聊天通道（command）；
3. **动作层操作原语**：无可靠事件路径的连续操作（事件层长按被 vanilla 焦点双门控废掉、视角控制）允许 Mod 提供操作型工具——M2-D `look` 开先例、M3.5 `dig` 确立为模式（真机证据链见 reports/M3.5-T6.md §二.1）——对 brain 暴露意图级接口（"挖这个方块"→结果判定），不暴露按键细节；**任务级语义组合仍在 brain**（walkTo/collectBlock 是 brain 原语，Baritone 经 command 通道驱动，Mod 无任务概念）。

### 8.3 反射层与寻路（brain 侧，设计借鉴 Numen；归属 2026-08-19 裁决、寻路 2026-08-20 裁决、反射层 M4 落地 2026-08-20、M4.1 独立存活修复 2026-08-21）

- **归属裁决（2026-08-19）**：Bridge 按分工三层提供原语（见 §8.2 分工原则），任务语义与处理归 brain——反射层实现为 **brain 侧无 LLM 规则模块**，消费 Bridge 上报的 CRITICAL 危险事件（drown/fire/health_low/death，M2-B 已实现），回灌 input.* 指令；localhost WS 往返延迟对本能响应足够（MLG 级毫秒精度需求届时再议，可设计"预武装模式"由 brain 预授指令模板）
- **本能链（M4 落地版，2026-08-20；实现 `sirius-brain/sirius_brain/agent/reflexes.py` ~900 行 + loop.py 接线）**：核心主张**脊髓不过大脑**——保命动作在确定性代码里完成、零 VLM token，认知层只收事后简报。调度裁决沿用序数固定分层的原始论证（2026-08-19 依据 Numen 现行代码修正，弃每 tick 数值竞价）：按注册序自上而下问 `can_run()`，**首个 True 胜出并 break**（层内单候选，结构性消灭比较——可预测性优先于"最优性"，Numen TaskSelector.java:21-67 的裁决理由）。落地形态：
  - **等级框架**：`ReflexLevel` 三级**严格单调能力束**（OBSERVER L0 观察 ⊂ SELF_PRESERVE L1 自保·默认 ⊂ GUARD L2 自卫·预留枚举位）——不做每条反射的开关（Numen 删空闸教训）；L2 客户端战斗模块后置独立轮次，接入需同步修订 loop 的"禁止攻击"安全约束。切换=聊天命令"反射等级 观察/自保"（`match_reflex_level_command` 在 `_on_chat` 识别即切换+播报），**人类-only**——不进 VLM 工具表，VLM 无权升攻击等级；只改内存不持久化（重启回 `LoopConfig.reflex_level` 默认）。`instincts_section(level)` 按等级生成系统提示 `<本能>` 节——**等级是代码层与认知层唯一同步点**。等级门控在调度循环（`chain.level.value > self.level.value` 跳过），danger 事件 handler 不门控——**L0 关动作不关感知**（〔危险〕行照进认知）
  - **调度器 `ReflexScheduler`**（Numen CompanionBrain 的 asyncio 移植）：0.5s 轮询协程（`poll_interval` 可配，测试 0.05s），每轮 `sample_body()` → 按注册序单候选执行 → 睡轮询间隔；`busy` 标志是 AgentLoop 的任务门（反射 act 期间不开新任务——先归位再做事）；单条反射/采样异常不杀调度器
  - **三档打断**（`InterruptKind = Literal["none","cooperative","preempt"]`）：`none`=旁路（不碰任务不碰按键，如 speaking_look 转头看人）；`cooperative`=短暂接管按键后归还、任务不停（换气/脱困——真客户端多输入天然共存）；`preempt`=经 `AgentLoop.request_preempt(reason)` 掀翻当前任务（撤离/逃离/低血/死亡）——end_reason 记 `preempt`、不播报（反射路径自己上报），与玩家急停的回话完全分流
  - **BodyState 信号源（实事求是版）**：getStats 轮询 2Hz（position/health/air/in_game/alive + **yaw（v1.3 起，unstuck 扇形爆发基准）**——schema 实测无 on_fire/眼位水）；眼在水中=air<300 时才花一次 `world.query(blocks, filter=water, range=3)` 查眼位方块（truncated 保守为 True——M3.5 T5a 截断在排序前的教训）；on_fire/dead/health_low=CRITICAL 事件置位（M2-B danger 采样器，边沿+5s 冷却；dead 另有 alive=False 轮询兜底与复活 5s 宽限重臂闩）；threat/companion=entities 低频采样（每 2 轮 1Hz）：`category=="monster"` 且进入危险半径（`width/2 + 1.5`，Numen Menace 简化——不查 attribute/攻击距离，远程怪偏保守、低血反射兜底）；movement_active=AgentLoop 在 walkTo/collectBlock 执行期置位（脱困的"有输入"信号 + M4.1 起换气 #pause 的配对条件）。事件订阅**一次带全类型** `["chat","death","fire","health_low","drown"]` 且不带 min_level——bridge 订阅是单槽替换语义，CRITICAL 过滤会把 INFO 的 chat 滤掉
  - **L1 七条反射**（注册序即优先级；触发阈值全部来自精读实测值）：

    | # | 反射（id） | 触发 | 动作 | 打断 |
    |---|---|---|---|---|
    | 1 | 死亡 death | CRITICAL death / alive=False（reported 闩，复活过宽限自动重臂） | preempt + **直发播报死亡坐标（chat.send，死亡屏免疫）** + 〔紧急〕注入，等玩家指令**不自动重生** | preempt |
    | 2 | 低血 health_low | CRITICAL health_low（8s 本地冷却） | preempt + #stop（**safe_command**）+ **直发警报（chat.send）** + 〔紧急〕注入 | preempt |
    | 3 | 撤离 fire | CRITICAL fire 置位（act 开头清位，再触发靠新事件边沿） | preempt + #stop（**safe_command**）+ 20 格找水优先，否则反向撤 5 格（反向=近 3s 运动速度反方向，无近期运动取 +X——getStats 读不到火源方向） | preempt |
    | 4 | 逃离 flee | 12 格内 monster 进入危险半径（6s 冷却） | preempt + #stop（**safe_command**）+ 反向走 8 格（walk_briefly 超时即收，绝不恋战）+ 简报，**不还击** | preempt |
    | 5 | 换气 breath | 眼在水中且 air≤240/300（dead 门控） | **movement_active 时先 #pause 后 try/finally #resume（Baritone 让路，T5 裁决）**；按住 SPACE **按满 800ms 等释放再复查**（M4.1 压测修：旧 400ms/500ms 歇的低占空比深水净上浮≈0）循环到脱离；10s 封顶仍浮不上→聊天求助 | cooperative |
    | 6 | 脱困 unstuck | 2s 窗有移动输入但位移<0.75 格（6s 冷却） | 3 段 137° 扇形 lookAt+burst（W 600ms+SPACE 300ms 周期跳）；任务不停。**扇形基准取当前 yaw（getStats v1.3；旧 bridge 回落 0°）** | cooperative |
    | 7 | 注视 speaking_look | 播报窗口 2.5s 内且 16 格内有其他玩家（1.2s 最小间隔） | lookAt 对方头部（平滑转头由 bridge turn_speed 兜底；不写简报——常态不是事件） | none |

  - **事后知会（非记忆）**：反射 act **结束才写** `behavior_log`（先归位再宣布；act 中途死亡/被取消则该行不写——设计非 bug）；AgentLoop 每轮 VLM 调用前 `_inject_reflex_notices` **替换式**注入一条〔本能反应〕消息（尾部 500 字符，历史里恒一条）；death/低血走 `inject_urgent` 排队、下次调用前/任务开头 drain 成〔紧急〕（排队而非直插活动 messages——vlm.chat 在工作线程遍历 messages，并发 append 有竞态；任务死了自动留给下一任务）。无持久化、无证据、无检索——纯消息；behavior_log（deque）是 M6+ 危险记忆的天然挂点
  - **与 Numen 原设计的落地差异**（诚实清单）：任务不可 pin 反射；抢占无"截止时间冻结+恰好一次结算"（brain 任务是 VLM 工具循环、无截止时间簿记——重派=重算的既有裁决天然涵盖）；边沿自持改为每条反射内部管理触发窗/冷却/一次性闩，**轮询为主、CRITICAL 事件只作加速器/置位**（原设计的"胜者变化那一 tick 才 stop"边沿中断未单列——preempt 本身就是一次性动作）；反射短途行走用 `walk_briefly`（#goto+轮询到位+超时即收），不走 Primitives 的长超时/看门狗契约（撤退不恋战）
- **M4.1 修复轮注记（2026-08-21，独立存活 + 自然转头）**：M4-rerun 用户观看版暴露五缺陷，修复与裁决全记录在 `reports/M4.1.md`（两个预设假设被证据推翻：TurnController 压制说——审计日志零旋转写入，真因 Baritone 该构建行走不转头；令牌桶按 tick 计费说——源码全 `tryAcquire(1)` 按调用计费，-32010 实为演示脚本管道化并发，无需改码）：
  - **命令串行锁下沉 `BridgeClient.command`**（客户端级 `_command_lock`，覆盖探针/反射/调度器一切发送方；LoopClient 锁保留，锁序 loop→client 无死锁）+ **GUI 确认时序**：T 后轮询 getGuiState 等聊天屏打开（其他 GUI 占用即拒绝盲发——顺带消灭"ENTER 误点 GUI 按钮"旧风险）、ENTER 后等聊天屏关闭（关不掉=发送失败，ESC 丢弃残留防串扰）；身体无 `screen_open` 字段自动退回 M2-D 定时等待
  - **bridge `MovementLook`（移动自然转头）**：每 tick（`ClientTickEvent.Post`）水平速度 ≥0.05 b/t（≈1 m/s）时按**速度矢量方向**以 300°/s 固定角速度只写 yaw（pitch 不碰——不扰动瞄准）；让位规则=TurnController 活跃 turn（显式 lookAt/dig 瞄准）优先、与运动方向夹角 ≤2° 死区不写；config `movement_look`（默认 true）。真机：#goto 40 格 43 样本 yaw 覆盖 10 个度值，运动中 |yaw−运动方向| 中位 0°/P90 8°/max 16°
  - **播报直发通道**：死亡/低血播报走 `chat.send`（GUI 免疫，见 §8.2）——压测实证"低血警报发出前毫秒级死亡、死亡屏占 GUI 吞掉 T 键"的窗口期竞争；`LoopClient.say` 封装（echo 登记 + 命令锁 + 旧 jar -32601 自动回落）
  - **分段存活压测结论（反射层独立存活验收，用户修正的分段制）**：五场景各 120s、AgentLoop 只挂调度器零任务消费、场景前 saturation 效果消除饥饿变量——火/卡位/综合混灾 PASS；怪群 DEATH（**设计边界非缺陷**：L1 禁攻击+逃离冷却 6s，反射链全部正确仍死——L2 战斗模块是怪群存活的真正解）；深水首轮 DEATH（换气占空比缺陷）修后回归 PASS。**职责边界声明**：反射层职责=突发危险的即时自救（溺水/着火/围攻/卡位）；慢性消耗（饥饿/长期低饱食）不在职责内，进食反射记 backlog（M4.2/M5 候选）
- **寻路（2026-08-20 裁决：Baritone 集成，撤销自研 A* 计划）**：客户端装 Baritone Mod（1.21.1 / NeoForge 构建真机验证通过），brain 经 command 聊天通道发 `#goto x [y] z` / `#stop` 驱动（`#` 前缀被 Baritone 客户端拦截，不达服务器——对服务器零可见）。`sirius_brain/agent/primitives.py` 的 `walk_to` 封装：15s 无进展看门狗只重发一次、发令前界面屏障（quickPlay 加载屏竞态教训）、协作取消≤1s（取消/超时发 #stop 并报当前坐标）。原"brain 侧 A* + world.query 数据 + input.key 驱动"路线**不再实施**；M4 寻路内容收窄为 Baritone 注入路径优化（#goto 每次聊天往返 ~1.3s，collect 提速的下一个空间）。原"API 依赖 vs 完全自主"的顾虑不成立——集成面只有聊天命令，无代码级依赖。**M4.1 补充**：`#pause`/`#resume` 真机可用性实证（Baritone 聊天回执 `Paused`/`Resumed`），成为 cooperative 反射的 Baritone 让路通道；cooperative 反射不撤目标会与水下 GoalBlock 拉锯（M4-rerun 溺亡事故）由此消解
- 异步任务受理即回执 `{task_id, async:true}`，`task_finished/task_timeout` 事件；**替换式受理**（派新活顶掉旧活，旧任务恰好结算一次；唯一拒绝例：同 tick 内刚受理的任务——防模型一轮内双重派单）；**重派=重算**（无恢复簿记，进度已在背包里）；超时报告带进度与"推进中→重派同目标 vs 停滞→改主意"裁决信息。**M3.5 现状注记**：已落地的 walkTo/digBlock/collectBlock 走"同步阻塞 + 协作式取消"（AgentLoop 串行执行工具，60s 行走占 1 个 tool call 位、期间零 VLM 调用；cancel 微步轮询≤1s），M5 分层时再升级异步受理（task/task_finished 协议帧已备好）

### 8.4 从 Numen 吸收的设计修正（保留）

| 原设计 | 修正 | 理由 |
|---|---|---|
| 中断三档 CANCEL/PAUSE/DEFLECT | **取消 PAUSE**，保留 CANCEL/DEFLECT；恢复=重派（带新信息重算，背包收获即进度） | 无检查点簿记，无状态不一致 |
| 固定优先级中断表 | 反射层采用序数固定分层（Numen 现行设计） | 可预测性优先于"最优性"，新增本能零调度代码 |
| 消息消费时机 | 收件箱三态路由（回合中贴边界/任务中立刻开轮/空闲搭车） | 消息时机由大脑状态决定，不由消息类型决定 |
| 视觉优先（截图→VLM） | 空间导航感知用**语义字符网格**（从客户端世界数据生成）；截图用于目标识别/共视觉/证据 | STMR 文献：网格 vs 图像 SR 15.0% vs 1.1% |
| Skill 格式 | 兼容 Numen Skill Markdown 格式（`name/description` frontmatter + 正文） | 社区内容互通 |

### 8.5 与 Numen 的关系：错位共存

- 定位错位：Numen = 服务器里的 NPC/管家（服务端安装、服主运营）；我们 = 陪你进任何服务器的 AI 队友（客户端、私人）
- 它是我们的设计供体（§8.3/8.4）；skill 格式互通
- 扩展轨：大脑层身体无关——未来可通过其 MCP 服务端接管 Numen 身体（第二消费者，验证"大脑不绑死身体"）

### 8.6 参考项目清单

**本机克隆路径不写入本文档**（各开发者机器不同，一律见自己机器的 `local.md`「参考项目」节）。

参考项目价值一览（本机克隆路径见 `local.md`）：

| 项目 | 对 Sirius 的价值 |
|---|---|
| 本项目 | Sirius-Minecraft（sirius-brain + sirius-bridge 单仓） |
| Mindcraft CE（Node，mineflayer） | 遗产代码库：命令系统/conversation/modes 逻辑移植来源；逃生轨身体 |
| Numen（服务端假玩家 AI 同伴） | 设计供体：序数分层调度/收件箱路由/异步任务/重派即恢复/字符网格（`docs/spatial-perception.md`）/寻路（`docs/pathing-refactor-log.md`）/Skill 格式/MCP 服务端安全模型（`docs/mcp-server.md`）；心智模型宪法（`docs/architecture-mind-model.md`） |
| N.E.K.O（桌面 AI 伴侣，Python，Apache-2.0；独立项目，仅设计参考，不做协议兼容） | 设计供体：证据数学（`memory/evidence.py`）/反思层（`memory/reflection/`）/检索重排（`memory/recall.py`）/说话者信任（`memory/speaker_trust.py`）；任务帧结构+截图预算管线（`plugin/plugins/game_agent_minecraft/`）；人设参考 |
| NeoForge 官方文档 | sirius-bridge 开发手册：gettingstarted/networking/按目标版本取用 |

在线参考：

- [Mindcraft / Mindcraft CE](https://github.com/mindcraft-bots/mindcraft) — 原版（LLM+mineflayer 玩家，Kolby Nottingham）；[CE 社区版组织](https://github.com/mindcraft-ce) — 本仓本地副本 mindcraft-ce-develop 的血统来源（本机路径见 local.md）
- [Numen](https://github.com/Dwinovo/minecraft-numen) — 服务端假玩家 AI 同伴（本机路径见 local.md）
- [N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) — 桌面 AI 伴侣；"一边玩一边解说"陪玩形态的先行实现；独立项目仅设计参考（本机路径见 local.md）
- [AI-Player](https://github.com/shasankp000/AI-Player) — Fabric 客户端"第二玩家"，截图喂视觉 LLM 链路验证
- [Voyager](https://github.com/minedojo/voyager) — 分层 agent 与技能库研究鼻祖
- [Baritone](https://github.com/cabaletta/baritone) — 纯客户端寻路可行性证明（不复制源码）

---

## 9. 待决事项

- [ ] 规划器/执行器选型（候选：Claude/GPT 高端款 + DeepSeek/Qwen/本地 Llama）
- [ ] 第一期范围：仅"分层对话+任务下发" vs 含"自驱循环+失败上报"
- [ ] 执行器上下文预算（history 窗口）
- [ ] 规划器世界模型数据源：纯执行器报告 vs 加被动采样
- [ ] 人格一致性：执行器报告的第一人称转译
- [ ] 中断风暴保护：被中断 vs 自发循环的区分策略
- [ ] `!newAction` 受限开放程度（只读 / 任务卡授权写）
- [ ] 执行器并发：v1 串行，留并发口
- [ ] 快车道：`!goToPlayer` 距离上限；快车道命令限时强杀
- [ ] L1 得分权重与预算数值；`requestMemory` 限频阈值
- [ ] L3：数据包解析工具选型；wiki 爬取合规性；置信度演化参数；实验任务触发条件
- [ ] L2 容量上限与遗忘机制
- [ ] 图像记忆：bbox 可靠性校验；多卡同图去重；VLM/非VLM混布注入格式
- [ ] 底层交互：反投影精度；输入节流；GUI 生效判定
- [ ] Skill：轨迹存储格式；body 形态（命令/代码/混合）；转正验证执行者
- [ ] Bridge Mod：NeoForge MDK 选型（目标 MC 版本）；输入注入 API 选型（GLFW 注入 vs 事件模拟）；无头客户端方案（虚拟 GPU/软件渲染）；寻路引擎设计（参考 Baritone/Numen，不复制源码）
- [ ] 反作弊/服务端规则：输入驱动的 bot 在公共服务器的合规边界与风险告知
- [ ] 语义字符网格：从客户端世界数据生成的实现（对齐 Numen `look_around` 输出格式）
- [ ] 反思层：对话式验证的触发与话术设计（何时把假设说出口）；3天自动转正的计时与存档
- [ ] 证据数学参数：REIN/DISP 半衰期天数、promoted/confirmed/archive 阈值、说话者信任初值
- [ ] 主动陪伴：主动开口的触发器清单与频率上限（避免话痨）；现实时间感知的注入方式
- [ ] 人设系统：出厂人设卡套件（几款预置性格）；SillyTavern 角色卡导入的字段映射；旁白短语模板集的分节语法；人设卡 lint 规则清单

---

## 10. 实施路线图

## 10.1 里程碑计划（M0-M9，双轨并行）

开发四原则：薄切片先通再逐层加厚；风险前置（输入注入保真度最先验证）；协议先行（契约冻结后两轨并行）；每步有真人可看的演示。

```
sirius-bridge（身体轨，Java）：M1 眼 → M2 手 ── M3 会师 ─ M3.5 智能原语（dig/平滑转头/感知过滤）──→ M4（仅原语增补）→ …
sirius-brain（大脑轨，Python）：M0 协议冻结（对 mock body 开发）→ M3 会师 ─ M3.5 任务级原语（智能下沉此轨）──→ M4 反射层（智能全在此轨）→ …
```

> 并行轨修正（Python 裁决的代价）：大脑不再借用 mineflayer 身体（纯 JS 库）。对策 = **mock 优先**：M0 的 Python mock body 回放录制的真实协议帧，大脑全逻辑对 mock 开发；如需早期真机联调，可写百行 Node 垫片把 Mindcraft CE 包成 sirius 协议身体（可选，不进主线）。

| 里程碑 | 内容 | 验收标准 | 规模 |
|---|---|---|---|
| **M0 协议冻结+基建** | 双仓库（sirius-bridge / sirius-brain）；协议 schema 定稿（MCP 语义+任务帧（结构吸收自 N.E.K.O）+task_id 回传+五态状态表，pydantic 模型+JSON Schema 双产出）；**Python mock bridge server**（帧回放+可脚本响应）；NeoForge MDK 环境 | 双方对着同一份 schema 开发，mock 跑通 task/事件往返 | S |
| **M1 眼睛** | screenshot/getStats/world.query + localhost/token | Python 客户端连上并截图存盘，整合包客户端画面正确（含 Mod 内容） | S |
| **M2 手** | input.* 四原语 + 事件订阅推送 | **纯脚本重放**"按 E 开背包→拖木头→合成工作台"——证明项目可行性 | M |
| **M3 会师：最小整机** ⭐ | 大脑最简版（**单模型，先不分层**）结构化感知+按需截图→VLM（tool-calling）→工具（NEKO 兼容层已取消，2026-08-19 裁决） | 玩家在游戏聊天打字（如"把石头扔给我"），bot 听到并自主完成，全程闭环 | M |
| **M3.5 智能优化** ⭐（2026-08-20 完成） | 运动控制从 VLM 下沉确定性代码：brain 任务级原语 walkTo/digBlock/collectBlock + Baritone 集成（#goto/#stop）+ bridge dig 智能挖掘（动作层监视按住）/lookAt 平滑转头/world.query filter/input.click hold_ms/entities item 载荷 + 提示契约层（分层引导+滚动状态+预算 500k）；协议 1.0→1.2；VLM 工具表 11→14 | 砍树 22 步 212k 预算耗尽 → 4 步 16k tokens（T5b 直驱）；急停原语中 ≤1s；pytest 263→302 + smoke 241→345 + 真机多项 PASS。**+M3.6 小补丁轮（同日）**：观察纪律防幻觉直答/hello_ack 建模/dig 经验掉落 drops+pickup 工具（表→15）；pytest 311 + smoke 350 | S/M |
| **M4 反射层** ⭐（2026-08-20 完成） | brain 侧反射层落地（`agent/reflexes.py`）：ReflexLevel 等级框架（L0/L1/L2 预留，严格单调能力束，人类-only 聊天切换，instincts 同步点）+ ReflexScheduler（0.5s 轮询/注册序单候选/三档打断 none-cooperative-preempt/边沿自持/先归位再宣布）+ L1 七反射（换气/脱困/撤离/低血/死亡/危怪逃离/说话注视）+ 事后知会（〔本能反应〕flush + 〔紧急〕urgent）；bridge entities 载荷纯增 category/width（协议不动 1.2）。次项 Baritone 注入路径优化未动（系统提示硬约束已由 M3.6 提前销项）；记忆/认知接口按范围红线后置 | 真机零 VLM token：换气（air 217 触发回满）/死亡×3（报坐标+不自动重生）/低血/逃离（末影人 0.47 格→反向 8 格未还击）/等级切换（L0↔L1+L2 拒绝；L0 关动作不关感知）/Baritone 200 格（199 格 77s 终点差 1.2 格零误触发）全部 PASS；着火/脱困环境受限未真机（单测覆盖）；pytest 311→339 + smoke 350→353 | M |
| **M4.1 修复轮**（2026-08-21 完成） | 反射层独立存活 + 自然转头：五缺陷修复（①命令锁下沉 BridgeClient 客户端级 + GUI 确认时序 ②bridge MovementLook 移动转头——Baritone 该构建行走不转头的实证与补位 ③chat.send 直发播报[死亡屏免疫] ④令牌桶"按 tick 计费"证伪无需改码 ⑤#pause/#resume Baritone 让路）+ 压测引出换气占空比修与 safe_command；协议 1.2→1.3 | 分段存活压测（用户修正验收：五场景各 120s+饱和消除饥饿）：火/卡位/综合 PASS、怪群 DEATH（L1 禁攻击设计边界，归因明确）、深水修后回归 PASS——两次死亡全归因；转头 |yaw−运动方向| 中位 0°；10 条命令连发零合并；死亡播报真实进聊天；pytest 339→351 + smoke 353→369 | S/M |
| **M5 分层大脑** | 规划器/执行器分家、任务卡、TaskManager（替换式受理）、双 history；移植 Mindcraft CE 命令/对话 | 挖矿时连续聊天不阻塞；"搞一组铁装备"能分解执行 | M |
| **M6 记忆 L1/L2+玩家记忆** | 活跃层得分、LanceDB、证据数学全套 | 跨会话记得玩家愿望；被纠正的错误不再犯 | M |
| **M7 L3 知识库** | 数据包导入、触发式检索、置信度演化、图像记忆 | 新 Mod 玩 3 小时后能答"紫色方块怎么用"，答案可溯源 | M/L |
| **M8 Skill 沉淀** | 轨迹记录、提炼、验证转正、退化重学；亲手模式 | 第二次同类 Mod 机器操作直接调 skill 快速完成 | L |
| **M9 陪伴感** | 主动陪伴触发器（nudge 循环，抄 NEKO 参数）、现实时间、人格演化、语音（直连 TTS，M9 前再定方案） | 深夜主动唠叨；boss 出现主动惊叹 | S/M |

**阶段递进逻辑**：M0-M3 证明它能活 → M3.5 证明它"能用"（复杂任务在预算内跑完）→ M4-M8 证明它好用 → M9 证明它像人。

### 关键决策点（到时再定）

| 决策点 | 时点 | 内容 |
|---|---|---|
| ~~Baritone 依赖 vs 自研寻路~~ | ~~M4 前~~ | **已决（2026-08-20，M3.5）：Baritone 集成**——客户端 Mod 安装，brain 经聊天命令 `#goto`/`#stop` 驱动（无 API 依赖，`#` 前缀客户端拦截不达服务器），真机冒烟 3s 收敛 2.0 格；M4 相应收窄（见里程碑表） |
| 执行器①是否引入 Numen 式确定性任务 | M5 前 | 挖矿/战斗等高频任务硬化成代码，小模型只做长尾 |

### 贯穿全程的工程纪律

- **测试不花 LLM 钱**：协议帧录制/重放、VLM mock、确定性验收脚本（M2 重放脚本是第一个）
- **每里程碑一个 demo 视频**（最终可剪成宣传片）
- **协议版本号每里程碑递增**：sirius-bridge 与 sirius-brain 独立发版的前提
- **大脑轨全程对 mock body 开发**（Python，回放真实协议帧），M3 切换真身体——"大脑不绑死身体"的第一次实战；需真机联调时可加 Node 垫片（可选）

### 开发协作模式：主管模式（supervisor mode）

主会话（规划器）不亲自写代码，负责拆解、派发、验收；实现工作全部由子代理（执行器）完成。与项目自身的分层架构同构：

- **任务简报 = 任务卡**：目标 / 验收标准 / 约束 / 相关文件路径；简报必须**自包含**（子代理无会话历史）
- **派发**：实现类 → general-purpose 代理；调研/搜索类 → Explore 代理（要结论不要转储）
- **验收**：看 diff、跑测试、对照验收标准；不合格打回并附原因
- **上下文纪律**：主会话只读 diff 与验收产物，不全文读大文件；设计文档是唯一跨会话持久记忆（对抗上下文压缩），每轮决策后同步更新
- **跟踪**：用 session todo 列表跟踪里程碑内任务状态

## 10.2 扩展轨

大脑层保持身体无关：可接管 mineflayer（Mindcraft CE）或 Numen（其 MCP 服务端）作为备选身体——身体不绑死大脑（NEKO 侧兼容已取消，见 §8.2 裁决）。
