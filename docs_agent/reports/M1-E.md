# M1-E 工作报告（真机集成验收）

- 任务：M1 收官——Python 大脑连真 Minecraft 客户端，验收"眼睛"
- 日期：2026-08-18
- 状态：PASS（一次通过）
- 验收：技术规格 §10.1 M1 行"Python 客户端连上并截图存盘，画面正确"

## 环境

- HMCL 3.16.3 实例 `1.21.1-Sirius`（MC 1.21.1 / NeoForge 21.1.248 / MSA LegnaW9473 / 854x480）
- mods：sirius_bridge-0.1.0.jar（M1-C 产物）
- Python：`.venv\Scripts\python.exe` + BridgeClient（M1-D）

## 验收一：标题屏（2026-08-18）

| 步骤 | 结果 |
|---|---|
| token 握手 | PASS（token 自动从 config/sirius_bridge.toml 读取） |
| 能力协商 | 12 项，protocol 1.0 |
| getStats / world.query | `in_game: false` 优雅降级 |
| screenshot | 854x480 q80 未降级 72KB → VLM 确认完整中文标题画面（含 NeoForge 21.1.248 角标） |

## 验收二：in-game（用户开本地世界，创造模式）

| 步骤 | 结果 |
|---|---|
| getStats | 完整快照：health 20 / creative / (7.5, 72, -5.5) / overworld |
| world.query(blocks r=6) | count=512 **truncated=true**（防爆炸保护按设计生效）；地形合理：泥土 235/石头 186/草方块 76/短草 9/**煤矿石 6** |
| world.query(entities r=32) | 1 实体 = 玩家本人，坐标与 getStats 一致 |
| screenshot | 第一人称草地平原、创造 HUD、钻石镐在手、准星清晰（VLM 确认） |

## 证据

- `docs_agent/m1-evidence/m1e_screenshot.jpg`（标题屏）
- `docs_agent/m1-evidence/m1e_ingame.jpg`（in-game）
- 脚本：`m1e_acceptance.py` / `m1e_ingame.py`（可重复执行）

## 交接须知

- **M1 全链路结论**：协议冻结 → mock → 真 Mod → 真机，端到端贯通；"大脑不绑死身体"首次实战成功（同一 BridgeClient 对 mock/真 Mod 零改动）
- M2 注意：世界是创造模式（演示"合成工作台"无物资压力）；窗口 854x480 的 GUI 坐标换算以实际分辨率截图为准
- 关联报告：M1-B/C/D（被验部件）
