# Sirius 全局技术文档（overall）

> 写给第一次接触本项目的**人**：从一段话看懂全貌，往下逐层加深，直到贴着源码的细节设计。
> 所有代码片段逐字摘自仓库真实文件并标注 `文件:行号`（截至 2026-08-18，M0+M1 完成态）。
> agent 侧的权威规格在 [`docs_agent/`](../docs_agent/)（本文是它的可读重述，冲突时以权威为准）。

## 0. 一段话

Sirius 是一个 Minecraft AI 陪玩项目：让 AI 拥有**一个真正的 Minecraft 客户端**当身体（不是协议模拟器，也不是服务端假人），后端 Python 大脑通过 WebSocket 指挥这具身体——看它看的画面、（M2 起）替它动鼠标键盘。目标是"陪你进任何服务器玩的 AI 队友"：它看得到你的皮肤和 mod boss 的特效，对服务器来说就是个普通玩家。大脑侧是分层架构（大模型规划器 + 小模型执行器 + 无 LLM 反射层），记忆/技能/人格系统让它越玩越熟练、越处越懂你。

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

两侧唯一的耦合点是**协议**（v1.0，M0 冻结）：MCP 语义的请求-响应 + 事件推送，外加 NEKO 兼容任务帧。大脑对 mock 假身体开发全部逻辑（M0-M3），M3 换真身体零改动——"大脑不绑死身体"已实战验证（同一 BridgeClient 连 mock 与真 Mod）。

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

连接建立的前置：首帧必须是 `hello`（token 握手，BridgeServer.java:228），之后通常先 `capabilities/list` 协商能力（12 项，协议版本 1.0）。

### 2.2 一次任务帧的旅程（NEKO 兼容，fire-and-forget）

```
BridgeClient.send_task("挖一组铁矿")
  → TaskFrame{type:"task", task:"挖一组铁矿", task_id:"T-42"}            frames.py:73
  → （不等待，立即返回 task_id）
  → Mod 干活（M2 前是占位：立即回 interrupted）
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
| `sirius_brain/mock/` | 假身体：剧本驱动的 WS 服务 + JSONL 帧回放（大脑 M0-M3 的开发靶） |
| `sirius_brain/bridge/` | BridgeClient：连接真/mock 身体的统一入口（重连监督/RPC 配对/事件分发） |
| `schema/` | 冻结产物：27 个自包含 JSON Schema（Java 侧消费，构建期单向同步进 jar） |
| `tests/` | 6 个测试文件，真实 WebSocket 回环（不 mock websockets 库本身） |

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
| `ImageOps.java` | 纯逻辑：裁剪/JPEG/预算降级阶梯（纯 JDK/AWT） |
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
// sirius-bridge/src/main/java/io/sirius/bridge/BridgeServer.java:256-261
    /** Constant-time token comparison. */
    private boolean tokenMatches(String candidate) {
        return MessageDigest.isEqual(
                config.token.getBytes(StandardCharsets.UTF_8),
                candidate.getBytes(StandardCharsets.UTF_8));
    }
```

认证转移与看门狗的竞态用 per-connection 同步消除——认证恰好只发生一次，晚到的看门狗被取消：

```java
// sirius-bridge/src/main/java/io/sirius/bridge/BridgeServer.java:69-86
    private static final class ClientSession {
        volatile boolean authenticated;
        volatile ScheduledFuture<?> helloDeadline;

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
// sirius-bridge/src/main/java/io/sirius/bridge/PerceptionTools.java:254-268（省略失败重抛尾部）
    private static <T> T callOnMainThread(ToolContext ctx, Supplier<T> supplier) throws Exception {
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
// sirius-bridge/src/main/java/io/sirius/bridge/PerceptionTools.java:109-117
    private static BufferedImage grabScreen() {
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
// sirius-bridge/src/main/java/io/sirius/bridge/ImageOps.java:126-142
    public static Encoded encodeWithinBudget(BufferedImage image, int quality) throws IOException {
        BufferedImage current = image;
        boolean scaled = false;
        Encoded last = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            for (int q : qualityLadder(quality)) {
                byte[] jpeg = encodeJpeg(current, q);
                last = new Encoded(jpeg, q, scaled);
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
// sirius-bridge/src/main/java/io/sirius/bridge/BridgeServer.java:93-97
        // Built-in tool implementations. M1-C adds screenshot/getStats/world.query
        // (and later input.*) by registering handlers here - dispatcher untouched.
        tools.register("capabilities/list", (ctx, params) ->
                Json.capabilitiesResponse(ctx.id(), Capabilities.list(), Capabilities.PROTOCOL_VERSION));
        PerceptionTools.registerAll(tools);
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

## 5. 已知边界与下一步

**当前边界（M1 完成态）**：输入注入（手）未实现——`task` 帧在 Mod 侧是占位（立即回 interrupted）；事件推送通道未实现；权限分级（observe/input_world/input_gui）与输入限频留给 M2；world.query 的 range 64 开阔空域要一次性摸 ~210 万方块位（主线程数百 ms）；客户端侧生物血量常未同步（best-effort）。

**M2（手）**：input.* 四原语（mouseMove/click/key/text）+ look/lookAt/getGuiState + 事件订阅推送 + 权限分级/限频。验收 = 纯脚本重放"按 E 开背包→拖木头→合成工作台"——整个项目可行性的证明点。

**M3（会师）**：大脑最简版（单模型：截图→VLM→工具）驱动真身体 + NEKO 协议兼容层。之后 M4-M9：反射/寻路 → 分层大脑 → 记忆 → 知识库 → 技能沉淀 → 陪伴感。完整路线图见 `docs_agent/sirius-technical.md` §10。
