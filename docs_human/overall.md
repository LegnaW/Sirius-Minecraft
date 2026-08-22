# Sirius 全局技术文档（overall）

> 写给第一次接触本项目的**人**：从一段话看懂全貌，往下逐层加深，直到贴着源码的细节设计。
> 所有代码片段逐字摘自仓库真实文件并标注 `文件:行号`（截至 2026-08-21，M4.1 完成态；§4.1-4.7 代码摘录时点较早，行号以标注为准）。
> agent 侧的权威规格在 [`docs_agent/`](../docs_agent/)（本文是它的可读重述，冲突时以权威为准）；设计愿景（纯思路）见同目录 [sirius-design.md](./sirius-design.md)。

## 0. 一段话

Sirius 是一个 Minecraft AI 陪玩项目：让 AI 拥有**一个真正的 Minecraft 客户端**当身体（不是协议模拟器，也不是服务端假人），后端 Python 大脑通过 WebSocket 指挥这具身体——看它看的画面、替它动鼠标键盘（M2 起），再到"走到/挖掉/收集 N 个"一次调用自动完成的任务级原语（M3.5 起，运动控制从 VLM 下沉确定性代码），以及溺水自动上浮、危怪转身就跑、低血自动喊停这类保命动作的无 LLM 反射层（M4 起，0.5 秒内完成、零 token；M4.1 起通过分段存活压测——零上层指令时突发危险的自救独立成立，移动会像人一样转头看向前进方向）。目标是"陪你进任何服务器玩的 AI 队友"：它看得到你的皮肤和 mod boss 的特效，对服务器来说就是个普通玩家。大脑侧是分层架构（大模型规划器 + 小模型执行器 + 无 LLM 反射层），记忆/技能/人格系统让它越玩越熟练、越处越懂你。

## 1. 全景图

```
┌─────────────────────────┐         ┌──────────────────────────────────┐
│  sirius-brain (Python)  │         │  真实 Minecraft 客户端 (HMCL)     │
│                         │  WS ══▶ │  ┌────────────────────────────┐  │
│  BridgeClient           │ JSON    │  │ sirius-bridge (NeoForge Mod)│  │
│  ├── protocol/ 帧模型    │ 127.0.0.1│  │  眼：screenshot/getStats/   │  │
│  ├── mock/ 假身体        │ :8765   │  │      world.query            │  │
│  └── (M3+) 规划器/执行器  │         │  │  手：(M2) input.* 注入       │  │
│                         │         │  └────────────────────────────┘  │
└─────────────────────────┘         └──────────────────────────────────┘
```

两侧唯一的耦合点是**协议**（M0 冻结 v1.0，M4.1 演进至 v1.3，始终向后兼容）：MCP 语义的请求-响应 + 事件推送，外加任务帧（结构吸收自 N.E.K.O，非兼容承诺）。大脑对 mock 假身体开发全部逻辑（M0-M3），M3 换真身体零改动——"大脑不绑死身体"已实战验证（同一 BridgeClient 连 mock 与真 Mod）。

## 2. 三条调用栈旅程

### 2.1 一次工具调用的旅程（大脑 → 身体 → 大脑）

```
BridgeClient.call("getStats")
  → ToolCallRequest{type:"request", id:<uuid4>, method:"getStats"}     client.py:241
  → WebSocket → BridgeServer.onMessage                                  BridgeServer.java:180
  → 已认证? → type=="request" → ToolRegistry.find("getStats")           BridgeServer.java:265
  → PerceptionTools.getStats → callOnMainThread（主线程 latch 等待）       PerceptionTools.java:254
  → ToolContracts.statsResult 组装响应（纯逻辑）                          ToolContracts.java:180
  → ToolCallResponse{type:"response", id:<同一个>, result:{...}} → 大脑
  → client.py 按 id 配对，await 的 call() 返回 result                    client.py:267
```

连接建立的前置：首帧必须是 `hello`（token 握手，BridgeServer.java:228），之后通常先 `capabilities/list` 协商能力（M4.1 后 14 项含 chat.send，协议版本 1.3）。

### 2.2 一次任务帧的旅程（fire-and-forget）

```
BridgeClient.send_task("挖一组铁矿")
  → TaskFrame{type:"task", task:"挖一组铁矿", task_id:"T-42"}            frames.py:73
  → （不等待，立即返回 task_id）
  → Mod 干活（M3 前是占位：立即回 interrupted）
  → TaskFinishedFrame{type:"task_finished", status:<五态>, task_id:"T-42", text}
  → 大脑 on_task_finished 回调（按 task_id 归属，乱序完成也不出错）        frames.py:81
```

五态：`ok / failed / interrupted / superseded / timeout`。铁律两条：**task_id 原样回传**（out-of-order 完成时按完成序匹配会张冠李戴——N.E.K.O 踩过的坑）；**失败绝不能报成 ok**（上层人格会把翻车当成功叙述）。

### 2.3 一次事件推送的旅程（身体 → 大脑，M2 完整实现）

```
Mod 侧事件源（着火/聊天/GUI 变化…）
  → NotificationFrame{type:"notification", event:"fire", data:{level:"CRITICAL",...},
                      timestamp, seq:<每连接从 0 单调递增>}               frames.py:36
  → BridgeClient 接收循环按 event 名分发到注册的 handler；seq 乱序仅告警
```

事件分级 CRITICAL/WARNING/INFO 对应反射层立即处理 / 排队 / 缓冲。

## 3. 子项目结构导览

### sirius-brain（Python，大脑）

| 包 | 干什么 |
|---|---|
| `sirius_brain/protocol/` | 协议权威：pydantic 帧模型（frames/tools/tasks/enums）+ schema 导出 CLI |
| `sirius_brain/mock/` | 假身体：剧本驱动的 WS 服务 + JSONL 帧回放 + FakeWorldBridge 可变世界（M3.5，假 Baritone/可挖方块/掉落物吸附——原语全链路离线可测） |
| `sirius_brain/bridge/` | BridgeClient：连接真/mock 身体的统一入口（重连监督/RPC 配对/事件分发） |
| `sirius_brain/agent/` | M3 大脑循环（loop/tools/vlm/config）+ M3.5 任务级原语（primitives.py：walkTo/digBlock/collectBlock/pickup）+ M4 反射层（reflexes.py：等级框架/调度器/七条脊髓反射） |
| `schema/` | 冻结产物：28 个自包含 JSON Schema（Java 侧消费，构建期单向同步进 jar） |
| `tests/` | 真实 WebSocket 回环（不 mock websockets 库本身），M4.1 后 351 项 |

### sirius-bridge（Java/NeoForge，眼与手）

| 类 | 干什么 |
|---|---|
| `SiriusBridge.java` | Mod 入口：首个无 overlay 的 tick 启动服务，关机时停 |
| `BridgeServer.java` | WS 服务端：hello/token 状态机、帧分发、审计 |
| `BridgeConfig.java` | `config/sirius_bridge.toml`（端口 + 首启随机生成的 token） |
| `AuditLog.java` | `logs/sirius_bridge.log`：每个安全/协议事件一行 |
| `Capabilities.java` | 从 jar 内 schema 资源组装能力清单（永不手写两份） |
| `ToolRegistry.java` | method → handler 注册表（加工具不动分发器） |
| `ToolContext.java` | 每次调用的上下文：主线程编排 + 线程安全回帧 |
| `PerceptionTools.java` | M1-C 三工具的 MC 薄壳（渲染线程抓屏/主线程读状态） |
| `ToolContracts.java` | 纯逻辑：参数校验/响应组装/世界扫描（不 import 任何 MC 类） |
| `ImageOps.java` | 纯逻辑：裁剪/JPEG/预算降级阶梯（纯 JDK/AWT；M2-B 加 100KB 推流档） |
| `InputTools.java` + `KeyCodes` + `TokenBucket` + `InputContracts` | M2-A 四输入原语：GLFW 事件层注入（反射直达私有回调）、限频、GUI 点击留证 |
| `InputGuard.java` | 输入总闸：开关/令牌桶/权限四级（M2-D） |
| `EventPusher.java` + `EventsContracts.java` | M2-B 事件推送：单一事件入口 + 截图流（节流/预算/环形缓冲） |
| `GuiTools.java` + `GuiContracts.java` | M2-C getGuiState：widget 树 + 容器格子角色分类 |
| `LookTools.java` + `LookContracts` + `PermissionContracts` | M2-D 视角控制（vanilla lookAt 原式）+ 权限判定；M3.5 加 turn_speed_deg_s 平滑转头 |
| `TurnController.java` | M3.5 tick 驱动平滑转头状态机（固定角速度两轴推进、新 turn 替换旧） |
| `DigTools.java` + `DigContracts.java` | M3.5 dig 智能挖掘原语：纯状态机监视器（Contracts）+ 动作层按住壳（Tools，焦点免疫） |
| `MovementLook.java` | M4.1 移动转头：每 tick 按速度矢量方向 300°/s 只写 yaw（显式转头任务优先让位、2° 死区）——Baritone 行走不转头的补位 |
| `ChatTools.java` + `ChatContracts.java` | M4.1 chat.send 直发聊天：进程内 `ClientPacketListener.sendChat`（聊天屏 ENTER 同款入口，任何 GUI 包括死亡屏都屏蔽不了） |
| `Json.java` | 帧构造 + JSON-RPC 风格错误码 |

## 4. 细节设计（思路 + 真实代码）

### 4.1 协议双产出与防漂移

**思路**：协议只有一份权威定义（Python pydantic 模型），Java 侧只消费导出的 JSON Schema，永不手写两份。防漂移靠两道闸：pytest 对比仓库 schema 与代码重导出（忘导出→红）；gradle 构建期单向同步进 jar（忘同步→产物过期）。

NEKO 兼容帧直接作为一等帧类型纳入（与 N.E.K.O 的 task/task_finished 字段同构）：

```python
# sirius-brain/sirius_brain/protocol/frames.py:73-87
class TaskFrame(BaseModel):
    """NEKO 兼容：后端 → Mod 任务帧 {type:"task", task, task_id}。spec §8.2。"""

    type: Literal["task"] = "task"
    task: str
    task_id: str


class TaskFinishedFrame(BaseModel):
    """NEKO 兼容：Mod → 后端任务完成帧。task_id 必须原样回传（否则 out-of-order 完成会错误归属）。spec §8.2。"""

    type: Literal["task_finished"] = "task_finished"
    status: TaskFinishedStatus
    task_id: str
    text: str
```

### 4.2 安全模型：合法远控的四道闸

**思路**：Bridge Mod 本质是"合法远控"，安全必须内建而非后补。四道闸：仅绑定 loopback（`127.0.0.1`，网络上不可达）；token 握手（首帧强制 hello，错 token / 先发别的帧 / 10 秒沉默 → close 1008）；token 常数时间比较（防时序侧信道）；全事件审计日志。

```java
// sirius-bridge/src/main/java/io/sirius/bridge/BridgeServer.java:324-329
    /** Constant-time token comparison. */
    private boolean tokenMatches(String candidate) {
        return MessageDigest.isEqual(
                config.token.getBytes(StandardCharsets.UTF_8),
                candidate.getBytes(StandardCharsets.UTF_8));
    }
```

认证转移与看门狗的竞态用 per-connection 同步消除——认证恰好只发生一次，晚到的看门狗被取消：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/BridgeServer.java:74-99（M2-B 加了订阅与 seq）
    static final class ClientSession {
        volatile boolean authenticated;
        volatile ScheduledFuture<?> helloDeadline;
        /** Event subscription; null = unsubscribed (default: no pushes at all). */
        volatile EventsContracts.Subscription subscription;
        /** Per-connection notification counter; first delivered frame gets 0. */
        private final AtomicLong eventSeq = new AtomicLong();

        /** Auth transition is guarded so the watchdog cannot race the hello. */
        synchronized boolean authenticate() {
            if (authenticated) {
                return false;
            }
            authenticated = true;
            ScheduledFuture<?> deadline = helloDeadline;
            if (deadline != null) {
                deadline.cancel(false);
                helloDeadline = null;
            }
            return true;
        }

        /** Sets/changes the event subscription (volatile write, any thread). */
        void setSubscription(EventsContracts.Subscription subscription) {
            this.subscription = subscription;
        }
```

### 4.3 线程模型：WS 线程与游戏主线程的楚河汉界

**思路**：WebSocket 回调跑在库自己的线程上，而 Minecraft 的游戏状态只允许主（渲染）线程碰。规则一句话：**WS 线程做解析/校验/分发，碰游戏状态必须 `Minecraft.execute` 排进主线程**；回帧从任何线程都安全。

```java
// sirius-bridge/src/main/java/io/sirius/bridge/ToolContext.java:40-43
    /** Queues work on the client main (render) thread - mandatory for game state. */
    public void onMainThread(Runnable action) {
        Minecraft.getInstance().execute(action);
    }
```

工具需要同步拿到主线程结果时，用 latch 等待——**必须带超时**：窗口最小化时渲染循环停止排空任务队列，任务会活活饿死，无限等待等于挂死连接：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/PerceptionTools.java:259-273（省略失败重抛尾部；M2-C 起包私有共享）
    static <T> T callOnMainThread(ToolContext ctx, Supplier<T> supplier) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        Object[] box = new Object[2]; // [0] result, [1] failure
        ctx.onMainThread(() -> {
            try {
                box[0] = supplier.get();
            } catch (Throwable t) {
                box[1] = t;
            } finally {
                done.countDown();
            }
        });
        if (!done.await(MAIN_THREAD_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
            throw new IllegalStateException("client main thread did not run the task within "
                    + MAIN_THREAD_TIMEOUT_SECONDS + "s (game iconified or shutting down?)");
        }
        // ...失败重抛、成功返回 box[0]
```

### 4.4 截图管线：它亲眼所见，含 GUI

**思路**：1.21.1 把世界、手、HUD/打开的 GUI 全画进主渲染目标，所以对主 framebuffer 截图就是"玩家屏上所见"，标题屏也有效。渲染线程只做像素下载（~10-30ms），裁剪/JPEG/base64 全放 WS 线程——渲染线程零阻塞。

```java
// sirius-bridge/src/main/java/io/sirius/bridge/PerceptionTools.java:110-118（M2-A 起包私有：留证复用）
    static BufferedImage grabScreen() {
        RenderTarget target = Minecraft.getInstance().getMainRenderTarget();
        NativeImage shot = Screenshot.takeScreenshot(target); // world + hand + GUI, as on screen
        try {
            return toBufferedImage(shot);
        } finally {
            shot.close(); // native memory - always free it
        }
    }
```

像素格式有个坑：`NativeImage` 是小端 ABGR，要换红蓝字节道才成标准 ARGB（`PerceptionTools.java:120-134`）。

编码侧是**预算阶梯**：base64 超 2MB 就质量 -10 一路降到 40，还超就缩到长边 1024px 再走一遍；病态图超预算也照发不失败（安全阀）：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/ImageOps.java:140-156
    public static Encoded encodeWithinBudget(BufferedImage image, int quality) throws IOException {
        BufferedImage current = image;
        boolean scaled = false;
        Encoded last = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            for (int q : qualityLadder(quality)) {
                byte[] jpeg = encodeJpeg(current, q);
                last = new Encoded(jpeg, q, scaled, current.getWidth(), current.getHeight());
                if (base64Length(jpeg) <= MAX_BASE64_LENGTH) {
                    return last;
                }
            }
            current = scaleLongestEdge(current, DOWNSCALE_LONGEST_EDGE);
            scaled = true;
        }
        return last; // safety valve: over budget but delivered
    }
```

真机实测：854x480 标题屏 q80 直出 72KB；4K 不可压缩噪声图降级后 430KB / 1024x576。

### 4.5 纯逻辑与薄壳分离：不起游戏也能测

**思路**：`ToolContracts`/`ImageOps` 不 import 任何 Minecraft 类（参数校验/响应组装/扫描逻辑/图像处理全在纯 JDK 层），`PerceptionTools` 只留碰 MC API 的薄壳。因此 45 项冒烟检查不起游戏就能跑（`gradlew smokeTest`，挂进 build）。加新工具 = 注册一个 handler，分发器零改动：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/BridgeServer.java:116-125（M2 全家注册，分发器不动）
        // Built-in tool implementations. M1-C adds screenshot/getStats/world.query,
        // M2-A the input.* primitives, M2-C getGuiState and M2-B events.subscribe
        // by registering handlers here - dispatcher untouched. M2-D adds look/
        // lookAt, sharing the InputGuard (master switch + rate limit + permission
        // tier) with the input.* tools.
        tools.register("capabilities/list", (ctx, params) ->
```

能力清单同样"零手写"：`Capabilities.list()` 运行时从 jar 内 schema 资源组装（构建期由 gradle `syncToolSchemas` 从 `../sirius-brain/schema` 单向同步）。

### 4.6 BridgeClient：连接监督与 RPC 配对

**思路**：大脑侧只有一个身体接口。RPC 用 uuid id 配对，迟到的回包按未知 id 安全忽略；断线时在途请求立即失败（不悬挂），重连指数退避自管（显式关掉 websockets 库的内建重连）。

```python
# sirius-brain/sirius_brain/bridge/client.py:241-273（节选）
    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """工具调用 RPC：发 ToolCallRequest，按 id 配对等 ToolCallResponse。

        - 正常返回 ``response.result``
        - 身体回错误帧（-32601/-32602 等）→ ``BridgeError(code, message, data)``
        - 超时 / 连接断开 → ``TimeoutError`` / ``BridgeError(CODE_CONNECTION_LOST)``
        """
        if timeout is None:
            timeout = self.config.request_timeout
        req_id, pending = self._register_pending("tool")
        request = ToolCallRequest(id=req_id, method=method, params=dict(params or {}))
        ...
        try:
            response = await asyncio.wait_for(pending.future, timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)  # 迟到的回包会被接收循环按未知 id 忽略
            raise TimeoutError(f"工具调用 {method!r} 超时（>{timeout:.1f}s）") from None
        if response.error is not None:
            raise BridgeError(response.error.code, response.error.message, response.error.data)
        return response.result
```

两个值得知道的细节：hello **同步**发送（监督循环内、就绪信号放行 RPC 之前）——曾有竞态让首个 RPC 抢在 hello 之前出站，回归测试 `test_hello_is_first_outbound_message` 锁死此行为；首连失败立即报错不重试（`connect()` 拿到清晰异常），重试只发生在"连上过又断了"（`client.py:354-413` `_supervise`）。

### 4.7 mock 身体：剧本驱动，专造真游戏难造的场景

**思路**：大脑 M0-M3 全部逻辑对 mock 开发。mock 不是硬编码——行为由剧本（JSON）驱动：工具回什么/延迟多少、task 按文本子串匹配回五态。失败、超时、乱序完成这些真游戏里难复现的场景，在剧本里都是一等公民。

```python
# sirius-brain/sirius_brain/mock/script.py:76-81
    def task_outcome(self, task_text: str) -> ScriptedTask:
        """task 帧命中的剧本：task_rules 按序取第一条 match 为 None 或子串命中者。"""
        for rule in self.task_rules:
            if rule.match is None or rule.match in task_text:
                return rule
        return self.default_task
```

task_id 不参与匹配，回帧一律原样带回——和真 Mod 同一条铁律（`script.py:24-34` ScriptedTask docstring 明示）。

## 4.8 M2 的手：事件层注入、事件通道与 GUI 感知（速览）

三个关键设计事实，细节见 `docs_agent/reports/M2-{A,B,C,D}.md`：

1. **输入走 1.21.1 真实的 GLFW 回调入口**（`KeyboardHandler.keyPress` public 直调、`onPress/onMove/charTyped` 反射直达）——不是绕过事件系统的"动作直调"，Mod GUI、按键映射、反作弊视角下与真人按键同管线同行为（真机验收：像素差 95.4% 确认背包开合）。
2. **事件推送是单一入口的推送通道**：`EventPusher.push()` 过滤订阅（类型+级别）、每连接原子 seq、诚实丢弃计数；截图推流按 N.E.K.O 生产参数（6s 节流最新帧待发、质量×边长双阶梯压 100KB 硬预算）。
3. **GUI 感知的角色分类踩过两次真坑，都已修**：`AbstractContainerMenu.addSlot` 会把 `Slot.index` 覆写成菜单位（真索引在 `getContainerSlot()`）；盔甲槽的容器就是玩家背包，不独立分角色会让"找空背包格"命中头盔位——收官首跑的木板就这么进的头盔：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/GuiContracts.java:190-215（节选）
    public static String roleOf(boolean craftingContainer, boolean resultSlot,
                                boolean playerInventory, int containerSlot) {
        if (craftingContainer) {
            return ROLE_CRAFTING;
        }
        if (resultSlot) {
            return ROLE_RESULT;
        }
        if (playerInventory) {
            if (containerSlot < 9) {
                return ROLE_HOTBAR;
            }
            // Armor/offhand must not masquerade as storage: the first real-machine
            // acceptance run dragged crafted planks onto an "empty player slot"
            // that was actually the helmet slot (armor lives in the player
            // Inventory at container indices 36-39, offhand at 40). Distinct
            // roles let script and brain filters exclude them naturally.
            if (containerSlot >= 36 && containerSlot <= 39) {
                return ROLE_ARMOR;
            }
            ...
```

**M3（会师）**：大脑最简版（单模型：结构化感知+按需截图→VLM→工具）驱动真身体（NEKO 兼容层已取消，2026-08-19 裁决）。之后 M3.5 智能优化（见 §4.10）、M4 反射层（见 §4.11）、M4.1 反射层独立存活修复轮（见 §4.12）→ M5-M9：分层大脑 → 记忆 → 知识库 → 技能沉淀 → 陪伴感。完整路线图见 `docs_agent/sirius-technical.md` §10。

## 4.9 M3 的会师：最小整机大脑活了（2026-08-19）

M3 是两条开发轨的会师点——大脑（Python，M0-M2 对 mock 开发）第一次接上真身体（Java Mod，M1-M2 造的眼与手），在真服务器上跑通完整闭环。

**三层架构首次合体**：

```
玩家在游戏里打字「试试来我这里」
  ↓ Bridge Mod 上报 chat 事件（M2-B 事件系统）
大脑 AgentLoop 收到（过滤掉自己的回声）
  ↓ 组装上下文：系统提示 + getStats + getGuiState 摘要
VLM（qwen3.7-plus）决策 → tool_calls
  ↓ world.query 找玩家 → lookAt 转向 → input.key W 走 → getStats 验位
循环 11 步 → finish("我到附近了，距离约 3 格")
  ↓ command() 在游戏聊天里回话
玩家看到 bot 的回复
```

**真机验收三任务**：

| 任务 | 结果 | 步数 | 耗时 | 说明 |
|---|---|---|---|---|
| 你好 | ✅ 闭环 | 1 | 2s | 纯文字回复，自我介绍+当前状态 |
| 试试来我这里 | ✅ 闭环 | 11 | 25s | world.query→lookAt→走→验位→finish，VLM 会算距离规划移动 |
| 搜集几个云杉木 | ⚠️ 中止 | 22 | 74s | 方向对（找树→截图→靠近）但 token 预算用尽没走到砍 |

**这证明了什么**（给关心项目可行性的人）：

1. **闭环成立**——从"玩家说话"到"bot 自主完成并回话"，全程零人工。这不是脚本回放，是 VLM 实时看着游戏画面做决策
2. **VLM 会用工具**——qwen3.7-plus 能读截图找树、能用坐标算距离、参数被拒绝时会自我纠正（请求 range=100 被拒→改 64 重试）
3. **哑管道原则正确**——Java 侧只管上报数据和执行注入，所有智能在 Python 大脑，两轨解耦干净，运行流畅
4. **急停/自回显/预算保护**都按设计生效——bot 不会无限烧 token、不会把自己的话当指令、玩家说"停下"就停

**还做不到什么**（诚实清单，M3 时点）：

- 复杂任务（找树+砍+收集）单次跑不完——token 预算 200k 太紧，反复截图累积太快
- bot 找特定方块（如 spruce_log）很吃力——world.query 返回 512 个方块就截断，VLM 只能靠截图绕路
- bot 还不会"挖方块"——input.click 左键长按挖方块的组合动作，VLM 还没学会
- 长距离移动会撞墙掉坑——没有寻路（M4 的工作）
- 这些问题都有明确的后续方案（见下方"下一步"）——其中前三条 + 寻路，M3.5 一次解决了（见 §4.10）

## 4.10 M3.5 的智能优化：把小脑还给代码（2026-08-20）

M3 的"不智能"根因是**动作粒度太低**：VLM 被迫当小脑，每按一次 W、每转一次视角都是一次完整的模型调用。参考项目（Numen / Mindcraft）的共同答案是"LLM 只做意图层决策，执行下沉确定性代码"。M3.5 把这个论点落进了代码，并在真机上闭环验证。

**为什么下沉（同任务前后对比）**：

| | M3-C（2026-08-19） | M3.5 T5b（2026-08-20） |
|---|---|---|
| 任务 | 搜集云杉木 5 根 | 砍橡木（同意图复测） |
| VLM 调用 | 22 步 | **4 步** |
| 用量 | 212k tokens，预算耗尽中止 | **16k tokens**，8.0s 完成 |
| 差别 | VLM 逐步键控（query→lookAt→input.key W→验位→…） | `collectBlock("#minecraft:logs", 3)` 一次调用，内部找/走/挖/捡全自动 |

**交付了什么（三层）**：

1. **brain 任务级原语**（`sirius-brain/sirius_brain/agent/primitives.py`）：`walk_to`（Baritone #goto 封装 + 15s 看门狗 + 界面屏障 + 协作取消≤1s）、`dig_block`（bridge dig 优先、旧 jar 自动回退）、`collect_block`（"找最近→走位→挖→拾取"循环；pickup 参数可关）+ `pickup()`（暂未暴露给 VLM）。工具描述本身就是契约——模型读描述就知道"该用哪个、失败怎么办"：

```python
# sirius-brain/sirius_brain/agent/tools.py:87-91
PRIMITIVE_TOOL_HINTS: dict[str, str] = {
    WALK_TO_TOOL:
        "走到目标坐标 (x,z)（y 可选）。受理即执行：自动寻路并阻塞行走到位后才返回，"
        "不需要你操心路径与按键细节，更不要用 input.key 一步步走。成功返回最终坐标；"
        "失败时读返回文本里的建议行动照做；行走超时时同参数重发即可续走剩余路程",
```

2. **bridge 智能原语**：`dig`（动作层监视按住，见下）、`lookAt` 的 `turn_speed_deg_s`（固定角速度平滑转头，"转头像自然转头"）、`world.query` 的 `filter`（registry id / `#tag`，命中按距离最近优先返回 32 条）、`input.click` 的 `hold_ms`（长按）、entities 载荷带掉落物注册名。协议 1.0→1.1→1.2，缺省参数行为与 v1.0 字节级兼容。

3. **提示契约层**：系统提示重写（原语优先/键鼠兜底的分层引导 + 工具边界契约）、预算 200k→500k、错误码→建议动作映射；另支持本地 LM Studio 模型作 VLM（`reasoning_effort:"none"` 是本地关思考唯一有效开关；部署细节见各机 local.md）。

**Bridge 边界从两层升三层（本轮架构修订）**。原分工"输入标准化、感知原语化——Mod 是哑管道"在 M3.5 被一个真机发现修订：事件层（input.click 式注入）的"按住"在 vanilla 里被**操作系统焦点双门控**——聊天框开/关一次（walkTo 的必经路径！）就会永久丢失鼠标抓取，之后注入的按键状态在、却零挖掘零报错。Mod 因此新增第三层"动作层操作原语"（dig 直接调 vanilla 持键路径自己调用的方法，焦点完全免疫），这是 M2-D look 先例的正式确立：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/DigTools.java:52-65
 * <p><b>Why the ACTION layer for the hold (not input.click's event layer):</b>
 * vanilla's continuous destroy is double-gated on OS focus:
 * {@code Minecraft.handleKeybinds} only continues mining while
 * {@code mouseHandler.isMouseGrabbed()}, and {@code MouseHandler.grabMouse()}
 * REFUSES the (re)grab whenever the window is not active - so after any
 * chat-screen open/close while unfocused (exactly the AI-plays-human-watches
 * workflow), an injected PRESS sets the key state but nothing mines
 * (real-machine-verified, M3.5 T6). The dig tool instead calls the exact
 * gameMode methods vanilla's held-button path itself runs -
 * {@code startDestroyBlock} / {@code continueDestroyBlock} /
 * {@code stopDestroyBlock} (+ hand swing) - the M2-D "look" precedent: an
 * action-layer primitive where no reliably-injectable event path exists.
 * This keeps working while the human alt-tabs (with
 * {@code keep_running_unfocused}, ticks continue).
```

三层新边界：**感知原语化**（Mod 上报未加工数据）→ **输入标准化**（注入走可校验的 input.*）→ **动作层操作原语**（无可靠事件路径的连续操作允许 Mod 提供意图级工具，如"挖这个方块"）；**任务级语义组合仍在 brain**（walkTo/collectBlock 是 brain 原语，Mod 无任务概念）。

**智能挖掘机制（一图流）**——`collectBlock(["#minecraft:logs"], 3)` 一次调用内部发生的事：

```
VLM 调 collectBlock(["#minecraft:logs"], 3)          ← 唯一一次 VLM 决策
  ├─ world.query(type=blocks, filter=["#minecraft:logs"])   → 候选按距离升序（最近优先 32）
  ├─ 取最近的树干坐标，走位到它旁边 ±1.5 格
  │     └─ walk_to → Baritone #goto（"#" 前缀客户端拦截，不达服务器）+ 轮询到位
  ├─ dig_block(x,y,z)
  │     ├─ 复核存在 + 触及 ≤4.5 格检查（太远不盲挖，教学"先 walkTo"）
  │     ├─ bridge dig 原语：300°/s 平滑转头瞄准 → 动作层按住监视 → 遮挡穿透
  │     │   （视线先穿树叶再达树干也挖得开，结果带 broken_via_occluder 标记）
  │     └─ 挖掉后顺路捡掉落：只捡挖点 4 格内、注册名精确匹配的（别人的不碰），
  │         走过去让 vanilla 吸附，实体消失=已拾取
  └─ 循环到 3/3 或 64 格内清空 → 收尾话术（有收获=成功；一个没有=失败+建议）
```

挖掘时长不是拍脑袋——常量注释就是取值依据（徒手 oak_log 需连续按住 3.0s，600ms 短按八段全空是真机踩过的坑）：

```python
# sirius-brain/sirius_brain/agent/primitives.py:58-66
#: 单段挖掘的按住时长（毫秒）。**必须 ≥ 目标徒手破坏时间**——vanilla 机制松键后
#: 挖掘进度清零，跨段不累积，段太短永远破不了。徒手 oak_log hardness 2.0 → 2.0×1.5=3.0s
#: （有斧时才是 0.3-0.6s），基准段 3500ms 覆盖无遮挡徒手木类（M3.5 T5a 真机教训：
#: 600ms 八段全空，见 docs_agent/reports/M3.5-T5a.md）。**遮挡场景按段递增**：
#: 视线先穿 k 格树叶（0.35s/格）再达树干，需 hold ≥ 0.35k+3.0s——段 n 的 hold
#: = min(DIG_CLICK_HOLD_MS×n, DIG_CLICK_HOLD_MAX_MS)，第 3 段起 8s（真机实证
#: 8s 可穿透 2 格树叶 + 树干）。协议上限 10000。石头徒手 7.5s 仍超段长——那是
#: "给工具再挖"的预期教学失败，不是本层要解决的
DIG_CLICK_HOLD_MS = 3500
```

另一个小而重要的设计是**滚动状态免费搭车**（Numen 做法）：每步 VLM 调用前自动注入一条最新的自身状态摘要（替换式，不累积），模型不必为"我在哪/血量如何"专门花一次工具调用：

```python
# sirius-brain/sirius_brain/agent/loop.py:613-618
    async def _inject_rolling_status(self, messages: list[dict[str, Any]]) -> None:
        """每步 VLM 调用前注入一条〔当前状态〕user 消息（M3.5，替换式不累积）。

        Numen runtime_state 的"免费搭车"做法（EntityAgentLoop）：模型每步白拿一份
        最新自身状态，不必为"我在哪/血量如何"专门花一次 getStats 工具调用。
        getStats 失败则跳过该步注入（上一条旧状态留在原地，不阻塞主循环）。
        """
```

**真机验证数字**：Baritone 冒烟（#goto 3s 收敛 2.0 格）；原语层 6/6（急停 1.49s）；T6 五项 5/5（collect 提速 4.9 倍至 16.8s/3 根；遮挡穿透 3.9s；平滑转头收口与瞬间转 0.000° 一致）；T7 拾取 4/4；pytest 263→302、Java 冒烟 241→345。

**还做不到什么**（M3.5 诚实清单；斜体为后续轮次的处置）：

- 本地 VLM 遇观察类问题偶尔不调工具直接幻觉作答——*已解决（M3.6）：系统提示新增"观察纪律"硬约束节*
- 掉落物匹配是精确 id：挖石头掉的圆石（stone→cobblestone）不会被捡——*已解决（M3.6）：dig 实测 `drops` 报告（挖掉后看实际掉了什么）取代掉落表知识，模组方块零硬编码；多人服"不碰别人掉落"的礼仪照旧*
- `pickup()` 原语已实现未暴露给 VLM——*已解决（M3.6）：已注册进工具表（14→15）*
- 无 filter 的 world.query 仍是"先截断后排序"旧语义（brain 侧已防御；根治属协议变更）
- input.click 事件层长按、input.mouseMove 转头仍需窗口焦点（vanilla 门控无开关；"AI 播放"场景的标准部署 = 游戏窗口保持前台）
- 多人服在线复验待用户开服（本轮服务器主机离线，单机验证 4/4）；完整聊天循环验收待用户进世界（直驱已过）——*后者已由用户实测通过*
- collect 16.8s/3 根的下一步提速在换斧/执行器层（M5），不在本层

## 4.11 M4 的反射层：把脊髓还给代码（2026-08-20）

M3.5 把"怎么走/怎么挖"下沉给了代码，M4 下沉的是更底层的东西——**保命**。人在被水淹时不会先开个会讨论要不要浮上去：脊髓接管，大脑事后才知道。Sirius 的反射层就是这个脊髓：一个 0.5 秒一轮的调度循环，盯着身体状态（氧气/血量/着火/周围的怪），触发条件满足就直接动手——**全程零 VLM token，大脑只在事后收到一行简报**。

**它是什么**：`sirius-brain/sirius_brain/agent/reflexes.py` 里的 `ReflexScheduler` 调度协程 + 七条"反射链"。每轮采样身体（getStats 2Hz + 危险事件即时置位 + 每秒扫一次周围实体），按注册顺序问每条链"你能处理吗"（`can_run`），**第一个说能的赢**——没有优先级比较、没有竞价，顺序写死在注册表里，可预测性优先（Numen 的裁决理由）：

```python
# sirius-brain/sirius_brain/agent/reflexes.py:420-434（节选）
                    for chain in self.chains:
                        if chain.level.value > self.level.value:
                            continue  # 等级门控：L0 下七条全跳过
                        if not chain.can_run(self.body):
                            continue
                        logger.info("反射触发：%s（interrupt=%s）",
                                    chain.id, chain.interrupt)
                        self.busy = True
                        try:
                            await chain.act(self.body)
                        except Exception:  # noqa: BLE001 —— 单条反射异常不杀调度器
                            logger.exception("反射 %s 执行异常", chain.id)
                        finally:
                            self.busy = False
                        break  # 层内单候选：本轮有链执行即结束
```

**三档打断**是这轮设计里最"真客户端"的一笔：反射动身体有三种动法——`none` 纯旁路（说话时转头看你，不碰任何东西）、`cooperative` 短暂借走按键再还（水下按 SPACE 上浮、卡位时挣扎几下，**任务不停**——因为真客户端里"寻路走路"和"按空格上浮"本来就是并存的输入）、`preempt` 直接掀翻当前任务（着火撤离/逃离/低血/死亡——保命比手头的活重要）。掀任务走 `AgentLoop.request_preempt`，和玩家说"停下"同一条检查点，但不播报"好的停下了"——反射自己会报。

**三级反射等级**（严格单调能力束：L0 ⊂ L1 ⊂ L2，不做每条反射的单独开关——开关矩阵是 Numen 删掉过的坑）：

| 等级 | 名字 | 身体 | 大脑 |
|---|---|---|---|
| L0 | 观察（observer） | 什么都不自动做——脊髓完全交出 | 危险**照常被告知**（〔危险〕/〔紧急〕简报照进认知），怎么处置 VLM 自己决定 |
| L1 | 自保（self_preserve，默认） | 七条反射全开：换气/脱困/撤离/低血/死亡/危怪逃离/说话注视 | 事后收一行〔本能反应〕简报；死亡和低血即时〔紧急〕+ 中止任务 |
| L2 | 自卫（guard，**预留位**） | 未实现——切换会被拒绝并播报"预留位" | —（实现时须同步修订"禁止攻击"安全约束） |

切换是聊天里说一句"**反射等级 观察**"或"**反射等级 自保**"——**人类专属通道**，VLM 工具表里没有这个能力，模型无权给自己的身体升攻击等级。等级还是代码与认知的唯一同步点：系统提示里的"本能说明"节按等级动态生成，L0 时模型会被告知"你没有本能反射，一切自理"。

**真机数字**（集成服务器单机世界，VLM 全程零调用）：

| 场景 | 实测 |
|---|---|
| 溺水 | 潜入深水，氧气 300→217（≤240 触发）→ 自动按 SPACE 上浮 4 次 → 氧气回满，简报"换气完成"；被困水下按不上浮的场景也验了——10 秒封顶后主动聊天求助 |
| 死亡 ×3 | 聊天播报"我死了……死亡位置约 (x,y,z)。等你指示，我不会自动重生"——死亡屏保持打开（确不自动重生） |
| 低血 | 被骷髅射到血量 1.86（阈值 6）→ 停任务 → 聊天警报 → 〔紧急〕消息注入认知 |
| 危怪逃离 | 白天逼近末影人到 0.47 格（危险半径 1.8）→ 停下 → 反向跑 8 格 → 简报"逃离 enderman……未还击" |
| 等级切换 | L0↔L1 来回切 + L2 拒绝，各带播报；L0 期间推着火事件——〔危险〕简报照进认知、身体零动作；切回 L1 后立刻补执行撤离（**关动作不关感知**验证过） |
| 长距不误伤 | Baritone 走 199 格 77 秒（含涉水爬坡），终点差 1.2 格，全程零反射误触发 |

**还做不到什么**（M4 诚实清单）：

- **着火撤离、脱困没有真机实测**：测试世界不开作弊（放不了火方块）、64 格内没岩浆、也找不到可复现的卡位点——两条反射的逻辑有单测覆盖，触发通道（CRITICAL 事件）是 M2-B 就验证过的；补测需要火源环境，留下一轮——*已解决（M4.1）：压测探针 GUI 自动化开作弊后，火（脚下点火 15s 复燃）与卡位（1 宽 2 深坑）场景补验 PASS，见 §4.12*
- **等级切换的聊天命令没做到端到端**：单机世界没有第二个玩家，bot 自己说的话会被自回显过滤——命令解析有单测，切换执行（播报/生效）真机验过；"玩家在聊天里打字切等级"的完整链路等多人环境补验
- **"说话时看着你"在单机自然不触发**（周围没有别的玩家）
- **逃离半径对远程怪偏保守**：危险半径按怪的碰撞箱宽算（末影人 1.8 格），骷髅在 30 格外就能把 bot 射死、flee 不会触发——远程伤害的实际兜底是低血反射（真机验证过）
- **单候选的代价**：撤离的 8 格行走期间，低血的上报只能排队等（实测延迟约 10 秒）——"通知型"与"动作型"反射分层是候选优化
- 等级切换不持久化（重启回默认 L1）；反射保护以 brain 进程在跑为前提（它不是保命外挂）；游戏窗口最小化时鼠标注入全废（恢复前台即好）

## 4.12 M4.1 修复轮：两个怀疑被证据推翻，反射层学会独立保命（2026-08-21）

M4 收官后用户在场观看了一整场验收重跑（M4-rerun），画面很好但日志里露出五个缺陷：反射连发的 `#stop` 和 `#goto` 在聊天框里粘成一行 `stop#goto`（逃生命令被吞，bot 原地被围殴打死）；bot 走路是"固定视角的平移"（横着飘，头不动）；死亡播报在协议层发了却没出现在游戏聊天（死亡屏把 T 键吃了）；压测时撞上限流（当时归因"长按按 tick 扣令牌"）；换气反射和 Baritone 的水下目标来回拉锯把 bot 按进水里。M4.1 的两个目标：**零上层指令时反射层尽量不死**（诚实口径：记录死因而非承诺零死亡）和**走路自然转头**。

### 两个怀疑，都被证据推翻了

排障最有意思的地方在于：五个缺陷里有两个，最初的怀疑方向是完全错的——修 bug 先要审自己的假设。

**怀疑一："转头冲突"——以为是两个旋转源打架，其实压根没人在转头。** 当时的合理猜想：M3.5 的 TurnController 和 Baritone 都在逐 tick 写视角，互相覆盖导致相机冻结。证据说话：调出 M4-rerun 那段 130 格长走的 bridge 审计日志，**全程零条视角写入**（只有状态轮询）——TurnController 根本没活跃（它收敛即注销、有自过期上限，不存在"驻留霸占"）。真因是 **Baritone 这个构建走路压根不转头**：它用 WASD 组合平移移动，相机全程冻结。于是修复策略从"调停两个旋转源"变成"自己补位"——bridge 新增 `MovementLook`，每 tick 看速度矢量，头以固定角速度转向前进方向：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/MovementLook.java:94-106（纯决策，不起游戏可冒烟）
    static Double nextYaw(double currentYaw, double vx, double vz, double speedDegPerSec) {
        double horizontal = Math.sqrt(vx * vx + vz * vz);
        if (horizontal < MIN_SPEED) {          // 0.05 b/t ≈ 1 m/s：静止/漂移/纯下落不转头
            return null;
        }
        double targetYaw = LookContracts.wrapDegrees(Math.toDegrees(Math.atan2(vz, vx)) - 90.0);
        double delta = LookContracts.yawDelta(currentYaw, targetYaw);
        if (Math.abs(delta) <= DEADZONE_DEG) { // 与运动方向夹角 ≤2° 死区不写
            return null;
        }
        double step = speedDegPerSec * TICK_SECONDS;  // 300°/s 固定角速度
        return LookContracts.approach(currentYaw, delta, step);
    }
```

三条让位规则保证它不和任何主动旋转打架：显式转头任务（lookAt/挖掘瞄准）活跃时让位；与运动方向夹角 ≤2° 死区不写（将来 Baritone 若自己转头，目标一致时控制器自然静默）；只写 yaw 不碰 pitch（不扰动瞄准）。修复前后对比：

| | 修复前（M4-rerun 实测） | 修复后（M4.1 真机 #goto 40 格） |
|---|---|---|
| 画面观感 | 固定视角平移，横着飘 | 头随身体转向前进方向 |
| 行走中视角写入 | 审计日志**零条**（没人转头） | 43 样本 yaw 覆盖 10 个度值（270→314 随路径转向） |
| 头与运动方向夹角 | 恒定偏移（头不动身体动） | **中位 0°、P90 8°、最大 16°** |

**怀疑二："长按按 tick 扣令牌"——以为限流器计费有 bug，其实是测试脚本自己打穿了它。** M4-rerun 死亡演示时撞上 -32010 限流拒绝，当时推断"input.key 长按每 tick 消耗一个令牌，和反射的 SPACE 叠加打空 20/s 预算"。两步验证推翻：① 源码里所有计费点（key/text/mouseMove/click/dig）都是每次调用 `tryAcquire(1)`——长按只占 1 个令牌；② 真机裁决实验：1500ms SHIFT 长按 + 19 个并发短按，**19/19 全部通过**；30 个不等待的齐发才观测到拒绝——限流器工作得好好的。当时 -32010 的真凶是**演示脚本自身的管道化并发**（input.* 在主线程逐个执行 ~50ms，老老实实 await 的串行调用 ~5/s 永远追不上 20/s 的补充，只有 fire-and-forget 齐发才打得穿）。结论：不改代码，改的是归因——反射层全部 await 串行（≤2/s），天然安全。

### 其余三个缺陷的修法

- **命令竞态**：串行锁从 LoopClient 下沉到 `BridgeClient.command`（客户端级，探针/反射/调度器一切发送方共用），时序从"拍定时数"改成"确认"——T 之后轮询等聊天屏真的打开（被其他 GUI 占着就拒绝盲发，顺带消灭了"文本盲发进任意界面 + ENTER 误点按钮"的旧风险），ENTER 之后等它真的关闭（关不掉说明发送失败，ESC 丢弃残留）。真机 10 条 #stop/#goto 连发：**0 合并**，位移 14.9 格。
- **死亡播报**：bridge 新工具 `chat.send`——进程内直调 vanilla 聊天屏 ENTER 走的同款发送入口（`ClientPacketListener.sendChat`），死亡屏等任何 GUI 都屏蔽不了。上轮"播报 PASS ×3"的复查结论也很诚实：当时验的是协议层已发送，游戏聊天里看得见只是轮询抢在死亡屏渲染前的**时序侥幸**。本轮端到端实证：世界恰好停在死亡屏上，播报"我死了……等你指示，我不会自动重生"真实出现在游戏聊天，死亡屏保持打开。低血警报同样改直发——压测抓到过"警报发出前毫秒级死亡、死亡屏吞掉 T 键"的窗口期竞争。
- **换气×Baritone 拉锯**：cooperative 反射生效期间发 `#pause`（Baritone 停下按键但保住目标），结束后 `#resume` 续走——比 #stop 优越在 walk 任务的原语语义完整保留。真机有 Baritone 聊天回执为证。

### 分段压测：让反射层独自面对五种死法

验收标准由用户修正为**分段制**：连续跑 10 分钟会被活活饿死——那是资源约束，不是反射层的缺陷。改为五场景各 2 分钟独立压测，AgentLoop 只挂调度器零任务消费（零 VLM token），场景前用饱和效果消除饥饿变量，死了就记归因：

| 场景 | 结果 | 反射触发 | 死亡归因 |
|---|---|---|---|
| 火（脚下点火，15s 复燃） | **PASS** | 撤离×1、低血×1 | —（血量 19~20 振荡） |
| 卡位（1 宽 2 深坑+围壁） | **PASS** | 脱困×20（每冷却期一次爆发） | —（恒满血） |
| 综合混灾（怪+火+水） | **PASS** | 换气×10、撤离×1、逃离×3、低血×1 | —（最低 7.7 存活） |
| 怪群围攻（3 僵尸+2 溺尸，持续补怪） | **DEATH** t=20s | 逃离×2（均到位）、低血×2、死亡播报×1 | **设计边界非缺陷**：反射链全部正确仍死——裸装无武器被 5 只怪轮番贴身，L1 禁攻击+逃离冷却 6s。L2 战斗模块才是怪群存活的真正解 |
| 深水（tp 到最深水格，浮出即按回） | 首轮 **DEATH** t=74s → **修复后回归 PASS** | 换气×7（首轮）/ ×14（修后全成功） | 占空比缺陷：旧"按 400ms 歇 500ms"净上浮≈0（上升被歇息时的下沉吃光）。修为按满 800ms 再复查，修后 2 分钟存活、血量最低 4.3 |

两次死亡全部归因、一次修复回归——这正是"尽量不死"的诚实口径该有的样子。压测还顺手引出两个计划外的修复：换气占空比（上表）和 `safe_command`（低血反射的 #stop 被占用的 GUI 拒绝时抛异常，会跳过后面的直发警报——保命链里的停止动作不该有能力打断警报，现在失败只记日志继续走）。

**职责边界声明**（压测固化的诚实口径）：反射层 = 突发危险的即时自救（溺水/着火/围攻/卡位）——它是脊髓不是全能保镖；慢性消耗（饥饿/长期低饱食）不在职责内，**进食反射**（检测到饿+背包有食物自动吃）记入 backlog，是 L1 的合理未来成员。

**还做不到什么**（M4.1 诚实清单）：

- 怪群高密度围攻仍可致死（L1 禁攻击设计边界；L2 战斗模块是解，留独立轮次）
- 慢性饥饿不在职责内（进食反射 backlog，M4.2/M5 候选）
- 单候选串行：撤离的 8 格行走期间低血上报仍要排队（~10s；safe_command 已消除"上报被打断"的风险，阻塞本身仍在）
- 等级切换多人端到端、说话注视有对象（M4 遗留沿用，等多人环境）
- 反射保护以 agent 进程常驻为前提（M4 已知边界不变）

## 5. 已知边界与下一步

**当前边界（M4.1 完成态）**：M0-M4.1 全部完成。bot 能听、能看、能想、能动、能回话、**能保命**——复杂任务预算内一次跑完（4 步 16k tokens），溺水/危怪/低血/死亡零 token 自动处置，零上层指令下突发危险的自救经分段压测验收（3 PASS / 2 DEATH 全归因），走路会转头看向前进方向。351 项 pytest + 369 项 Java smoke 全绿，协议 v1.3。

**M3 暴露的问题 → M3.5 处置**（对照 §4.9 诚实清单）：

| 优先级 | 问题（M3 时点） | M3.5 处置 |
|---|---|---|
| P0 | token 预算 200k 太紧，复杂任务跑不完 | **已解决**：预算提至 500k + 原语下沉后同任务用量 212k→16k |
| P0 | world.query 512 截断，找不到特定方块 | **已解决**：filter 参数（registry id/#tag、最近优先 32）；无 filter 路径保留旧语义（brain 已防御） |
| P0 | VLM 不会组合"挖方块" | **已解决**：digBlock/collectBlock 原语 + bridge dig 智能挖掘 |
| P1 | hello_ack 帧未在协议建模 | 未动（功能不影响，清理项） |
| P1 | command() 长消息可能丢字 | 未动（真机长文本测试+调时序常量） |
| P1 | 22 步 history 未压缩 | 原语下沉后步数骤降，压力缓解；摘要机制留 M5 |
| P2 | 视角转动需窗口前台 | **部分解决**：lookAt/look/dig 走动作层焦点免疫；input.mouseMove 仍需焦点 |
| P2 | 无寻路，长距离撞墙 | **已解决**：Baritone 集成（#goto/#stop 聊天命令驱动，真机冒烟通过） |

**M3.5 新暴露的问题**：见 §4.10 诚实清单（其中幻觉直答、掉落表知识、pickup 暴露三项已由 M3.6 解决；焦点门控残余、多人服复验仍开放）。

**M4/M4.1 交付与遗留**：反射层（等级框架 + 调度器 + L1 七反射 + 事后知会）上线，真机多项 PASS（见 §4.11）；M4.1 修复轮（命令竞态锁下沉/移动转头/死亡播报直发/#pause 配对 + 分段压测，见 §4.12）关闭了着火/脱困真机补测的遗留。仍开放：多人环境补验（等级切换端到端 + 说话注视）、L2 战斗模块（预留位，后置独立轮次——压测证明它是怪群存活的真正解）、进食反射（backlog，M4.2/M5 候选）、调度分层优化（低血上报被撤离行走阻塞 ~10s）。

**下一步（等用户反馈定）**：候选方向——M5 分层大脑（规划器/执行器分家、任务卡、TaskManager，原路线图主体）；或先补 L2 战斗 / 进食反射小轮 / 多人真机补验这类收尾件。反射层给未来留的挂点：behavior_log 简报流是 M6 危险记忆（"这个地方有骷髅"）的天然数据源。
