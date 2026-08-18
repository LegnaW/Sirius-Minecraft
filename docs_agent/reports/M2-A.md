# M2-A 工作报告

- 任务：sirius-bridge 四个输入原语（input.key / input.text / input.mouseMove / input.click）：事件层注入 + 限频 + 审计 + GUI 点击留证
- 日期：2026-08-18
- 状态：完成（代码全部落地并部署；真机行为待主管验收）
- 验收：build + smokeTest 111/111 全绿；jar 已 deploy.cmd 部署到测试客户端；代码审阅要点见下（事件层注入、主线程提交、限频/审计/留证）；真机"按 E 开背包"等见待真机验证清单

## 交付物

- `sirius-bridge/src/main/java/io/sirius/bridge/InputTools.java`：四工具 MC 薄壳——GLFW 事件回调注入（keyPress 公有直调；charTyped/onPress/onMove 反射）、主线程提交 + latch 10s 超时、令牌桶限频、GUI 点击留证（截图→缩长边 1024→JPEG q40→`logs/sirius_evidence/`）、INPUT 审计行、RELEASE/连点调度线程
- `sirius-bridge/src/main/java/io/sirius/bridge/KeyCodes.java`（纯逻辑）：逻辑键名→GLFW 键码映射（字母/数字/F1-F25/方向/修饰键/导航/小键盘/标点/别名），键码域 32..348，反向 canonical 名
- `sirius-bridge/src/main/java/io/sirius/bridge/TokenBucket.java`（纯逻辑）：令牌桶限频器，容量=1 秒额度，时钟可注入（冒烟确定性测试）
- `sirius-bridge/src/main/java/io/sirius/bridge/InputContracts.java`（纯逻辑）：四工具参数校验（照冻结 schema，违规 -32602）、结果组装、留证文件名生成（`evidence_click_<yyyyMMdd_HHmmssSSS>.jpg`，本地时区）
- `BridgeConfig.java`：追加 3 键 `input_enabled`（默认 true）/`rate_limit_per_sec`（默认 20，1..1000）/`gui_click_evidence`（默认 true）；已有键语义未动
- `Json.java`：新错误码 `-32010 RATE_LIMITED`、`-32011 INPUT_DISABLED`
- `BridgeServer.java`：+1 行 `InputTools.registerAll(tools, config)` 挂载；shutdown 时关输入调度线程
- `PerceptionTools.java`：grabScreen 改包私有（InputTools 留证复用截图管线），仅此一处可见性变化
- `SmokeMain.java`：+66 项冒烟（键表/令牌桶/参数校验/结果组装/留证命名），总 111 项挂入 build
- `sirius-bridge/README.md`：新增 "The input tools (M2-A)" 章节（行为/坐标系/限频/留证/错误码/注意事项）

## 关键决策与理由

1. **事件层注入而非动作直调**：一切输入走 1.21.1 真实的 GLFW 回调入口——`KeyboardHandler.keyPress`（public）、`KeyboardHandler.charTyped` / `MouseHandler.onPress` / `MouseHandler.onMove`（private，反射 setAccessible）。替代方案是直接调 `Minecraft.setScreen` / `KeyMapping.click` / `Screen.mouseClicked` 等动作方法——被否决：绕过 NeoForge 事件钩子、输入类型追踪、KeyMapping 状态机，Mod GUI 与反作弊视角的行为都会与真人不一致（正是规格"风险前置"要点）。
2. **私有回调用反射而非复制分发逻辑**：onPress/onMove/charTyped 在 1.21.1 是 private。复制其内部逻辑（screen.mouseClicked + ClientHooks 前后钩子 + wrapScreenError + KeyMapping）会随版本漂移且易漏钩子；反射一行直达原方法。NeoForge 生产环境对 net.minecraft 包全开放（FML 模块变换），setAccessible 可用——此点列入真机验证清单第 1 条。若反射查找失败，工具返回 -32603 且启动期日志明示，不会静默。
3. **input.key 的 code 双形态**：冻结 schema 里 `code` 是整数（GLFW 键码），但任务简报要求逻辑键名。实现两者都收：字符串走 KeyCodes 表解析成同一整数，整数直接用——协议面（schema/返回结构）不变，调用方（大脑）可用可读名。
4. **默认 50ms tap 语义**：duration_ms=0（缺省）也调度 RELEASE——PRESS 后 50ms 自动 RELEASE。E 开背包、T 开聊天都是"轻点"；长按 W 用 duration_ms=500。RELEASE 不由请求方手动发，避免键卡死（另加 60s 上限护栏）。
5. **响应只等首个事件，后续事件按时刻调度**：latch 只等 PRESS/首事件（与 M1-C 同款 10s 超时防最小化饿死）；RELEASE 与连点由 daemon 调度线程在到期时 `Minecraft.execute` 提交——事件间隔不依赖 WS 往返延迟，连点 50ms 节奏稳定。
6. **GUI 点击位置=MouseHandler 内部位移**：onPress 的 GUI 坐标换算用 handler 自己的 xpos/ypos（不是真实 OS 光标），所以**点击前必须先 input.mouseMove**；mouseMove 返回 gui_scaled 坐标（与 onPress 同公式），给 M2-C 的 getGuiState 对齐用。
7. **留证失败不阻断点击**：截图/编码/写盘任何异常→evidence 省略+warn 日志，点击照常注入（留证是安全栏，不是前置条件）。
8. **限频粒度=每次工具调用 1 token**：四工具共享一个桶（20/s）；连点的重复事件不再逐个计费（一次调用=1 token），文本整串=1 token。若真机后需要更严，把 tryAcquire 挪进事件层即可。

## 实现要点 / 1.21.1 输入 API 笔记（M2-C 必读）

**签名（全部从 gradle 缓存反编译源逐参核实，`neoformruntime/intermediate_results/sourcesAndCompiledWithNeoForge_*.jar`）：**

1. `KeyboardHandler.keyPress(long window, int key, int scancode, int action, int modifiers)` — **public**。参数序=GLFW key 回调（window,key,scancode,action,mods）；action 1=PRESS 0=RELEASE 2=REPEAT。入口即 `window == mc.getWindow().getWindow()` 校验，**必须传真窗口句柄**。
2. `KeyboardHandler.charTyped(long window, int codepoint, int modifiers)` — **private**，反射调。来自 `glfwSetCharModsCallback`（1.21.1 用 CharMods，codepoint+mods）。**只在有 Screen 且无 overlay 时生效**；代理对（surrogate pair）会被它拆成两个 char 分别投递——中文/emoji 直接可用。Entry：`Minecraft.keyboardHandler`（public final 字段）。
3. `MouseHandler.onPress(long window, int button, int action, int mods)` — private。action 1=PRESS 0=RELEASE。GUI 路径：`xpos * guiScaledWidth / screenWidth` 换算后走 NeoForge 前后钩子 + `screen.mouseClicked/mouseReleased`（**mods 不传给 mouseClicked**）；无 GUI 路径：未 grab 时先 `grabMouse()`，再 `KeyMapping.set/click(MOUSE)`=攻击/使用/拾取。
4. `MouseHandler.onMove(long window, double x, double y)` — private。窗口客户区像素、左上原点；首次（ignoreFirstMove）只记位置，其后 delta 进 accumulatedDX/DY。
5. **视角转动不在 onMove 里**：`Minecraft.runTick` 每帧渲染前调 `mouseHandler.handleAccumulatedMovement()`——有 Screen 时投 mouseMoved（hover 更新）+ 拖拽；无 Screen 且 mouse grabbed 时 `turnPlayer`（灵敏度/平滑相机公式全套）。⇒ 注入 onMove 后**下一帧**才生效视角/hover，且要求 `mc.isWindowActive()`（窗口需前台）。
6. 窗口 API：`mc.getWindow().getWindow()`→long 句柄；`getScreenWidth()/getScreenHeight()`=客户区像素（坐标基准）；`getGuiScaledWidth()/Height()`=GUI 逻辑坐标。测试客户端无 DPI 缩放时客户区像素==截图像素==framebuffer 像素，1:1。
7. `InputConstants.getKey(key, scancode)`：key≠-1 时走 KEYSYM（scancode 不参与映射），故注入 scancode 用 `glfwGetKeyScancode(key)` 取真值、失败兜 0 均可。
8. **修饰键真相**：keyPress 的 mods 参数只透传给 `screen.keyPressed` 与 NeoForge 钩子；`Screen.hasShiftDown()` 等读**真实 GLFW 键态**（glfwGetKey），注入的 mods 位骗不过它。⇒ GUI 内 shift+click 语义需真注入 LEFT_SHIFT 键（KeyMapping 路径有效）或键盘导航绕行（见已知限制）。
9. F3 手动崩溃陷阱：keyPress 入口检测 Ctrl+C 按住 10s 触发崩溃报告——注入频率下无风险（我们从不长按住真实 Ctrl+C 组合）。
10. 坐标系：mouseMove x,y ∈ [0, screenWidth/Height]，越界钳制（真人光标不可能在客户区外）；响应回传钳制后值与 gui_scaled。
11. keyPress 的 keyScreenshot 快捷键匹配在 PRESS 时截全屏图（F2 场景），与本工具无冲突。

**线程模型**：WS 线程做校验/限频/审计/JPEG 编码/文件写；一切回调经 `ToolContext.onMainThread`（=vanilla 自己 GLFW 回调的 `minecraft.execute` 编组方式）提交主线程；latch 只等首事件；RELEASE/连点由 `sirius-bridge-input-scheduler`（daemon 单线程）到点提交。魔法数字：tap 50ms、连点间隔 50ms、按压 25ms、duration 上限 60s、count 上限 8、文本 512 码点——全部在 InputContracts 常量并写入 README。

## 验证方式

- `gradlew build` 全绿（含 `check`→`smokeTest`）：**111/111**（M1-C 45 项全保留 + 新 66 项：键表 18、令牌桶 5（时钟注入确定性：20 连发通过/第 21 拒、50ms 恰回填 1 token、闲置封顶）、参数校验 28、结果组装 5、留证命名 4、input.key 13 等）
- `deploy.cmd` 输出 `Deployed: ..\.minecraft\versions\1.21.1-Sirius\mods\sirius_bridge-0.1.0.jar`（旧 jar 先移除，无残留）
- 编译即一次过点：全部 MC API 调用（字段/方法名/参数序）对照反编译源写出，无一处凭记忆
- 真机行为按约束未自测（不启动客户端），风险面收敛进下述清单

## 待真机验证清单（主管验收用）

1. **反射可达性**：生产客户端里 `charTyped/onPress/onMove` setAccessible 成功（启动后 capabilities 正常、发一次 input.mouseMove 不回 -32603 即证明；失败会有 ERROR 日志 `not found`）
2. **按 E 开背包**：`input.key {"code":"E"}` → 背包打开（screenshot 验证 + 响应 `screen_open:true`）；再按 E 或 ESC 关闭
3. **鼠标移动转视角**：无 GUI 时两次 `input.mouseMove`（不同 x）→ 截图对比视角变化（需游戏窗口前台）
4. **GUI 点击链路**：开背包 → `input.mouseMove` 到某格 → `input.click {"button":0}` → 物品被拿起；`logs/sirius_evidence/` 出现 JPEG 且内容正确（含被点 GUI）
5. **中文输入**：T 开聊天 → `input.text {"string":"你好"}` → 聊天框出现文字 → `input.key ENTER` 发送
6. **限频**：连续快速 >20 次调用出现 `-32010`；审计日志有 `result=rate_limited` 行
7. **长按 W 前进**：`input.key {"code":"W","duration_ms":1000}` → 截图位移证明持续移动 1s 后停
8. **右键/中键**：右键放置（或使用）、中键拾取（创造模式）
9. **count=2 双击**：背包内双击同格聚合同类物品（或至少两次独立点击生效）
10. **窗口最小化**时调用 → 10s 超时 -32603 不挂死（M1-C 同款行为）
11. **config 开关**：`input_enabled=false` → -32011；`gui_click_evidence=false` → 点击响应无 evidence 字段
12. **screenshot 与 mouseMove 坐标 1:1**：截图上认出的按钮位置直接作 mouseMove 坐标可点中（DPI 缩放异常则需换算，见已知限制）

## 交接须知

- 下一步扩展点：M2-C getGuiState 直接复用 mouseMove 的 gui_scaled 换算与 screen 类名回传模式；look/lookAt 可考虑走 `player.turn`（动作层合法——它本身无事件回调入口）；事件推流见 sirius-technical.md §8.2 节流参数；权限分级（observe/input_world/input_gui）可在 InputGuard 加 token 级别判定，四工具入口已集中
- 已知限制：
  - `Screen.hasShiftDown()` 读真实键态 ⇒ **GUI 内 shift+click 无法仅靠 mods 位伪造**；替代=先 `input.key LEFT_SHIFT`（按住）再点击再松开（KeyMapping 与多数 Mod GUI 走 keyPressed mods，可用；纯 hasShiftDown 分支无效）——真机第 9 条顺带验证
  - 视角转动要求窗口前台（isWindowActive）；最小化时一切输入主线程任务饿死（超时保护）
  - 真实 OS 光标移动会覆盖注入位置（真人共用身体时的预期行为）；注入不移动真实光标
  - input.text 无 Screen 时 delivered=0（非错误，调用方查 screen_open）
  - 高 DPI 缩放客户端下客户区像素≠framebuffer 像素，mouseMove 坐标需按 screenshot 尺寸换算（本机测试客户端不受影响）
  - 令牌桶按调用计费不按事件；连点 burst（8 次≈350ms）与限频 20/s 的叠加语义已按"一次调用=一次配额"处理
- 关联报告：M1-B（ToolRegistry 挂载/线程模型）、M1-C（latch 模式/截图管线/API 方法论）、M1-E（真机验收流程参照）

## M2-A2 补丁：失焦不暂停 + 失焦行为核实（2026-08-19）

- 任务：单玩家窗口失焦自动暂停（tick 冻结）对"AI 自己玩、人围观"致命——加 `keep_running_unfocused` 运行时关闭 vanilla `pauseOnLostFocus`；并从反编译源核实失焦时 input 注入/视角转动的真实行为
- 状态：完成（build + smokeTest 119/119 全绿，jar 已部署；真机行为待主管验收，清单 +3 条见下）

### 交付物

- `BridgeConfig.java`：追加 `keep_running_unfocused` 键（默认 **true**，常量 `DEFAULT_KEEP_RUNNING_UNFOCUSED`），true/false 大小写不敏感解析、非法值回退默认+note、落盘进 sirius_bridge.toml（含注释：手动改 options.txt 的替代方案）；已有键语义未动
- `SiriusBridge.java`：新增 `applyFocusPolicy(config)`，在 `start()`（首个客户端 tick、主线程、options 已加载后）里当 `keep_running_unfocused=true` 时执行 `Minecraft.getInstance().options.pauseOnLostFocus = false`，INFO 日志留痕；try/catch 包裹（失败仅 warn 不阻断 bridge 启动）；**不调用 `Options.save()`，bridge 永不写 options.txt**
- `SmokeMain.java`：+8 项 config 冒烟（默认值/新文件落盘含新键/M2-A 键默认回归/false 解析/save 重写往返/大小写/非法回退/未知键忽略），总 **119/119**
- `README.md`：config 块加键；input.key 补 ESC 语义（GUI 内=onClose 关界面，游戏内=pauseGame 开暂停菜单=单人暂停世界）；新增 "Unfocused-window behaviour (M2-A2)" 小节（机制/失焦能力矩阵/已知限制）

### pauseOnLostFocus API 结论（简报猜测有误，已源码纠正）

1. **1.21.1 里它不是 `OptionInstance<Boolean>`，而是 `Options` 上的 plain `public boolean` 字段**（`Options.java:297`，默认 true）。运行时设置 API 就是字段直写，一行：`mc.options.pauseOnLostFocus = false;`。无需反射、无需 set/put 路径。
2. options.txt 键名 `pauseOnLostFocus`（无空格），经 `ProcessHelper.process("pauseOnLostFocus", ...)` 读写（`Options.java:1212`）。
3. **唯一游戏内消费点在 `GameRenderer.render` 头部**（`GameRenderer.java:999-1007`）：`!isWindowActive() && options.pauseOnLostFocus`（触屏另有例外）且持续 >500ms → `pauseGame(false)` → 开 PauseScreen → 单人未发布时 runTick 的 pause 条件成立 → `timer.updatePauseState(true)` 冻结 tick。**失焦暂停的实质是"自动弹暂停菜单"**；关掉该字段即彻底移除此路径。
4. **F3+P 是官方运行时开关**（`KeyboardHandler.handleDebugKeys` case 80）：翻转同一字段 + `options.save()`——证明字段直写是受支持的运行时操作。
5. `isWindowActive()` = `Minecraft.windowActive` 标志，由 `Window.onFocus`（GLFW focus 回调）→ `setWindowActive` 维护；**失焦只改这个标志**，不解抓鼠标、不清理任何状态。
6. Caveat（已写进 README）：`Options.save()` 持久化全部 plain 字段——用户日后在原版设置界面改任何项或按 F3+P，vanilla 会把我们运行时置的 false 落进 options.txt。这是 vanilla 行为不是 bridge 写入；`keep_running_unfocused=false` 后 bridge 完全不碰该字段。

### 失焦行为结论（全部源码核实）

1. **直调回调不依赖窗口焦点（预期成立）**：`keyPress`/`charTyped`/`onPress`/`onMove` 入口唯一校验是窗口句柄比对（`window == mc.getWindow().getWindow()`，我们传真句柄），四处方法体均不查 `isWindowActive`——它们是普通方法调用，不是 OS 事件。
2. **失焦（窗口可见未最小化）时帧循环照跑** → `Minecraft.execute` 任务照常执行 → **input.key / input.text / input.click（GUI 路径与 KeyMapping 路径）全部有效**；`mouseMove` 的位置追踪也有效（xpos/ypos 无条件更新，GUI 点击换算依赖它）。
3. **视角转动失焦时失效（双重门，无官方开关）**：① `MouseHandler.onMove` 只在 `isWindowActive()` 时把 delta 累进 accumulatedDX/DY；② `handleAccumulatedMovement()`（runTick 每帧调用）整个包在 `if (isWindowActive())` 里——turnPlayer 与 GUI hover/拖拽更新全停。⇒ 已知限制，M4 寻路处理：保持游戏窗口前台（人围观的常态）或改走动作层 `player.turn()`（本就无事件回调入口，M2-A 交接已提及 look/lookAt 候选）。
4. 附带发现：`MouseHandler.grabMouse()` 检查 `isWindowActive()` → 鼠标未抓取状态下失焦点击无法重新抓取（如标题屏）；世界内已抓取则无影响。
5. 最小化仍是最坏情形（M1-C 结论不变）：渲染循环停止泵任务，10s latch 超时 -32603 保护。失焦但可见=受支持的"人切走"场景。

### 验证方式

- `gradlew build` 全绿；smokeTest **119/119**（M1-C 45 + M2-A 66 + M2-A2 config 8）
- `deploy.cmd` → `..\.minecraft\versions\1.21.1-Sirius\mods\sirius_bridge-0.1.0.jar`（哈希与 build/libs 一致）；测试客户端下次启动时 load/save 会自动把 `keep_running_unfocused = true` 补进现有 sirius_bridge.toml（bridge 自己的 config，非 options.txt）

### 待真机验证清单（M2-A2 追加 3 条）

13. **失焦不暂停生效**：默认 config 启动进单人世界 → 切走焦点 >5s → 世界不暂停（getStats 心跳照跑、不弹暂停菜单）；对照：`keep_running_unfocused=false` 重启后切焦点 ~1s 弹暂停菜单（可选）
14. **失焦时 input.key 有效**：切走焦点 → `input.key {"code":"E"}` 开背包 / `input.key {"code":"W","duration_ms":1000}` 位移（screenshot 验证）
15. **失焦时视角转动行为**：切走焦点 → 两次 `input.mouseMove`（不同 x）→ 视角不动（预期=已知限制）；回焦后同操作视角恢复转动
