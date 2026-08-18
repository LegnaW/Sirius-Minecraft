# Sirius · 天狼星

Minecraft AI 陪玩：给 AI 一个**真正的 Minecraft 客户端**当身体，Python 后端大脑通过 WebSocket 指挥它——看它看的画面、替它动鼠标键盘。目标是"陪你进任何服务器玩的 AI 队友"：共同视觉体验、Mod 世界可看可点、对服务器就是个普通玩家。

```
sirius-brain（Python 大脑）══ WebSocket ══▶ sirius-bridge（NeoForge Mod，跑在真客户端里）
     规划器/执行器/记忆/Mock身体                  眼：截图/状态/世界查询  手：输入注入(M2)
```

## 仓库结构

| 目录 | 是什么 |
|---|---|
| `sirius-brain/` | Python 后端大脑：协议权威（pydantic）、mock 假身体、BridgeClient |
| `sirius-bridge/` | NeoForge 客户端 Mod（Java 21，MC 1.21.1）：AI 的眼与手 |
| `docs_human/` | **给人读**：[overall.md](./docs_human/overall.md) 全局技术文档（从一段话到贴源码的细节设计） |
| `docs_agent/` | **给开发 agent 读**：设计权威、开发手册、进度锚点、历史决策、任务报告 |

## 快速了解

- 5 分钟掌握全貌 → [`docs_human/overall.md`](./docs_human/overall.md)
- 为什么这样设计 → [`docs_agent/sirius-design.md`](./docs_agent/sirius-design.md)
- 接口与协议规格 → [`docs_agent/sirius-technical.md`](./docs_agent/sirius-technical.md)
- 开发环境与流程 → [`docs_agent/DEVELOPMENT.md`](./docs_agent/DEVELOPMENT.md)

## 当前状态

**M0（协议冻结）+ M1（眼睛）完成**，含真机验收（HMCL 客户端 + Python 大脑端到端贯通）。下一步 M2（手）：输入注入四原语与事件推送。进度详情 → [`docs_agent/PROGRESS.md`](./docs_agent/PROGRESS.md)。

## 开发

```sh
# 大脑（Python 3.11+，uv 管理）
cd sirius-brain && uv sync && uv run pytest

# 身体（JDK 21）
cd sirius-bridge && gradlew build   # 含 45 项进程内冒烟
```

License: Apache-2.0
