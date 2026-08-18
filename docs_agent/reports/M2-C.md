# M2-C 工作报告

- 任务：sirius-bridge GUI 状态工具 `getGuiState()`——打开界面的结构化快照（widget 树 + 容器槽位 + 物品注册名），纯逻辑/薄壳分离，降级兜底
- 日期：2026-08-18
- 状态：完成（代码全部落地、build + smokeTest 193/193 全绿；真机行为待主管验收，清单见下）
- 验收：`gradlew build` BUILD SUCCESSFUL；smokeTest **193/193**（既有 175 项一行未动 + 新增 18 项）；协议/schema 零改动（getGuiState 本就在 12 个冻结能力里，Python 侧通用 `call()` 直接可用）；未部署、未提交

## 交付物

- `sirius-bridge/src/main/java/io/sirius/bridge/GuiContracts.java`（新增，纯逻辑 226 行，零 MC import）：参数校验（冻结 schema 声明空对象——params 带任何键 → -32602）、`WidgetNode`/`SlotFact` 记录、`WidgetCollector`（512 节点上限 + `truncated` 标志，满即拒收、调用方停止枚举——world.query 纪律）、widget/slot JSON 组装（空 message 省略、text 仅 EditBox、item 为 JSON null、失败 note）、三种响应组装（无屏 `{"screen_open":false}` / 标准 / fallback `fallback:true + rects + note`）、五种 role 常量
- `sirius-bridge/src/main/java/io/sirius/bridge/GuiTools.java`（新增，MC 薄壳 195 行）：注册 `getGuiState`；主线程（复用 PerceptionTools latch，10s 超时）一次性完成读取 + JSON 组装；widget 递归遍历（children() 树，深度上限 12、节点上限 512，AbstractWidget 出节点、ContainerEventHandler 递归、两者可兼得如滚动列表）；容器路径（`menu.slots` → `getGuiLeft()+slot.x / getGuiTop()+slot.y`，逐槽 try/catch，坏槽降级 item:null+note 不杀整个响应）；遍历抛异常 → fallback 响应（绝不向 dispatcher 抛 → 不出 -32603）；role 判定（CraftingContainer→crafting、ResultSlot→result、玩家 Inventory→index<9 hotbar 否则 player、其余 container）
- `BridgeServer.java`（+2 行）：构造器 `GuiTools.registerAll(tools);` + 注释更新
- `PerceptionTools.java`（1 词）：`callOnMainThread` 从 private 改包私有（GuiTools 复用；行为零变化，javadoc 注明 M2-A 的本地副本保留）
- `SmokeMain.java`（+127 行）：+18 项冒烟，总 **193/193**
- `sirius-bridge/README.md`：新增 "The GUI state tool (M2-C)" 章节（响应形状、坐标基准=gui-scaled 与 mouseMove 返回同基、role 语义、46 槽分解、cap/truncation、fallback、已知限制）；frames 表加行；冒烟计数 175→193；项目布局/状态注记/下一步更新

## 关键决策与理由

1. **JSON 组装放主线程任务内**（简报允许二选一）：界面数据是一次性快照，拆两半要在 WS 线程重建遍历状态（collector 本身可变）；组装是纯 Gson 操作（46 槽 + 几十 widget 微秒级），主线程零感知。screenshot 的"重后处理放 WS 线程"理由（JPEG 编码 10-30ms）在这里不存在。
2. **共享 latch 而非第三份拷贝**：简报给了两个选项；改 PerceptionTools.callOnMainThread 为包私有是 1 词改动、行为零变化（可见性而已），比复制 30 行或新建工具类都干净。InputTools 里 M2-A 的副本不动（避免无谓 churn），javadoc 已注明新工具优先用共享版。
3. **params 严格校验为空**：冻结 schema `getGuiState.json` 是 `"properties": {}`。简报说 "validate params is empty like getStats does"——实际 getStats 完全忽略 params（不校验）；我选择**严格**：任何键 → -32602（与 screenshot/world.query 的校验纪律一致，且可冒烟测试）。空对象 `{}` 与缺省 params（dispatcher 补空对象）都正常。
4. **空 message 省略而非置空串**：简报留了选择（"empty message omitted or empty"）；省略让响应更干净（几十个无 label 的图像 widget 不会都带 `"message":""`），与 InputContracts.clickResult "null 字段省略" 的既有风格一致。text 同理（非 EditBox 无此字段）。
5. **role 判定照 Numen 通用法照抄 + 兜底 "container"**：crafting/result/hotbar/player 四角色按简报；但简报没说箱子/熔炉等**非玩家容器**的槽给什么 role——补了通用兜底 `container`（判定不了就诚实说是容器槽），否则会出现 undefined。armor(36-39)/offhand(40) 落在 "player"（container==玩家 Inventory 且 index>=9），见偏离说明 2。
6. **逐槽 try/catch 而非整个容器一个**：简报明说"坏 mod 槽不杀整个响应"；粒度做到槽级——坏槽出 `item:null + note`（note 是该槽字段），好槽照常。遍历 widget 的异常则按简报走整树 fallback（rects 保留已收集几何）。
7. **in_game 字段**：简报 DECIDE 项照"简单"方案——无论有无世界都报 screen + widgets（标题屏也是屏），加 `in_game` 布尔供大脑区分（镜像 getStats 惯例）。`mc.level != null` 判定（与 getStats/worldQuery 的 player==null||level==null 门同源）。
8. **坐标基准如实文档化（不照搬简报措辞）**：简报写 "slot x,y can be fed directly to mouseMove-then-click"——不准确：mouseMove 的**入参**是窗口像素，**返回值** gui_scaled 才与本工具同基。README/javadoc 写明换算配方（任一次 mouseMove 响应里 delivered px / gui_scaled 即得比例，或 `gui * screenWidth / guiScaledWidth`）。见偏离说明 1。

## 实现要点 / API 笔记（全部对照反编译源逐条核实）

**1.21.1/NeoForge 21.1.248 签名（gradle 缓存 sourcesAndCompiledWithNeoForge_*.jar）：**

1. `Screen.children()` :341 返回 `List<? extends GuiEventListener>`——**带通配符**（简报写的是 `List<GuiEventListener>`），遍历语法无差，赋值给具体类型才需要注意。children 由 addRenderableWidget/addWidget 填充（:219）。
2. `AbstractWidget`：`getX()` :302 / `getY()` :312 / `getWidth()` :213 / `getHeight()` :53 / `getMessage()` :233（返回 Component，`getString()` 取纯文本）；**visible :38 / active :37 是 public 字段**（无 isVisible()）；`isActive()` :251 = `visible && active`。我直接读字段（比 isActive() 信息多）。
3. `EditBox.getValue()` :105 返回 String（文本框内容）。
4. `AbstractContainerWidget extends AbstractWidget implements ContainerEventHandler`（AbstractContainerWidget.java:13；AbstractSelectionList 又继承它）——**一个节点可以既是 widget 又是容器**（滚动列表）：遍历先出节点再递归，两分支不互斥。
5. 容器屏：`AbstractContainerScreen<T>` 的 `menu` :41 是 protected，但类实现 `MenuAccess<T>`，**`getMenu()` 是 public**（MenuAccess.java:14）；NeoForge 补丁加 `getGuiLeft()` :670 / `getGuiTop()` :671（读 leftPos/topPos）。`menu.slots` 是 `public final NonNullList<Slot>`（AbstractContainerMenu.java:47）。
6. **槽不在 children() 树里**：槽从 `menu.slots` 单独渲染；命中测试 `isHovering(slot,...)` = `leftPos+slot.x, topPos+slot.y` 的 16x16 格（:567-580，±1 松量）。所以 widget 树 + 槽表是互补的两份结构。
7. `Slot`：`container` :13（public final Container）、`index` :14（**容器内**索引，非菜单位次）、`x`/`y` :15-16（public final int）、`getItem()` :49、`hasItem()` :53、`isActive()` :95。
8. InventoryMenu（E 键屏，InventoryMenu.java:52-81）实排：1 ResultSlot（container=ResultContainer）+ 2x2 Slot（container=TransientCraftingContainer，**实现 CraftingContainer 接口**）+ 4 ArmorSlot + 27+9 Slot + 1 offhand——**armor/offhand 槽的 container 就是玩家 Inventory，index 36-39/40** → 按 role 规则归 "player"。共 46 槽。
9. `Player.getInventory()` :1429 public（拿玩家 Inventory 实例做 container 同一性比较）；`Minecraft.screen` :361 / `Minecraft.level` public 字段（既有代码已用）。
10. `ItemStack.isEmpty()` :300 先判再取（EMPTY 栈的 getItem() 危险）；`getCount()` :1039；`BuiltInRegistries.ITEM` :144 是 `DefaultedRegistry<Item>` 但 `getKey(T)`（Registry.java:62）对未注册项仍返回 null——照 PerceptionTools 的 BLOCK 用法判 null（出 note "not registered"）。

**坑**：`expectInvalid` 冒烟助手要求 lambda 有返回值，`guiStateParams` 返回 void——用语句 lambda `-> { ...; return null; }` 包裹。Gson `serializeNulls()` 使 `item:null` 如期上线（wire 序列化用 Json.GSON）。

**魔法数字**（常量化并写 README）：widget 上限 512（与 world.query blocks 同值，"世界查询纪律"）、遍历深度上限 12（原版树深 ~3，防 mod 死循环）、槽数无独立上限（原版 <50、扁平不递归，README 注明）。

**线程模型**：WS 线程只做 params 校验 + 错误映射；主线程任务 = 读 screen → 遍历 widget（异常→fallback）→ 若容器屏读槽（逐槽隔离）→ 组装 JSON；latch 10s 超时（最小化窗口 → -32603，与其他工具一致——这是超时路径不是内容路径，简报的"never throw"指屏内容异常）。

## 验证方式

- `gradlew build`（含 check→smokeTest）BUILD SUCCESSFUL，**193/193**：
  - 新增 18 项分布：参数校验 2（空对象通过/带键拒绝）、collector 2（上限内收/512 停 + truncated）、widget 节点 3（字段齐全/空 message 省略 + EditBox text/null message+text 省略且不可见仍上报）、标准形状 1（非容器屏无 slots 字段）、容器形状 + 槽 5（slots 字段/槽字段齐全 note 省略/空槽 null+0/坏槽 item:null+note）、无屏形状 1（恰一字段）、fallback 4（标志+类名+note/rects 只留几何/空 partial 空 rects）、role 2（五角色互异/字符串映射）
  - 既有 175 项一行未改、全绿
- 全部 MC API 调用（children 通配符/字段 vs 方法/getMenu/getGuiLeft/Slot 字段/InventoryMenu 排布/ItemStack 判空）对照反编译源写出，无一处凭记忆
- 真机行为按约束未自测（不启动客户端），风险面收敛进下述清单

## 待真机验证清单（主管验收用）

前置：Python 侧 `BridgeClient` 零改动，`call("getGuiState", {})` 直接调用。

1. **无屏**：游戏内不开任何界面 → `{"screen_open": false}`（恰一个字段，非错误）
2. **开背包**：`input.key {"code":"E"}` → `getGuiState` → `screen_class:"InventoryScreen"`、`in_game:true`、**slots 46 个**：1 result + 4 crafting + 9 hotbar + 32 player（27 主 + 4 盔甲 + 1 副手，均 role=player）、`truncated:false`；背包里放几个物品 → 对应槽 `item:"minecraft:oak_log"` 之类 + `count` 正确，空槽 `item:null, count:0`
3. **role 抽查**：合成格放物品（4 格 role=crafting）、产物格 role=result；hotbar（index 0-8）role=hotbar
4. **槽坐标→点击闭环**：取某槽 gui (x,y)，先用一次 mouseMove 响应推换算比例（delivered px / gui_scaled），换算后 `input.mouseMove(px,py)` + `input.click` → 物品被拿起（evidence jpg 佐证）——这是坐标基准正确性的终极检验
5. **EditBox 文本**：`input.key {"code":"T"}` 开聊天 → `input.text {"string":"hello"}` → `getGuiState` → widgets 里有 `type:"EditBox"` 且 `text:"hello"`（ChatScreen 的输入框）
6. **标题屏**：游戏外/断线时 → `screen_open:true, in_game:false, screen_class:"TitleScreen"`，widgets 含 Button 且 message 非空（"Singleplayer" 等）
7. **箱子**（下一里程碑顺带）：放个箱子右键开 → screen_class:"ChestScreen" 之类，槽 role=container（上半）+ player/hotbar（下半）
8. **最小化**：窗口最小化时调用 → 10s 后 -32603（与其他工具一致的饿死保护）
9. **性能直觉**：背包开着连发 getGuiState（10 次/秒级）→ 帧率无可感知下降（主线程单帧微秒级组装）

## 偏离说明

1. **"可直接喂给 mouseMove" 措辞纠正**：简报称槽坐标 "can be fed directly to mouseMove-then-click"——实际 mouseMove **收窗口像素**、其**返回值** gui_scaled 才与本工具同基；直接喂会点偏（比例=GUI scale，2-4 倍）。README/javadoc 写明换算配方（探测一次 mouseMove 即得比例）。语义不变（同基=gui-scaled），只是把"直接"改成"换算后"。
2. **armor/offhand 归 "player"**：简报期望清单列了 "4 armor" 一项，但采用的 Numen 判定法只有 crafting/result/hotbar/player 四角色（armor 槽 container==玩家 Inventory、index 36-39 → player）。未加 "armor" 角色：避免 vanilla 专属嵌套类知识（InventoryMenu.ArmorSlot）进通用判定；如大脑需要可后续按 index>=36 细分。另补了简报未定义的**非玩家容器槽** role=`container`。
3. **params 校验比 getStats 严**：简报说 "validate params is empty like getStats does"，但 getStats 实际不校验；实现为任何键 → -32602（对齐 screenshot/world.query 纪律）。
4. **共享 latch 而非第三份拷贝**（简报二选一）：PerceptionTools.callOnMainThread private→包私有，1 词、行为零变化；InputTools 的 M2-A 副本未动（避免 churn）。
5. **冒烟规模**：目标 +15-25，实际 +18，总 193。

## 交接须知

- 下一步扩展点：`events.watch`（M3）挂 EventPusher 旁；`look/lookAt` 走动作层 `player.turn`；GUI 自动化（点击槽位序列）= getGuiState 选目标 + mouseMove/click 执行，坐标换算配方在 README；role 细分（armor/offhand）只需在 GuiTools.roleOf 加分支（纯侧 role 常量已在 GuiContracts）
- 已知限制：
  - 自绘 mod 屏可能 widgets 很少甚至为空（不走 widget 树）——fallback/截图兜底
  - message 只有纯文本（Component.getString()，无样式元数据）；text 只覆盖 EditBox（其他自管文本的控件看不到）
  - 快照语义：返回与点击之间屏可能变化（大脑侧自担，事件流的 gui_open/gui_close 可辅助）
  - 槽无独立上限：病态 mod 菜单（数百槽）会让响应变大——现实未见过，出现再加 cap
  - 最小化窗口 10s 超时 -32603（既有保护，非内容错误）
- 关联报告：M1-C（latch 模式/grabScreen/纯逻辑-薄壳方法论）、M2-A（gui_scaled 坐标基/输入原语）、M2-B（gui_open/gui_close 事件）、sirius-technical.md §8.2（fallback 层级语义）
