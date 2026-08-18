# M2-D 工作报告（第 1 部分：look/lookAt + 权限分级 + Python command()）

- 任务：sirius-bridge 两个视角工具（look/lookAt 绝对转视角）、`permission` 配置分级（observe/input_gui/input_world/full）、sirius-brain `BridgeClient.command()` 聊天/命令编排
- 日期：2026-08-18
- 状态：完成（代码全部落地并双端验证；真机行为待主管验收，见清单）
- 验收：`gradlew build` 全绿（smokeTest **241/241**，原 200 + 新 41）；sirius-brain `pytest -q` **193 passed**（原 191 + 新 2）；未部署（约束：不 deploy 不 commit）

## 交付物

- `sirius-bridge/.../LookTools.java`（新增）：look/lookAt 的 MC 薄壳——主线程 latch 读写、null player 优雅回 `{"in_game":false,"looked":false}`、旋转写入复刻 vanilla `Entity.lookAt` 语句序列（setYRot/setXRot + yRotO/xRotO 同步 + setYHeadRot）
- `sirius-bridge/.../LookContracts.java`（新增，纯逻辑）：冻结 schema 参数校验（yaw ±180 / pitch ±90 边界含端点）、`rotationTowards()`（vanilla lookAt 公式的纯复刻，类 javadoc 记推导）、`wrapDegrees()`（Mth.wrapDegrees 纯复刻）、三个结果组装
- `sirius-bridge/.../PermissionContracts.java`（新增，纯逻辑）：Tier 枚举（parse 大小写不敏感/trim）、Action 枚举（INPUT/LOOK）、`allows()` 决策矩阵、`deniedRegardlessOfScreen()` 预检判定、`deniedMessage()`
- `sirius-bridge/.../InputGuard.java`（新增，从 InputTools 内部类提取为顶层包私有类）：+tier 字段；InputTools/LookTools 共享同一实例
- `sirius-bridge/.../InputTools.java`：四工具前置加 tier 预检（observe 预 Flight 拒绝，不烧令牌）；主线程任务首行加屏幕门（input_gui/input_world 按任务内实时 `mc.screen` 判定），拒绝时任务**返回** `PermissionDeniedMarker`（非抛出，避免映射成 -32603），handler 转 -32012 + `INPUT_DENIED reason=permission tier=...` 审计行；registerAll 签名改收 InputGuard
- `sirius-bridge/.../BridgeConfig.java`：新键 `permission`（字符串，默认 "full"，大小写不敏感，非法回退默认+note，save 自动补进旧 toml + 注释）
- `sirius-bridge/.../Json.java`：新错误码 `-32012 PERMISSION_DENIED`
- `sirius-bridge/.../BridgeServer.java`：构造器建共享 `InputGuard`，`LookTools.registerAll(tools, guard)` 挂载
- `sirius-bridge/src/test/java/io/sirius/bridge/SmokeMain.java`：+41 项冒烟（look 校验 17、lookAt 校验 3、旋转数学 8（含手算对拍与全域界内）、wrapDegrees 1、结果组装 3、权限矩阵 7、config permission 5），总 **241/241**
- `sirius-brain/sirius_brain/bridge/client.py`：`command(text, settle=0.5, timeout=None)`——T(84)→0.4s→input.text→0.3s→ENTER(257)→settle；返回最后一步 result，任一步失败抛 BridgeError；模块级常量 `GLFW_KEY_T/GLFW_KEY_ENTER`
- `sirius-brain/tests/test_bridge_client.py`：+2 用例（出站帧顺序 T→text→ENTER + 返回 ENTER 结果；普通聊天无斜杠同路径 + -32012 错误透传）
- `sirius-bridge/README.md`：状态行、config 块（permission）、帧表 +look/lookAt、"The look tools (M2-D)" 与 "Permission tiers (M2-D)" 章节（矩阵）、错误码 -32012、冒烟计数 241、项目布局、Next steps
- `sirius-brain/README.md`：BridgeClient 用例 +command()、测试清单补一行

## 关键决策与理由

1. **look 走动作层直写而非事件层注入（对 M2-A 原则的记录在案的偏离）**：视角转动没有"人类等价"的 GLFW 回调入口——人动鼠标产生的是增量，且增量路径双重门在 `isWindowActive()`（M2-A2 已证），失焦即失效。绝对视角只能直写旋转字段；写入语句序列逐行复刻 vanilla `Entity.lookAt(Anchor, Vec3)`（Entity.java:3129-3140）：setYRot/setXRot（NaN 防护 setter）→ `yRotO/xRotO` 同步（否则下一渲染帧从旧角度插值出残影）→ `setYHeadRot`（头身不脱节一帧）。LocalPlayer 下一 tick 经 `sendPosition()` 自动发 PosRot 包同步服务器（LocalPlayer.java:233 起逐行核实）——无需任何包工作。
2. **lookAt 数学 = vanilla 公式原样**：眼位 `getEyePosition()`（Entity.java:1577，= feet + eyeHeight，即 EYES anchor）；`yaw = wrapDegrees(deg(atan2(dz, dx)) - 90)`、`pitch = wrapDegrees(-deg(atan2(dy, horizontal)))`、distance = 欧氏距离。这与 `calculateViewVector` 的逆一致（类 javadoc 记推导），冒烟用 +Z/+X/-Z/正上/正下/3-4-5 对角六个手算用例钉死。退化情形（目标=眼位）得 yaw -90/pitch 0——与 vanilla 自身 lookAt 的 atan2(0,0)=0 行为一致，无害。
3. **权限判定点：屏幕态门在主线程任务内，observe 预 Flight**：屏幕态每 tick 可变，WS 线程预检必竞态。规则拆两段：`deniedRegardlessOfScreen`（observe 对一切、input_gui 对 look）→ WS 线程立即 -32012（不烧令牌、不进主线程）；屏幕相关组合（input_gui/input_world 对 input.*）→ 任务首行读 `mc.screen` 判定。拒绝时任务**返回 marker 而非抛异常**——latch 把任务内异常映射成 -32603，语义错误。input.key/text/mouseMove 在 input_gui 下的无屏按键会流向 KeyMapping/世界（=GUI 外动作），故按"无屏即拒"的统一规则处理；input.click 沿用其既有 `mc.screen != null` 分支。
4. **InputGuard 提取为顶层类并加 tier**：M2-A 交接即预留此扩展点。一个实例由 BridgeServer 构造、InputTools 与 LookTools 共享——预检顺序（validate → enabled(-32011) → tier(-32012) → rate(-32010)）在所有动作工具一致。纯决策逻辑放 `PermissionContracts`（无 MC 类），冒烟可测全矩阵。
5. **look 也受 input_enabled 总闸（-32011）但不进令牌桶**：转视角是"动作"（改变世界交互状态+发包），总闸语义保守覆盖；但令牌桶针对的是事件层注入洪泛，look 是一次字段写，且 M4 瞄准需要高频 look 不应挤占 input.* 配额——不加限频，记入报告。
6. **默认 full 与 M2-A 逐字节兼容**：新键只增不改；full 分支在所有判定里直接放行，input.* 四工具的代码路径与拒绝优先级（enabled → rate）在 full 下不变。
7. **command() 用整数键码**（84/257）而非 "T"/"ENTER" 键名：冻结 schema `input.key.code` 声明的是 integer，键名是 Mod 侧扩展；mock 按 schema 严格校验，字符串会 -32602（测试首跑即抓到）。时序常量（0.4s/0.3s/settle 0.5s）与 docstring 说明 /give 需服务器往返+容器同步（Mindcraft CE 模式）。

## 实现要点 / API 笔记

**1.21.1 API（全部对照反编译源逐行核实）**：

1. `Entity.lookAt(Anchor, Vec3)` Entity.java:3129——本任务数学与语句序列的权威来源（见上）；`Mth.wrapDegrees` Mth.java:190：`f=x%360; if(f>=180) f-=360; if(f<-180) f+=360`——**180° 归到 -180**（区间 [-180,180)），冒烟首跑抓到我按 [-180,180] 的错误预期，已修正为 vanilla 行为。
2. `getEyePosition()` = `(getX(), position.y+eyeHeight, getZ())`，与 EYES anchor（EntityAnchorArgument）完全一致。
3. `setYRot`:3432 / `setXRot`:3444——NaN 即丢弃并 logAndPauseIfInIde；`yRotO/xRotO` public 字段 :170-171。
4. `LivingEntity.setYHeadRot`:3011——纯字段写（yHeadRot），客户端本地设置无副作用。
5. `LocalPlayer.sendPosition()`:233——`isControlledCamera()` 下 yaw/pitch 变化即发 Rot 包；我们只写字段，同步全自动。

**线程/错误模型**：look/lookAt 复用 `PerceptionTools.callOnMainThread`（包私有 latch，10s 超时 → -32603）；权限拒绝是**正常返回**的 -32012 错误帧；`INPUT_DENIED reason=permission tier=<t> method=<m> [screen=<b>]` 审计行（预检无屏幕态则省略 screen=）。

## 验证方式

- `gradlew build`：smokeTest **241/241**（M1-C 45 + M2-A 66 + M2-A2 8 + M2-B/C 81 + M2-D 41）。新增覆盖：look/lookAt 参数校验含端点值（±180/±90 收、越界拒）、旋转数学六个手算用例 + 360° 采样界内断言、wrapDegrees 边界（180→-180）、结果组装三形态、权限矩阵 4×2×2 全组合 + 预检判定 + parse + 消息、config permission 默认/往返/大小写/非法回退。
- `uv run pytest -q`：**193 passed**（原 191 + command 2：出站顺序断言经 `_send` 包装记录真实出站帧；错误透传用 mock 剧本 -32012）。
- 冻结 schema 零改动（git status 确认 sirius-brain/schema 与 mock 未动）；未 commit 未 deploy。

## 待真机验证清单（主管验收用）

1. **look 改视角**：screenshot → `look {"yaw":90,"pitch":0}` → screenshot，两图对比视角转过 90°（yaw 方向符合 F3：0=南(+Z)、90=西(-X)、-90=东(+X)）；服务器可见朝向同步（多人/旁观验证可选）
2. **lookAt 对已知方块**：站在已知坐标（F3 看 eyeball 位），`lookAt {"x":…,"y":…,"z":…}` → screenshot 确认准星压在目标上；`distance` 与手测距离一致
3. **插值无残影**：连续 `look`（如每 200ms 一次转 30°）→ 截图无"斜滑"（yRotO/xRotO 同步生效）
4. **标题屏**：`look`/`lookAt` → `{"in_game":false,"looked":false}`（非错误）
5. **observe 档**：toml 改 `permission = "observe"` 重启 → `input.key`/`look`/`lookAt` 均 -32012、审计行 `INPUT_DENIED reason=permission tier=observe`；`screenshot`/`getStats`/`getGuiState`/`events.subscribe` 照常
6. **input_gui 档**：无屏时 `input.key E` → -32012；开背包后（先手动/用 observe 外方式）`input.key`/`input.click` 放行；`look` 仍 -32012
7. **input_world 档**：无屏 `input.key W` 放行、`look` 放行；开屏后 `input.click` → -32012（任务内实时判定）
8. **full 默认回归**：不改 config（旧文件自动补 `permission = "full"`）→ 四个 input.* 与 M2-A 行为一致、look/lookAt 可用
9. **command() 全链路**（真 Mod）：`await client.command("/give @s diamond 1")` → 背包 1 秒内出现钻石；`command("hello")` 走普通聊天；中文命令（charTyped 码点路径）
10. **死亡态 look**（可选）：死亡时 look 返回 looked:true 但服务器朝向可能不同步——记录实际行为

## 交接须知

- 下一步扩展点：M3 `events.watch`（EventPusher 已是唯一 emit 点）；M4 寻路用 lookAt + input.key W 组合；权限分级若要按 token/连接区分，`PermissionContracts.allows` 已是纯函数，改传参即可
- 已知限制：
  - look 不进令牌桶（一次字段写，且 M4 需高频瞄准）——若真机发现滥用再入桶
  - lookAt 退化目标（=眼位）得 yaw -90（vanilla 同款，无害）
  - input_gui 档下 key/text/mouseMove 的"无屏即拒"是保守统一规则：理论上 mouseMove 仅移动光标不动作，但屏态判定无法区分"纯移动"，宁严勿松
  - 权限分级是进程级（config），非 per-connection；hello token 单一
  - command() 时序常量（0.4/0.3/0.5s）为经验值，真机如丢字再调
- 关联报告：M2-A（事件层注入方法论/InputGuard 前置）、M2-A2（失焦行为——look 不受窗口焦点影响，动作层无 isWindowActive 门）、M1-C（latch 模式）

## 真机验证结果（2026-08-19，主管执行，D 盘机）

### 部分 1：look/lookAt/command（PASS，一次通过）
- look 0°/180° 双截图 92.4% 像素不同（视角确已转动）；lookAt 头顶 10 格 → yaw/pitch 精确 (-90,-90)
- command("/give apple 1") → 苹果落入 hotbar 首格（getGuiState 确认）

### 部分 2：M2 里程碑收官验收（PASS）
- 全屏（高分辨率）+ armor/offhand 角色修复（46becd8）后重跑 `m2_final.py`
- 链路：/give 原木 → E → getGuiState 定位（role 过滤）→ 坐标换算（scale 0.25 自适应）→ 左键拖原木入格 → 取 4 板入 player 槽 idx=9 → 右键放单板×3+左键 1 → 工作台入包
- **终态双通道确认**：getGuiState 断言 crafting_table@player idx=9 count=1；qwen3.7-plus 高分辨率识图独立确认"工作台在主背包区最左上第一格"（与本轮首次 854x480 下木板/原木混淆形成对照——分辨率建议成立）
- 证据：m2_final_1~4 截图；中断根因记录：首跑木板被点入头盔槽（armor 槽容器=玩家 Inventory 被归 player 角色）→ role 细分修复
- **意义：M2 验收标准"纯脚本重放按 E 开背包→拖木头→合成工作台"达成——项目可行性证明点（四原则之"风险前置"全链路闭环）**

### 部分 3：权限分档实测（PASS，observe 档）
- toml `permission = "observe"` 重启后：screenshot/getStats 照常（只读不禁）；input.key 与 look 均拒绝 **-32012**（消息明示 tier）；审计 `INPUT_DENIED reason=permission tier=observe` 两行留痕
- 验后已改回 `permission = "full"`（下次启动恢复全功能）
- input_gui/input_world 两档未做重启实测（矩阵的纯逻辑侧 241 冒烟覆盖；屏幕态判定路径与 observe 同一代码路径）——留待日常使用中自然覆盖
