# M4 进度锚点（实现子代理，防中断）——**已收尾，终稿见 M4.md**

Spec: `docs_agent/session/2026-08-20-M4.md`（权威）。范围红线：不做记忆/危险记忆/认知接口（遵守）。

## 最终状态

- 交接报告：`docs_agent/reports/M4.md`（终稿，含全部验证数字与偏差说明）
- pytest **339 passed**（311+28）；gradlew **smoke 353 passed**（350+3）
- 真机：换气/死亡×3/低血/逃离（完整 PASS）/等级切换（同路径）/entities category/width/Baritone 200 格 全部 PASS；着火+脱困环境受限（无作弊无火源/无卡位点），单测覆盖
- 白天逃离实测：末影人 0.47 格触发 → #stop + 反向 8 格 + 完整简报（未还击）
- 游戏现跑新 jar（PID 3240，从原进程 WMI 提取启动命令重启）；令牌文件已删

## 任务清单（全部完成）

- [x] T1 bridge：entities 载荷加 `category` + `width`（纯增字段，协议 1.2 不动）
- [x] T2 brain：ReflexLevel/切换命令/instincts 联动/ReflexScheduler+BodyState
- [x] T3 brain：七条反射
- [x] T4 brain：behavior_log flush + urgent 通道
- [x] T5 真机（见上）
- [x] T6 交接报告 M4.md（session/PROGRESS 门禁留给主管收尾）

## 关键设计决定（边做边记）

- 事件订阅：单订阅槽替换语义（BridgeServer.ClientSession.subscription 单值）→ 一次 subscribe 全部类型 `["chat","death","fire","health_low","drown"]`，**不带 min_level**（CRITICAL 过滤会把 INFO 的 chat 滤掉）；危险判定靠事件类型本身
- 眼在水中：getStats 无此字段（实测字段：health/food/saturation/air/xp/position/dimension/game_mode/effects/alive）→ air<300 时才用 world.query(blocks, filter=water) 查眼位方块（省钱：空气满时不查）
- on_fire：仅 CRITICAL fire 事件置位（getStats 读不到）；反射撤离完成后清位、等下一条事件再触发
- 危险半径：entities 载荷加 width（纯增字段），危险= width/2+1.5（Numen Menace 简化）
- preempt：`request_preempt(reason)` 扩展 request_stop 语义；end_reason 新增 "preempt"，不播报（反射自己上报）
- 等级切换：仅内存（重启回 LoopConfig.reflex_level 默认 self_preserve）

## 状态日志

- 2026-08-20：开工，上下文读完（spec/loop/primitives/client/PerceptionTools/ToolContracts/EventPusher/tests/mock）
- 2026-08-20 T1 完成：`ToolContracts.EntityFact` 加 `category`+`width` 字段（null/0 兜底旧构造器）、`PerceptionTools.worldQuery` 从 `EntityType.getCategory().name()`（小写）与 `getDimensions(pose).width()` 读取、`filterEntities` 纯增输出；冒烟断言 3 条。gradlew build 全绿：**smoke 353 passed（350→353）**
- 2026-08-20 T2/T3/T4 完成（brain 侧）：
  - 新文件 `sirius_brain/agent/reflexes.py`：ReflexLevel（observer/self_preserve/guard 预留）+ LEVEL_SWITCH_WORDS/instincts_section/match_reflex_level_command + BodyState/ThreatFact + ReflexScheduler（0.5s 轮询、按注册序单候选、等级门控、danger_handler、采样：getStats 2Hz + air<300 才查眼位水 + 每秒实体采样）+ 七条反射（death/health_low/fire/flee/breath/unstuck/speaking_look）
  - `loop.py`：END_PREEMPT + request_preempt/inject_urgent（排队 drain，防线程竞态）+ run() 并行启动调度器 + busy 任务门 + 订阅扩展为 chat+四 danger 事件（不带 min_level）+ 等级切换命令（人类-only，不入队）+ instincts 节进系统提示 + 〔本能反应〕/〔紧急〕注入 + LoopClient.command 开注视窗口 + _execute_tool 行走原语置 movement_active
  - `config.py`：LoopConfig.reflex_level（默认 self_preserve）/reflex_poll_interval（0.5）+ 校验
  - `fakeworld.py`：mob_entities（带 category/width）+ stats_override + key_presses 记录 + entities 载荷带 category/width（掉落物按真语义 misc/0.25）
  - 新文件 `tests/test_reflexes.py`：28 用例（等级框架/危险采样/换气×3/脱困×2/撤离×3/低血+死亡×3/逃离/注视×2/等级切换×4/L0 门控）
- 2026-08-20 pytest 全绿：**339 passed（311→339）**；修过的坑：_execute_tool 误置 ok=False（编辑事故）、位置窗 maxlen 按轮询间隔定容、死亡反射无 #stop 信号改等 urgent 入队
- 2026-08-20 真机验收（旧 jar → 新 jar 两阶段）：
  - 游戏是集成服务器单机世界（玩家 Sirius_test，作弊关闭）；曾最小化——PowerShell ShowWindow 恢复后 mouseMove 坐标才有效（screenWidth=0 时点击全部无效）
  - **等级切换✓**（同路径 _apply_reflex_level：L0↔L1 切换 + L2 预留拒绝，三条播报均上游戏聊天）
  - **溺水换气✓**（min_air=217：眼位水检测→SPACE×4→air 回满→"换气完成"简报；零 VLM）
  - **死亡反射✓×3**（broadcast"死亡位置约(x,y,z)…不会自动重生"进游戏聊天；死亡屏保持打开=确不自动重生）
  - **低血反射✓**（被骷髅射到 1.86：事件→#stop→"警报：血量只剩…"→urgent 排队）
  - **新 jar 部署+重启✓**：deploy.cmd + 从运行进程 WMI 提取启动命令（令牌只落 %TEMP%/m4_restart，用后可删）；PID 3240；entities 载荷 live 带 category（monster/misc/water_ambient）+ width
  - **flee wire 级✓**（怪进入危险半径 min_d=0.77：#stop（游戏日志"ok canceled"）+ 反向 8 格 #goto；两次均被骷髅射死打断在撤离途中，事后知会行未及写出——单测覆盖完整路径）
  - **Baritone 200 格✓**（199 格 77s，终点距目标 1.2 格，途中零误触发）
  - **着火✗ 环境受限**：单机无作弊（/setblock 不可用）且 64 格内无岩浆/火——反射逻辑由单测覆盖，触发通道是 M2-B 已验证的 CRITICAL fire 事件
  - 真机暴露的产品缺陷已修：breath/unstuck/flee/speaking_look 加 dead 门控（溺死尸体按 SPACE 18 次刷简报的教训）；修复后 28 用例重跑全绿
- 待办：交接报告 M4.md + session 收尾；清理探针脚本与临时令牌
