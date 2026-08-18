# M1-A 工作报告

- 任务：sirius-bridge 对齐 NeoForge 21.1.248 + 建立部署脚本
- 日期：2026-08-18
- 状态：完成
- 验收：21.1.248 下 build 通过；mods 目录有且仅有一个 sirius_bridge jar；deploy.cmd 幂等（两连跑验证）

## 交付物

- `gradle.properties`：`neoforge_version` 21.1.233 → 21.1.248（对齐 HMCL 测试客户端 1.21.1-Sirius 的 NeoForge）
- `deploy.cmd`：build（带 localhost:9674 代理参数）→ 删 mods 旧 sirius_bridge jar → 复制新 jar（排除 -sources/-javadoc）→ 输出文件名；构建失败退出码 1
- `README.md`：版本表 + 部署用法

## 关键决策与理由

- 对齐 248 而非让客户端降 233：客户端是用户环境，源码迁就运行时
- 部署用 cmd 脚本而非 gradle task：跨项目路径（sirius-bridge → .minecraft）不适合进 build 逻辑

## 实现要点

- mods 目录：`.minecraft\versions\1.21.1-Sirius\mods\`（HMCL 版本隔离模式，gameDir 在版本目录内）
- deploy.cmd 纯 ASCII 内容（避免代码页问题）

## 验证方式

两次连续执行 deploy.cmd：第二次 build UP-TO-DATE（20s）、旧 jar 正确移除重部署、目录始终唯一 jar

## 交接须知

- 下一步扩展点：M2+ 每次 Java 改动后跑 `deploy.cmd` 即可热部署（客户端需重启才加载新 jar）
- 已知限制：deploy 不自动重启游戏
- 关联报告：M0-T4（骨架）、M1-B/C（被部署的内容）
