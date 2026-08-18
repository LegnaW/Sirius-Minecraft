# 环境路径模板 → 复制为仓库根 `ENV.local.md` 后填写

> **用法**：`cp docs_agent/ENV.template.md ENV.local.md`（或复制内容），填入你机器的真实值。
> `ENV.local.md` 已被 .gitignore 排除，**永不入库**——各开发者独立维护，可写敏感信息但建议只写路径不写密钥。
> 填完后：把 `DEVELOPMENT.md` 中的通用命令与本文件路径对照，确认每条命令在你机器上可用。

## 系统

- OS：（如 Windows 11 / macOS 15）
- 默认 shell：（cmd / PowerShell 5.1 / pwsh / bash——影响语法与编码行为）
- 代码页/locale：（中文 Windows = GBK，见 DEVELOPMENT.md 编码坑）

## 网络

- 代理：（如 `http://localhost:9674`；无则写"无"）
- pip/uv 镜像：（如清华源 + NO_PROXY 特殊处理）

## 工具链（不在 PATH 的写全路径）

- git：`（如 C:\Program Files\Git\cmd\git.exe）`
- Python（sirius-brain venv）：`（如 <仓库>\sirius-brain\.venv\Scripts\python.exe）`
- uv：`（全路径，或写"未安装"）`
- JDK：`（版本 + 路径；gradle 需要 21+）`
- gradlew：`（sirius-bridge\gradlew.bat，一般无需全路径）`

## 测试客户端（Minecraft 身体）

- 启动器：（HMCL / 官方 / 其他）
- 实例目录：`（如 <仓库>\.minecraft\versions\<版本名>\）`
- mods 目录：`（实例目录\mods\）`
- Bridge 配置/token：`（实例目录\config\sirius_bridge.toml）`
- Bridge 审计日志：`（实例目录\logs\sirius_bridge.log）`
- WS 地址：`（默认 ws://127.0.0.1:8765）`

## 本机特有的坑

- （自由记录：如"xxx 命令不存在"、"xx 路径与 DEVELOPMENT.md 不同"）
