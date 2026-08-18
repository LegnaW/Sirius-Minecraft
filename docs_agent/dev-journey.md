# Sirius 开发旅程（dev-journey）

> 按时间回答"为什么走到今天这一步"。每条只写有据可查的事实，来源标注在括号内。
> 状态类信息（做到哪了）看 [PROGRESS.md](./PROGRESS.md)；设计原理看 [sirius-design.md](./sirius-design.md)。
> 最后更新：2026-08-18（文档基建轮）

## 前传：为什么不直接用 Mindcraft CE

Mindcraft CE 已经是能干的 MC AI 玩家（对话/挖矿/合成/战斗/现场写代码），但架构是**单层平铺**——一个模型包办一切。四个核心痛点（sirius-design.md §0）：

1. 一心不可二用：挖矿时玩家聊天要排队
2. 上下文互相污染：任务输出和闲聊混在一起
3. 成本错配：回"哈哈"和制定打怪策略用同一个模型
4. 能力封顶：感知/动作封死在原版协议，Mod 世界对它隐形

目标定为：**随游玩时间自主成长、能玩 Mod 服务器、始终能和玩家自然对话的 AI 伙伴**。

## 2026-08-17：两个奠基性裁决

### 身体 = 真客户端（不是 mineflayer，也不是 Numen 服务端假玩家）

完整评估了三具身体（sirius-technical.md §8.1）：

| 方案 | 死因 |
|---|---|
| mineflayer 协议模拟 | 无屏幕；Mod 内容不可见不可操作 |
| Numen 服务端假玩家 | 无像素（陪玩缺共同视觉语境）；服务端必须装 Mod，带不进别人的服务器 |
| **真客户端（选定）** | 代价：每 bot 一客户端的资源开销 + 寻路自研 |

决定性论据都指向"陪玩"产品本质：①陪玩 bot 必须看得到玩家皮肤、感受 boss 的视觉冲击——聊天共享一块视觉语境；②"带 AI 朋友进任意服务器"要求对服务器=普通玩家、零安装。意外之喜：真客户端同时解决 Mod 渲染正确性与 Mod GUI 可看可点。

架构定为：机器人拥有独立 MC 客户端（装同款 Mod）+ 自研 Bridge Mod（眼与手）+ 后端大脑。

### 中断取消 PAUSE

原设计三档中断（CANCEL/PAUSE/DEFLECT），吸收 Numen 经验后砍掉 PAUSE（技术规格 §8.4）：恢复 = 重派重算（带新信息重算，背包收获即进度），无检查点簿记、无状态不一致风险。模型层直接拒绝 pause（M0-T1）。

## 2026-08-18：一日完成 M0 + M1

### 早晨的三个决定

- **命名 Sirius**：双星隐喻（天狼星 A=规划器/大模型，天狼星 B=执行器/小模型，一明一暗都是恒星）；GitHub 仓库 Sirius-Minecraft（避免撞名）。
- **sirius-brain 用 Python**：N.E.K.O 参考代码同语言、MCP/LanceDB 原生 Python、团队维护成本（决定性）。代价：Mindcraft CE（JS）只能"读逻辑、Python 重写"；mineflayer（纯 JS）并行轨失效 → **mock 优先**——M0 把 Python mock 身体做厚（回放+剧本），大脑全部逻辑对 mock 开发，M3 切真身体。
- **主管模式**：主会话不写代码，拆解/派发/验收全走子代理（与技术规格 §10 分层架构同构）。

### M0 协议冻结（T1→T4）

- **T1** sirius-brain 骨架：uv 工程 + pydantic 协议模型全套。关键点：`interrupt_policy` 无 pause；id 用 str(uuid)。
- **T2** mock bridge：剧本驱动（工具回什么、task 按子串匹配回五态+延迟）——复现失败/超时/乱序等真游戏难造的场景。错误码对齐 JSON-RPC（-32700/-32600/-32601/-32602），后来真 Mod 沿用同一套。
- **T3** schema 冻结：导出 27 个**自包含** draft 2020-12 JSON Schema（$ref 全为同文件片段）——Java 校验库单文件加载零装配。防漂移测试：改模型忘重导出 → CI 红。NEKO 映射文档同步产出。
  - task_id 必须原样回传的教训来自 N.E.K.O 已知限制：out-of-order 完成时按完成序匹配会错误归属。
  - 五态状态表（ok/failed/interrupted/superseded/timeout）冻结，铁律：**失败绝不能报成 ok**。
- **T4** sirius-bridge MDK 骨架：MC 1.21.1 / NeoForge 21.1.x（与本地 Numen 源码版本对齐方便参考）。mods.toml 用 templates 方式注入版本变量。
  - **教训（MIT license）**：MDK 模板默认 license 溜进产物，与仓库 Apache-2.0 矛盾。脚手架元数据必须逐字段核对，不能只看 build 通过。

### M1 眼睛（A→E）

- **A** 版本对齐：NeoForge 21.1.233 → 21.1.248（迁就 HMCL 测试客户端的运行时）；deploy.cmd 幂等部署。
- **B** WS 服务端：选型 **Java-WebSocket 1.5.7（jarJar 内嵌）而非 Netty**——核对生产客户端库清单发现 MC 1.21.1 不带 netty-codec-http；手写 RFC 6455 过重。安全模型：loopback 绑定、token 常数时间比较、10s 看门狗、首帧强制 hello、审计日志。能力协商从冻结 schema 组装（syncToolSchemas 构建期同步）。
  - 真 bug：Gson 默认丢 JsonNull → 响应缺 `"result":null` 与冻结帧格式不符 → serializeNulls()（进程内冒烟抓到）。
- **C** 三感知工具：screenshot（渲染线程 framebuffer 抓取，天然含 GUI——1.21.1 把 HUD/GUI 画进主渲染目标）/ getStats / world.query（512 截断/128 上限）。纯逻辑（ToolContracts/ImageOps）与 MC 薄壳（PerceptionTools）分离 → 不起游戏可测 45 项。
  - 1.21.1 API 坑四则（M2 必读）：GUI 画进主渲染目标；`Minecraft.execute` 最小化时饿死 → latch 必须带超时；NativeImage 小端 ABGR；`Holder.getRegisteredName()`。
- **D** Python BridgeClient：监督循环 + hello 首帧保证（曾有竞态：事件循环先唤醒调用方，首个 RPC 抢在 hello 之前出站 → 回归测试断言出站首帧 type=hello）。
- **E** 真机验收一次通过：token 握手、12 能力协商、未进世界优雅降级、854x480 截图 VLM 确认。**"大脑不绑死身体"首次实战**：同一 BridgeClient 对 mock 与真 Mod 零改动。

### 同日的事故与教训（GBK 乱码）

PowerShell 无编码参数读写 PROGRESS.md → 中文 GBK 双重编码乱码，从会话上下文恢复。此后确立硬性纪律：改中文文档统一用专用文件工具。

### 晚间：文档基建轮

用户确立固定工作方式（brainstorm→spec→门禁循环）与双层文档结构：`docs_agent/`（agent 读，准确完备）+ `docs_human/`（人读，可读性优先）。原 `docs_for_agents/` 与 `docs_agent/` 定位重合，git mv 改名归位（历史保留）；新建 DEVELOPMENT.md / dev-journey.md / session/ / docs_human/overall.md / 根 README。

## 下一步：M2（手）

路线图既定：input.* 四原语 + look/lookAt/getGuiState + 事件订阅推送 + 权限分级与限频。验收 = 纯脚本重放"按 E 开背包→拖木头→合成工作台"——整个项目可行性的证明点。输入注入保真度是四开发原则里"风险前置"的那个风险，M2 就是去验它。
