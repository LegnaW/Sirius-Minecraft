# Sirius 工作进度

> 本文档是**跨会话的状态锚点**：每轮工作结束时更新，新会话从这里恢复上下文。
> 设计内容不写这里（在 [sirius-design.md](../docs_human/sirius-design.md) / [sirius-technical.md](./sirius-technical.md)），这里只记"做到哪了、接下来干什么"。
> 最后更新：2026-08-21

## 当前阶段：M4.1（反射层独立存活修复轮）**已完成**——五缺陷修复（命令锁下沉/移动转头/chat.send 直发/令牌桶证伪/#pause 配对）+ 分段存活压测 3 PASS·2 DEATH 全归因 + 协议 1.2→1.3；pytest 351 / 冒烟 369 全绿。**等用户反馈决定下一轮**（候选：M5 分层大脑 / L2 战斗 / 进食反射小轮）

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

## M3 完成记录（2026-08-19，会师·最小整机）

- [x] **M3-A**：QwenVLM 客户端完成并验收（原生 tool-calling、b64 图片、直连清代理、重试退避、用量统计、transport 可注入 fake）；AgentConfig 从 local.md env 围栏块加载；pytest 244 绿；真实冒烟 1.17s
- [x] **M3-B**：最小大脑循环完成并验收（chat 指令入口+自回显双重过滤+急停+tool-calling 循环+finish/播报+上下文预算）；mock 双人全流程回归资产入库；pytest 263 绿
- [x] **M3-C 真机验收**：真服务器生存模式三任务——「你好」1 步闭环、「来我这里」11 步闭环（world.query→lookAt→走→验位→finish）、「搜集云杉木」22 步预算用尽中止（方向对没走完）。**最小整机大脑闭环成立**，问题清单见 reports/M3-C.md（P1: hello_ack 未建模/command 丢字/history 未压缩——P0 三项全部由 M3.5 解决）

## M3.5 完成记录（2026-08-20，智能优化轮：运动控制下沉）

核心论点（Numen/Mindcraft 精读共识）：LLM 只做意图层决策，执行下沉确定性代码。spec 见 session/2026-08-20.md，全轮报告 reports/M3.5-*.md。

- [x] **T0b Baritone 前置验证 PASS**：1.21.1 构建 + HMCL 实例安装 + #goto 冒烟（3s 收敛至 2.0 格、位移 18.1 格）；首轮 FAIL 定位为 quickPlay 加载屏竞态（探针时序问题，非 Baritone/bridge 缺陷）→ 落地 walkTo 界面屏障
- [x] **T1（Java 半）world.query filter + input.click hold_ms**：filter 走 registry id/#tag（零硬编码，Numen 手段）、命中最近优先 cap 32 + truncated；hold_ms 0..10000 与 count 互斥；entities 补齐 truncated；冒烟 241→291
- [x] **T2+T4 原语模块 + mock 世界**：`agent/primitives.py`（walk_to/dig_block/collect_block，Numen 式契约话术、看门狗、协作取消≤1s）+ `mock/fakeworld.py`（FakeWorldBridge：假 Baritone/可变方块/#tag filter）；pytest 263→276
- [x] **T3 原语接入大脑**：三工具注册（共 14 工具）+ Numen 式契约描述 + 系统提示重写（原语优先/键鼠兜底+边界契约）+ 滚动状态免费搭车 + 预算 200k→500k + 错误码→建议映射 + 界面屏障；pytest→287
- [x] **T5a 真机原语层验收 6/6**（零 VLM）：filter 契约/walk/dig/collect(3 根 83.2s)/急停 1.49s/性能；修 3 个真机缺陷（徒手 hold 600→3500ms 递增封顶 8000、段等待 hold 完成、遮挡递增 hold）；**发现 world.query 截断排序 bug**（cap 在排序前，铁证：range=4.0 可见 3.71 格目标、range=5.5 截断后反消失）→ Python 侧防御入库；采集目标按裁决改 oak_log（出生区无云杉）
- [x] **T6 bridge dig 智能原语 + 平滑转头 + 截断修复**：dig 动作层监视按住（startDestroyBlock/continueDestroyBlock——**事件层长按被 vanilla 焦点双门控废掉的真机发现**，M2-D look 同构先例）；lookAt turn_speed_deg_s 固定角速度（dig 瞄准 300deg/s）；filtered 扫描改"收集→排序→截断"修 bug；协议 1.1→1.2 三处同步；冒烟→342、pytest→292、真机 5/5（collect 提速 4.9 倍至 16.8s/3 根）
- [x] **T7 掉落物拾取**：entities 载荷补 item 注册名+count；collect_block 挖后拾取（pickup 可配置、只捡挖点 4 格内匹配掉落——多人服礼仪、实体消失=已拾取、skip 防死循环）+ pickup() 方法（未注册 VLM，M4 再议）；冒烟→345、pytest→302、真机 4/4（多人服离线回落单机；死亡屏 getGuiState+坐标换算重生）
- [x] **T5b 直驱验收达标**：问候 1.6s；砍橡木 4 步 8.0s 16k tokens（对比 M3-C 同意图 22 步 212k 预算耗尽——步数 1/5.5、token 1/13，验收线 ≤4 次/≤30k）；完整聊天循环验收待用户进世界
- [x] **架构裁决 4 项**（见下方决策表 2026-08-20 行）：Baritone 集成、操作型功能入 bridge、拾取 VLM 可配置、本地 LM Studio VLM 可用

## M3.6 完成记录（2026-08-20，小补丁轮；代码已提交 783558e，本记录为 M4 收尾时补登——上轮 PROGRESS 漏更）

- [x] **T1 幻觉直答防护**：系统提示新增"观察纪律（防幻觉直答）"节（答案取决于世界状态的问题必须先调感知工具；闲聊/知识问答/引用既有工具结果的汇报豁免——避免"你好"也触发查询）
- [x] **T2 hello_ack 协议建模**：`HelloAckFrame` + 客户端识别（连接日志不再出"忽略无法识别的帧"WARNING）；只进 frames.py 不进 schema 导出（握手帧从不导出），协议版本不动 1.2
- [x] **T3 dig 经验掉落报告 + pickup 工具**：bridge dig broken 后 20 tick 快照 diff 附 `drops`（**经验主义掉落知识，模组方块零硬编码**；挖前 uuid 快照保证别人的掉落绝不混入）；brain `_collect_drops` 优先用 drops 匹配；`pickup` 注册 VLM 工具（注册表 14→15，Numen collect_items 语义+多人服礼仪话术）
- 测试：pytest 302→**311**、冒烟 345→**350**、真机 3/3（穿遮挡聚合 dirt×3、诚实空清单、礼仪实证不捡他人树苗）；报告 reports/M3.6.md（注：session/2026-08-20-M3.6.md 收尾日志仍空，事实以报告与提交信息为准）

## M4 完成记录（2026-08-20，反射层：等级框架 + L0/L1）

核心主张：**脊髓不过大脑**——溺水/着火/危怪/卡位的保命动作在确定性代码里完成，零 VLM token；认知层只收事后简报。spec 见 session/2026-08-20-M4.md，全轮报告 reports/M4.md（含三处偏差与已知限制）。

- [x] **等级框架**：`ReflexLevel` 严格单调能力束（L0 观察 ⊂ L1 自保默认 ⊂ L2 自卫预留枚举位）；切换=聊天"反射等级 观察/自保"人类-only（不进 VLM 工具表，识别即切换+播报）；不持久化（重启回默认）；`instincts_section` 按等级动态生成系统提示节——代码层与认知层唯一同步点
- [x] **调度器**（`agent/reflexes.py`，Numen CompanionBrain 的 asyncio 移植）：0.5s 轮询协程，按注册序问 `can_run` 首个胜出（层内单候选，结构性消灭比较）；三档打断 none（旁路）/cooperative（接管归还，任务不停）/preempt（经 `request_preempt` 掀任务，end_reason=preempt 不播报）；边沿自持（触发窗/冷却/死亡闩+复活重臂）；先归位再宣布（act 结束才写 behavior_log）；等级门控在调度循环、danger 感知不门控（L0 关动作不关感知）
- [x] **L1 七反射**：换气（air≤240 按 SPACE 上浮，失败封顶上报）/脱困（2s 位移<0.75 → 137° 扇形爆发+跳）/撤离（#stop+20 格找水否则反向撤 5 格）/低血（#stop+警报+〔紧急〕）/死亡（报坐标+等玩家指令不自动重生）/危怪逃离（category=monster 进危险半径 width/2+1.5 → 反向 8 格，不还击）/说话注视（播报窗口内 lookAt 最近玩家）
- [x] **事后知会（非记忆）**：behavior_log（deque 纯消息）每轮 VLM 前替换式 flush 一条〔本能反应〕（尾 500 字符）；death/低血走 `inject_urgent` 排队 drain〔紧急〕（任务死了留给下一任务）；零持久化零检索——范围红线（本轮零记忆/零认知接口）遵守
- [x] **bridge**：entities 载荷加 `category`（MobCategory 注册表小写，模组怪自动归 monster）+ `width`（碰撞箱宽，flee 危险半径输入）——纯增字段协议不动 1.2，旧构造器 null/0 兜底
- [x] **真机验收（零 VLM token）**：换气（air 300→217 触发，SPACE×4 回满；困水下封顶上报也实测）/死亡×3（聊天报坐标+"不会自动重生"，死亡屏保持）/低血（health 1.86→#stop+警报+urgent）/逃离（末影人 0.47 格<1.8 半径→#stop+反向 8 格+简报未还击）/等级切换（L0↔L1+L2 拒绝；L0 下 fire 照进认知零动作）/entities payload（monster/misc/water_ambient）/Baritone 200 格（199 格 77s 终点差 1.2 格，途中零误触发）。着火/脱困环境受限未真机（无火源/无卡位点，单测覆盖+触发通道是 M2-B 已验的 CRITICAL 事件）
- [x] **真机暴露并修复**：死亡时 breath/unstuck/flee 不再动作（溺尸按 SPACE 刷 18 次简报教训——dead 门控）；修复后全量重跑绿
- 测试：pytest 311→**339**（+28）、冒烟 350→**353**（+3）

## M4.1 完成记录（2026-08-21，反射层独立存活 + 自然转头修复轮）

核心使命：①机器人零上层指令输入下完全靠反射层**尽量**不死（诚实口径：记录归因而非承诺零死亡）②移动自然转头（消灭固定视角平移）。spec 见 session/2026-08-21-M4.1.md（含用户修正的分段压测验收），全轮报告 reports/M4.1.md + M4.1-progress.md（五缺陷根因/两个预设假设被推翻/压测归因表/计划外第 6 修复 safe_command）。

- [x] **T1 命令竞态（🔴）**：根因两层——探针 raw `BridgeClient` 的 Primitives 与调度器并发（锁只在 LoopClient 级）+ `command()` 时序全靠拍定时数。修复：锁下沉 `BridgeClient.command` 客户端级（锁序 loop→client 无死锁）+ GUI 确认时序（T 后等聊天屏开——被占用拒绝盲发；ENTER 后等关闭——关不掉 ESC 丢弃）。真机 10 条 #stop/#goto 连发 5.19s 零合并、位移 14.9 格
- [x] **T2 移动转头（🔴）**：**TurnController 压制说被证据推翻**（130 格长走审计零旋转写入）——真因是 Baritone（该 standalone 构建）行走自身不转头（WASD 平移）。修复：bridge `MovementLook` 每 tick 按速度矢量方向 300°/s 只写 yaw（显式 turn 优先让位、2° 死区、pitch 不碰、config movement_look 可关）。真机 43 样本 |yaw−运动方向| 中位 0°/P90 8°/max 16°
- [x] **T3 死亡播报（🟡）**：bridge 新工具 `chat.send`（进程内 `ClientPacketListener.sendChat`，死亡屏免疫）+ brain `LoopClient.say`（旧 jar -32601 自动回落）；Death/低血警报改直发（压测实证"低血警报发出前毫秒级死亡、死亡屏吞 T 键"窗口期）。上轮"播报×3"复查结论：M4.md 验的是 wire 层，聊天可见性是时序侥幸。端到端实证：死亡屏开着播报真实进聊天且不自动重生
- [x] **T4 令牌桶（🟡）**：**"长按按 tick 计费"说被证伪**——源码全 `tryAcquire(1)` 按调用计费，真机 1500ms 长按+19 并发 tap 19/19 全过；-32010 真因是演示脚本自身管道化并发。**裁决：无需改码**（反射层 await 串行 ≤2/s 无风险）
- [x] **T5 cooperative×Baritone（🟢）**：`#pause`/`#resume` 配对（仅 movement_active 时 pause；preempt 已 #stop 后 #resume 无害空操作；幂等+失败只记日志）——优于 #stop+不重启（保住 walk 续走语义）。真机 Baritone 回执实证
- [x] **T6 分段存活压测（用户修正验收：分段制+饱和消除饥饿）**：五场景各 120s 零任务消费——火 PASS / 卡位 PASS / 综合混灾 PASS（最低 7.7 存活）；怪群 DEATH t=20s（**设计边界非缺陷**：L1 禁攻击+逃离冷却 6s，反射链全部正确仍死——L2 战斗是真正解）；深水首轮 DEATH t=74s（占空比缺陷：400ms 按/500ms 歇净上浮≈0）→ BREATH_PRESS_MS 400→800 修后回归 PASS（换气×14、最低 4.3 存活）
- [x] **压测引出第 6 修复 safe_command**：低血 #stop 被 GUI 占用拒绝（-32002）抛异常会跳过直发警报与 urgent 注入——preempt 反射 #stop 统一走 `safe_command`（失败只记日志，保命链不被单条命令打断）
- [x] **附带**：着火/卡位为此前"环境受限未真机"的补验（M4 遗留销项）；GUI 重生点击×2；探针 GUI 自动化开 LAN+作弊（世界原作弊关闭）
- 测试：pytest 339→**351**（+12）、冒烟 353→**369**（+16）；协议 1.2→**1.3**（chat.send + getStats yaw/pitch，三处同步）


## M2 完成记录（2026-08-19，D 盘机收口）

- [x] **M2-B 事件订阅推送**：EventPusher 单一事件入口（chat/gui_open/gui_close + 危险态 death/fire/health_low/drown 状态沿+5s 冷却）；events.subscribe per-connection 订阅（原子 seq）；截图推流（~1Hz 采样/6s 节流最新帧待发+边界补发/质量×边长双阶梯 100KB 硬预算/环形 3）；诚实丢弃计数。冒烟 119→175；真机 PASS（10 帧 seq 单调/预算内）。借鉴 N.E.K.O 生产管线（service.py:1037-1307）
- [x] **M2-C getGuiState**：widgets 递归树（512 截断/匿名类走具名超类/children+renderables 双注册表）+ 容器 slots（guiLeft+slot.x 公式 + Numen 式角色分类）；真机首验 2 缺陷（addSlot 覆写 Slot.index→改用 getContainerSlot；盔甲槽归 player 角色致收官首跑木板入头盔槽→armor/offhand 独立角色）回修后 PASS。冒烟 200
- [x] **M2-D look/lookAt+权限+command()**：vanilla Entity.lookAt 原式（setYRot/setXRot+yRotO/xRotO 同步，服务器自动跟随）；权限四级 observe/input_world/input_gui/full（默认 full 向后兼容，-32012+审计）；BridgeClient.command()（T→text→ENTER+500ms 沉降）。冒烟 241/pytest 193
- [x] **M2 里程碑收官验收 PASS**：`m2_final.py` 纯脚本零 LLM——/give→E→getGuiState 定位→坐标换算→拖拽合成→工作台入包；终态双通道确认（结构化断言 + qwen3.7-plus 高清识图独立定位）；证据 m2_final_1~4.jpg
- [x] **权限分档实测 PASS**（observe：感知照常/行为 -32012/审计留痕；验后已回 full）
- [x] **参考项目精读落地**（RULES §3 先找参考）：本机参考项目位于各开发者自己的机器（本仓约定见 `local.md`「参考项目」节；此前误记的 `D:\AI\Project\references\` 为另一开发机的私有路径，公共文档不记本机路径——2026-08-19 修正）；技术规格 §8.3 已按 Numen 现行代码修正（竞价→序数分层）

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
- **sirius-bridge 构建须带 JVM 代理参数**（联网解析 NeoForge 平台/依赖），统一走 `sirius-bridge/deploy.cmd`（构建+部署一体，参数正确）；裸跑 `gradlew build` 在本机网络环境会因不走代理而失败（2026-08-20 M3.5 教训，PROGRESS 既有代理口径的重申）
- **真机 bridge 独占 8765**：真机与离线测试同机并行时，mock/测试服务端一律绑定 `port=0` 随机空闲端口（2026-08-20 M3.5 教训，测试套件已全量迁随机端口）

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
| 2026-08-19 | **NEKO 协议兼容层取消**：N.E.K.O 是独立项目（自有感知/思维链路），降为纯设计参考；任务帧保留在协议但不作兼容承诺 |
| 2026-08-19 | **Bridge=哑管道**：只上报/接受信息，一切处理归 brain；API key 只配 local.md 一遍（排查确认 bridge 无 VLM 代码/无出站 HTTP，无需删码） |
| 2026-08-19 | **反射层归 brain**（原 §8.3 规划在 bridge 轨）：Python 无 LLM 规则消费 M2-B 的 CRITICAL 危险事件；寻路同理 brain 侧 |
| 2026-08-20 | **协议分拆**：sirius-bridge → LGPL-3.0（参考 Numen 实现+借用 Baritone，Numen 为 LGPL-3.0 按传染性要求同协议开源；LICENSE 落 sirius-bridge/）；sirius-brain 保持 Apache-2.0（根 LICENSE 不变）。用户裁决，防纠纷 |
| 2026-08-19 | **M3 方案定稿**：qwen3.7-plus 单模型；原生 tool-calling；结构化感知优先+按需截图；mock 双人先行、真机 LAN 收官 |
| 2026-08-20 | **寻路 = Baritone 集成**（不自研 A*）：#goto/#stop 聊天命令驱动（客户端拦截，不达服务器），真机冒烟 3s 收敛 2.0 格；M4 寻路里程碑收窄为反射层 |
| 2026-08-20 | **操作型功能入 bridge、对 brain 暴露接口**：dig/look 动作层先例（事件层长按被 vanilla 焦点双门控废掉，M2-D 同构）；bridge 边界由"哑管道"两层修订为"感知原语化 + 输入标准化 + 动作层操作原语"三层（sirius-technical §8.3） |
| 2026-08-20 | **拾取行为 VLM 可配置**：collectBlock pickup 参数（默认顺路捡匹配掉落，挖通道/清地形传 false）——意图层决策留 LLM、执行下沉代码 |
| 2026-08-20 | **VLM 可用本地 LM Studio 模型**（reasoning_effort:"none" 为本地关思考唯一有效开关，32k 上下文；部署细节见各机 local.md） |
| 2026-08-20 | **反射等级体系 = 三级严格单调能力束**（L0 观察 ⊂ L1 自保默认 ⊂ L2 自卫预留）：不做每条反射的开关（Numen 删空闸教训）；L2 客户端战斗模块后置独立轮次（接入需同步修订"禁止攻击"安全约束）；升攻击等级人类-only（聊天"反射等级"命令，不进 VLM 工具表）。用户裁决 |
| 2026-08-21 | **分段压测制裁决**（用户修正 M4.1 验收）：连续 10 分钟跑会被饥饿拖死（资源约束非反射层缺陷）——改为五场景各 120s 独立压测 + 场景前 instant_health/saturation 效果消除饥饿变量 + 死亡即记归因（"尽量不死"诚实口径：记录死因而非承诺零死亡） |
| 2026-08-21 | **反射层职责边界声明**（压测归因固化）：职责 = 突发危险的即时自救（溺水/着火/围攻/卡位）；慢性消耗（饥饿/长期低饱食）不在职责内——进食反射记 backlog（检测饥饿+库存有食物自动吃，L1 未来成员，执行器层协作，M4.2/M5 候选） |

## 遗留问题 / 待用户输入

### M4 遗留（本轮新增）

- **L2（guard）战斗模块**：枚举位+instincts 预留节已就位；实现后置独立轮次（客户端战斗是最难件；接入需 ①战斗反射链 ②同步修订"禁止攻击"安全约束 ③切换放行 ④消费 entities health 字段）。**M4.1 压测显性化：这是怪群存活的真正解**——裸装无武器 bot 被 5 只怪轮番贴身时反射链全部正确仍死（L1 禁攻击设计边界+逃离冷却 6s 期间再次贴身），归因见 reports/M4.1.md §2
- **进食反射**（M4.1 新增 backlog）：检测饥饿+库存有食物→自动吃——L1 合理未来成员，属执行器层协作范畴（M4.2/M5 候选）；M4.1 压测以饱和效果消除了该变量
- **多人补验**：等级切换聊天命令端到端（单机无第二玩家，bot 自回显过滤——命令解析单测覆盖、切换执行路径真机已验）+ speaking_look 有对象
- ~~**着火/脱困真机补测**：需火源（开作弊测试世界/岩浆/打火石）与可复现卡位点~~ **已解决（M4.1 T6 压测补验：火 PASS/卡位 PASS——探针 GUI 自动化开 LAN+作弊后点火与造坑）**
- **调度分层候选**：health_low/death 的"上报"与撤离的"行走"同层串行（单候选）——追击场景低血上报被撤离行走阻塞 ~10s（M4.1 safe_command 已消"上报被打断"风险，阻塞本身仍在）
- **flee 危险半径对远程怪保守**（width/2+1.5 是近战近似，骷髅 30 格外射死不触发；低血反射是实际兜底）
- **fire 再触发依赖新 CRITICAL 事件**（bridge 5s 边沿冷却）：撤离后仍在烧且无新边沿不会二次撤离
- **等级切换不持久化**（重启回 self_preserve）；**反射保护以 agent 进程常驻为前提**（真机两次死亡发生在探针间隙——调度器未挂，产品语义内）
- **NEKO 危险记忆/认知接口**：M4 范围红线未做；behavior_log（现 deque 纯消息）是将来危险记忆的天然挂点（M6+）
- 次项未动：Baritone 注入路径优化（#goto 聊天往返 ~1.3s/次）
- 真机运维：游戏窗口最小化时 mouseMove 全废（screenWidth=0）——恢复/重启前先 PowerShell `ShowWindow(SW_RESTORE)`

### 老项（跨轮）

- 旧 `mindcraft-ce-develop\` 本体（E 盘已有副本的原始位置）是否删除待定
- M5 前决策：执行器①是否引入 Numen 式确定性任务
- 模型选型（规划器/执行器具体型号）未定
- 无 filter 的 world.query 仍是 v1.0 cap-before-sort 截断语义（brain 侧已防御、filter 路径已修）→ 观察项，根治属协议语义变更
- input.click 事件层长按 / input.mouseMove 转头仍需窗口焦点（vanilla 门控无开关；"AI 播放"标准部署=游戏窗口保持前台，sirius-bridge/README 已记）
- 多人服在线复验待用户开服（T7 单机 4/4，礼仪约束由代码+单测钉死）
- collect 16.8s/3 根的下一步提速在换斧/执行器（M5），不在本层
- ~~本地 VLM 观察类问题不调工具直接幻觉作答~~ **已解决（M3.6 T1 观察纪律节）**
- ~~掉落物匹配为精确 id（需掉落表知识）~~ **已解决（M3.6 T3 dig 实测 drops 取代掉落表，模组方块零硬编码）**
- ~~pickup() 原语未暴露给 VLM~~ **已解决（M3.6 T3 注册，工具表 14→15）**
- ~~hello_ack 未建模~~ **已解决（M3.6 T2 HelloAckFrame）**
- ~~T5b 完整聊天循环验收待用户进世界~~ **已过（M3.5 收官时用户实测通过，见 M3.6 session 背景）**
