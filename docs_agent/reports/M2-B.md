# M2-B 工作报告

- 任务：sirius-bridge 事件订阅推送 + 截图流（events.subscribe 工具、notification 帧推送、chat/gui/危险状态事件源、~100KB 节流截图流）
- 日期：2026-08-18
- 状态：完成（代码全部落地、build + smokeTest 175/175 全绿；真机行为待主管验收，清单见下）
- 验收：`gradlew build` BUILD SUCCESSFUL；smokeTest **175/175**（M1-C 45 + M2-A 66 + M2-A2 8 + M2-B 新增 56，全部既有检查未动一行）；Python 侧零改动（协议 1.0 冻结，BridgeClient/mock 本就支持 notification 分发）；未部署（主管 deploy.cmd）、未提交（主管审后提交）

## 交付物

- `sirius-bridge/src/main/java/io/sirius/bridge/EventsContracts.java`（新增，纯逻辑 315 行）：EventLevel 枚举（CRITICAL>WARNING>INFO + `atLeast` 排序）、Subscription 匹配（精确/`"*"`/空集=全部 × 最低级别过滤）、events.subscribe 参数校验（照冻结 schema，违规 → -32602）与结果组装、notification 帧组装（level 注入 data 副本、timestamp=epoch 秒 float、seq）、`StreamThrottle<T>` 节流状态机（N.E.K.O 语义：窗口开=立即推+撤销已武装的延迟冲刷；窗口关=最新帧替换唯一 pending 槽+只武装一次边界冲刷）、环形缓冲辅助、流参数常量（6s/1Hz/1024/q80/100KB/环形3）
- `sirius-bridge/src/main/java/io/sirius/bridge/EventPusher.java`（新增，MC 薄壳 335 行）：唯一推送咽喉点 `push(type, level, data)`——按会话订阅过滤、每连接原子 seq（从 0 起）、sendFrame 线程安全投递、连接已关时 `EVENT_DROP` 审计+计数（诚实丢弃记账，从不抛异常）；事件源：chat（ClientChatReceivedEvent）、gui_open/gui_close（ScreenEvent.Opening/Closing）、危险状态采样（每 20 tick：death/fire/health_low≤6.0/drown=水下且 air<300，边沿触发+每类 5s 冷却抑制抖动）；截图流：每 20 tick 且有订阅者才抓帧（复用 PerceptionTools.grabScreen，渲染线程只做像素下载），编码+节流+边界冲刷全在 `sirius-bridge-events` daemon 单线程上（100KB 预算阶梯+环形缓冲3）；`shutdown()` 关线程+清缓冲+审计 pushed/dropped 总账
- `ImageOps.java`（+121 行）：新增 4 参 `encodeWithinBudget(image, quality, maxBase64Length, maxLongestEdge)` —— N.E.K.O 式阶梯：质量 `[q0,65,50,40,30]`（不超过 q0）× 边长 `[e0, e0/2, e0/4]`，质量外层/边长内层，首个 base64 长度达标组合返回；全部不达标则**发货最小尝试**（绝不丢帧）；`Encoded` record 扩展 `width/height` 字段（报告实际编码尺寸，流载荷需要）；`streamQualityLadder`/`streamEdgeLadder` 纯函数；**2 参重载逐字节保持原行为**（仅补记 width/height，jpeg/quality/downscaled 语义不变——4K 噪声 2MB 预算的既有检查原样通过）
- `BridgeServer.java`（+70/-12）：ClientSession 加 `volatile Subscription`（null=未订阅=不推）+ `AtomicLong eventSeq`（首帧 seq=0）；构造器注册 `events.subscribe`（严格校验 types 数组/字符串、min_level 枚举或 null，违规 -32602；SUBSCRIBE 审计行；结果回显生效订阅+note）；`sessionsView()`/`eventPusher()` 包私有访问器；shutdown() 在 InputTools.shutdown() 后调 `eventPusher.shutdown()`
- `ToolContext.java`（+9）：包私有 `connection()` 访问器（仅 bridge 内部需要按连接寻址的工具用——events.subscribe 写会话订阅）
- `SiriusBridge.java`（+27/-6）：`start()` 里 server 创建后 `registerEventListeners()`——chat/screen 三监听挂 NeoForge.EVENT_BUS 委托给 EventPusher；tick 采样器复用既有 onClientTick（server 非空分支调 `eventPusher().onClientTick()`，启动一次性逻辑原样保留）
- `SmokeMain.java`（+243 行）：+56 项冒烟（订阅匹配 5、参数校验 11、结果组装 2、notification 组装 8、节流状态机 14、阶梯参数化 8、100KB 预算编码 5、环形缓冲 1、2MB 重载维度回归 1、时间戳单位换算 1），总 **175/175**
- `sirius-bridge/README.md`：新增 "The event push channel (M2-B)" 章节（帧形状/timestamp 秒制说明、订阅语义、事件目录表、截图流参数、线程模型、诚实丢弃策略）；"Currently implemented frames" 表补 events.subscribe 行并注明 notification 为 outbound-only；冒烟计数 119→175；项目布局/下一步更新

## 关键决策与理由

1. **timestamp 用 epoch 秒（float）而非毫秒**：对照 `sirius_brain/protocol/frames.py` 的 `NotificationFrame.timestamp: float` 与 `mock/server.py push_notification` 的 `timestamp=time.time()`（秒）确认单位——`EventsContracts.timestampNowSeconds(ms) = ms/1000.0`。这是 Python 侧**不验证单位**但**验证类型**（float）的字段，毫秒会被静默当成秒（1970 年附近的时间戳），必须从源头对齐。
2. **seq 只在"会投递"时消耗**：Python BridgeClient 对 seq 退步只告警不致命，但我们仍按"每个匹配订阅的会话恰好一次 getAndIncrement"分配——不订阅/不匹配的会话不消耗 seq，断线重连后 seq 空间自然重置（每连接独立计数器，与 mock 的 `_ClientState.seq` 语义一致）。
3. **节流状态机做成纯类 `StreamThrottle<T>`（时钟注入）而非散在 EventPusher 里**：N.E.K.O service.py:1037-1079 的语义有三条不变量（窗口开→立即推+撤销武装冲刷+清 pending；窗口关→最新帧替换唯一槽；只武装一次边界冲刷），全部可确定性验证（14 项冒烟覆盖"边界前不冲刷/边界恰好冲刷/立即推送撤销陈旧冲刷"）。泛型 T 让 pending 槽存"已组装好的 data 载荷"，编码后才进节流——与 N.E.K.O 对 encoded bytes 节流一致，且 render 线程零编码成本。
4. **阶梯循环序：质量外层、边长内层（按简报）而非 N.E.K.O 的边长外层**：简报明写 "for quality q, for edge e"。功能等价（都找首个达标组合），质量优先保持配置画质更久（先缩分辨率再降质量）。记入偏离说明。
5. **100KB 预算算在 base64 长度上（而非 N.E.K.O 的原始 JPEG 字节）**：我们的 notification 帧只带 base64 一份（无 N.E.K.O message_plane 的原始副本 2.3× 膨胀），按 base64 计直接约束线上消息 <100KB——比按原始字节计更严格且更贴合"消息上限"本意。简报参数名 `maxBase64Bytes` 亦为此解。
6. **`Encoded` record 扩展 `width/height`（而非简报建议的 `edge`）**：流载荷 data 要回 `width/height`（对齐 screenshot RPC 响应字段名），阶梯在 ImageOps 内部缩放，调用方看不到中间图——record 直接报告实际编码尺寸最诚实。2 参重载同步补记（jpeg/quality/downscaled 输出不变，既有真机验证结论不受影响）。
7. **2 参重载不委托 4 参**：两者阶梯不同（2 参：质量 -10 降到 40→缩 1024 重来；4 参：N.E.K.O 交错网格+发货最小），委托会改变真机已验证行为。两实现并存，javadoc 互相注明。
8. **危险状态边沿触发+每类 5s 冷却**：火苗闪灭、血量在 6.0 附近振荡是真实游戏里的常态，纯边沿会刷屏；纯冷却会丢边沿。组合：false→true 跳变且距上次触发 ≥5s 才推。死亡期间压制 fire/health_low/drown（death 是更强信号）；离开世界清边沿（重进世界可再触发）。
9. **截图流只在"有订阅者"时抓帧**：`hasSubscriber("screenshot", INFO)` 每 20 tick 查一次（并发 map 遍历，开销可忽略）；无订阅者时 1Hz 的 framebuffer 下载（10-30ms）也不付。订阅是严格 opt-in。
10. **监听注册放 SiriusBridge、事件处理放 EventPusher**：与 M1-B 以来的生命周期归属一致（mod 入口管 NeoForge 总线与启动时序，WS 服务端管连接，EventPusher 纯推送语义）；tick 采样器直接搭既有 onClientTick 的"已启动"分支，不新增监听。

## 实现要点 / API 笔记（M3 必读）

**1.21.1/NeoForge 21.1.248 签名（全部对照 gradle 缓存反编译源逐条核实）：**

1. `ClientChatReceivedEvent`（NeoForge.EVENT_BUS，客户端）：`getMessage()`→Component（`.getString()` 取纯文本，含样式翻译后的结果）；`getSender()`→UUID（系统消息=Util.NIL_UUID）；`isSystem()` 即 NIL 判定。子类 `.Player`/`.System`——**监听基类两者都收**（EventBus 沿类层级分发）。可取消（我们不动）。
2. `ScreenEvent.Opening`（cancellable）：`getCurrentScreen()`/`getNewScreen()` 均 @Nullable。**只在 new screen 非 null 时触发**（Minecraft.setScreen :1037-1040 源码核实）；setScreen(null) 走 `ScreenEvent.Closing`。
3. `ScreenEvent.Closing`：`getScreen()`（基类 ScreenEvent :60）。**触发条件是 old!=null && new!=old**（:1043-1044）——即"换屏"也会发 Closing；gui_close 语义=被替换或关闭，README 已注明。
4. 危险采样读数：`LocalPlayer.isDeadOrDying()`（LivingEntity:1109）、`getHealth()`（LivingEntity:1101 float）、`isOnFire()`（Entity:2222）、`isUnderWater()`（LocalPlayer:1101）、`getAirSupply()`（**Entity:2362**，非 LivingEntity——简报猜测的方法名全对，只有归属类一处）。原版最大 air=300。
5. `Component.getString()` 对含样式组件返回拼接文本——聊天行直接可读。

**线程模型**：chat/screen/tick 源在客户端主（渲染）线程；截图编码/节流/边界冲刷在 `sirius-bridge-events`（daemon 单线程，编码与冲刷天然串行化，无节流竞态）；`push()` 全线程安全（sessions 并发 map、subscription volatile、seq 原子、sendFrame 本就跨线程安全）。渲染线程只付 framebuffer 下载（复用 PerceptionTools.grabScreen，tick 回调时渲染目标里是上一完整帧——与 Minecraft.execute 任务在帧首 runAllTasks 执行的保证等价）。

**坑**：`ClientSession.subscription` 是字段不是方法（ BridgeServer/EventPusher 直接字段访问，包私有）；`expectInvalid` 冒烟助手捕获的是 `ToolContracts.InvalidParams`——EventsContracts 复用该异常类保持 -32602 映射路径一致。Gson 序列化 double timestamp 产出如 `1.7554704E9` 科学计数——Python `json.loads`+pydantic float 正常解析（mock 同样会发 time.time() 的大数）。

**魔法数字**（全部常量化并写 README）：6s 最小推流间隔、1Hz 采样（20 tick）、1024/q80 起始、100KB base64 硬预算、环形 3、危险采样 20 tick、每类冷却 5s、health_low 阈值 6.0、drown 阈值 air<300。

## 验证方式

- `gradlew build`（含 check→smokeTest）BUILD SUCCESSFUL，**175/175**：
  - 新增 56 项分布：订阅匹配 5（精确/`"*"`/空集=全部/CRITICAL 下限/默认 INFO）、参数校验 11（缺 types/非数组/非字符串项/null types/非法 min_level×2/重复去重/通配+CRITICAL/空数组/显式 null）、结果组装 2、notification 组装 8（type/event/level 注入/原 data 不被改（深拷贝）/已有 level 不覆盖/null data 只剩 level/timestamp 是 float 数/seq 是整数/ms→秒换算 1724000000123→1724000000.123）
  - 节流 14（首帧立即/窗口内 DEFER/边界前不冲刷/最新帧替换/边界恰好冲刷且推的是最新帧/冲刷后窗口立即又关/恰好 min-interval 后重开/武装时刻=窗口开点/墙钟跳变重开/立即推撤销冲刷+清槽/无陈旧冲刷/零间隔不 DEFER）
  - 阶梯 8+5（质量表 80/100/65/30/20、边长表 1024/100/0 禁缩、800×600 噪声 100KB 预算内 36944 b64 chars 且 q80 256×192、微型图首档不缩、不可达预算 500 chars 发货最小尝试 q30 且不丢帧、Encoded 尺寸报告）+ 环形 1 + 2MB 重载维度回归 1
- 既有 119 项检查一行未改、全绿（2MB 预算 4K 噪声路径仍是原 ladder 行为）
- 全部 MC/NeoForge API 调用（事件类/访问器/触发点）对照反编译源写出，无一处凭记忆
- 真机行为按约束未自测（不启动客户端），风险面收敛进下述清单

## 待真机验证清单（主管验收用）

前置：Python 侧用 `BridgeClient`（零改动即可用）连上、`hello` 握手、`add_event_handler` 注册后 `subscribe_events(["*"], None)`。审计日志应出现 `SUBSCRIBE` 行。

1. **chat 事件**：游戏内打字发一条消息（或 `/say hello`）→ 客户端收到 `event="chat"` notification，data.message 正确、system 字段区分玩家/系统行；不订阅的连接收不到任何 notification
2. **gui_open/gui_close**：`input.key {"code":"E"}` 开背包 → WARNING `gui_open` data.screen="InventoryScreen"；再按 E 关闭 → `gui_close`。注意换屏（背包→合成台）也会发 gui_close（vanilla Closing 语义）
3. **截图流节奏**：订阅含 `screenshot` 类型 → 每 ≥6s 一帧 `event="screenshot"`，data.image_b64 解码为合法 JPEG、**整条 notification 消息文本 ≤100KB**、width/height ≤1024、seq 单调；连续观察 3+ 帧确认 latest-wins（帧间内容应为最新画面，无陈旧帧补发）；min_level="CRITICAL" 的订阅者应收不到 screenshot（INFO 被过滤）
4. **seq 单调性**：Python 客户端日志无 "事件 seq 乱序" 告警；每连接独立（可开两个连接对比各自 seq 从 0 起）
5. **CRITICAL 危险状态**（最难自然触发，用命令构造）：
   - fire：站在岩浆边或 `/effect give @s minecraft:instant_damage` 打到着火，或直接 `/setblock` 岩浆——收到 `event="fire"` data.health；火灭后 5s 内再着火**不**重复推（冷却），5s 后再着火会推（边沿重触发）
   - health_low：`/effect give @s minecraft:poison 10 1` 或跳崖摔——血 ≤6.0 时 `health_low` {health, threshold:6.0}
   - drown：创造飞行贴水面下潜——`drown` {air}（air<300 且水下即触发，头一浸水就会推，属预期）
   - death：`/kill`——`death` {health:0, air, on_fire}；死亡期间 fire/health_low/drown 被压制；重生后状态清零
6. **不订阅=零推送**：只 hello 不 subscribe 的连接，打字/开背包/落水均无 notification（capabilities/list 等请求响应正常）
7. **重订阅替换**：先 `types:["chat"]` 再 `types:["screenshot"]` → 之后只收 screenshot；seq 连续不重置（每连接计数器与订阅无关）
8. **-32602**：`types: "chat"`（非数组）→ invalid params；`min_level: "LOUD"` → invalid params
9. **性能直觉**：订阅截图流时 F3 帧率无可感知下降（渲染线程只付 ~10-30ms/s 的像素下载）；无订阅者时与 M2-A 行为一致
10. **关停**：退出游戏 → 审计日志 `EVENTS_STOP pushed=.. dropped=..` 行出现，无卡死/异常栈

## 偏离说明

1. **阶梯循环序**：简报明文 "for quality q, for edge e"（质量外层）；N.E.K.O 原实现是边长外层。按简报实现（功能等价，画质保持更久），已在 ImageOps javadoc 注明来源与差异。
2. **Encoded record 扩展字段**：简报建议加 `edge` 字段；实际加了 `width`+`height`（流载荷需要二维尺寸，edge 是其 max，信息严格包含）。2 参重载仅补记尺寸，编码行为逐字节不变。
3. **简报行号核对**：简报称 `getAirSupply()` 等 LocalPlayer 访问器——实际 `getAirSupply()` 声明在 `Entity`（:2362），`isOnFire()` 同在 Entity（:2222）；调用面无差（LocalPlayer 继承）。其余行号（ClientChatReceivedEvent.getMessage :48 等）与源码一致。
4. **危险状态字段微调**：简报的 "drown=underwater AND air<300" 照实现；另加了两处简报未明说的抑制：死亡期间压制 fire/health_low/drown（避免一次 /kill 推四条 CRITICAL）、health_low 仅在非死亡时判定（死亡另有 death 事件带完整现场）。语义更干净，已在 README 事件表注明。
5. **冒烟规模**：目标 ~+30，实际 +56（节流不变量与参数校验分支展开得比较细）；总数 175。

## 交接须知

- 下一步扩展点：新事件源=在 EventPusher 加一个源方法调 `push(type, level, data)`（唯一咽喉点，线程安全）；`events.watch`（M3）可在 EventPusher 旁挂独立的 watch 评估器（同样的 per-session 状态模式）；重连补帧=消费 `streamRing`（已存最近 3 帧编码载荷）；危险目录扩展（饥饿/被攻击/天气）只需在 `sampleDanger` 加槽位
- 已知限制：
  - gui_close 在"换屏"时也触发（vanilla Closing 语义 old!=null && new!=old），大脑侧如需精确"回到游戏"应以 gui_open(null 过渡后无新 open)或 getStats 判定
  - 截图流在窗口最小化时停止采样（渲染循环停转 → tick 停 → 自然停），恢复即续，无超时保护需要（与 RPC 路径的 -32603 语义不同）
  - ring buffer 只进不出（消费=未来重连补帧，当前仅 latest 经节流槽推出）
  - chat 的 message 是 `Component.getString()`：含样式组件的行（点击事件等）只回纯文本，样式元数据不推
  - 事件推送无 QoS：连接半开时 sendFrame 吞异常（debug 日志），由 seq 单调性+大脑侧重连兜底
- 关联报告：M1-B（会话/分发骨架）、M1-C（grabScreen/截图管线拆分）、M2-A（InputGuard/调度线程模式）、sirius-technical.md §8.2（事件分级/截图流预算管线/推送帧格式）
