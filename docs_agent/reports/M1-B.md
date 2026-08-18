# M1-B 工作报告

- 任务：Bridge Mod 内 WebSocket 服务端（localhost + token 握手 + 能力协商 + 帧分发骨架）
- 日期：2026-08-18
- 状态：完成
- 验收：build 通过部署；进程内协议冒烟 19/19；真实 Python BridgeClient 互通 9/9；主管代码审阅（loopback/常数时间比较实证）

## 交付物

- `BridgeServer.java`：WS 服务端（127.0.0.1 绑定、hello/token 状态机、帧分发、审计）
- `BridgeConfig.java`：config/sirius_bridge.toml（port/token；首启生成 64 位 hex token）
- `AuditLog.java`：logs/sirius_bridge.log（UTF-8 追加）
- `Capabilities.java`：从 classpath schema/ 组装 12 项能力
- `ToolRegistry.java` / `ToolContext.java`：工具注册表（新增工具零改分发器）+ 主线程编排
- `Json.java` / `SiriusBridge.java`（入口：首个无 overlay 的 ClientTickEvent.Post 启动，GameShuttingDownEvent 关闭）
- build.gradle：Java-WebSocket 依赖（jarJar）+ `syncToolSchemas`（从 ../sirius-brain/schema 单向同步进 jar）

## 关键决策与理由

- **Java-WebSocket 1.5.7（jarJar 内嵌）而非 Netty**：核对生产客户端库清单——MC 1.21.1 不带 netty-codec-http，Netty 的 websocketx 不可用；手写 RFC 6455 过重。单 jar 只依赖 slf4j
- token 常数时间比较（MessageDigest.isEqual）+ 10s 看门狗 + 首帧强制 hello（违规 1008 关闭）
- 能力协商**从冻结 schema 组装**：syncToolSchemas 构建期同步——协议改了忘了同步，构建产物直接更新，无手工步骤

## 实现要点

- 线程模型：WS 线程解析校验分发；游戏状态一律 `Minecraft.getInstance().execute(...)`（ToolContext.onMainThread 封装）；conn.send() 跨线程安全
- hello 状态机 per-connection synchronized authenticate() 消除与看门狗竞态
- **真 bug 修复**：Gson 默认丢 JsonNull → 响应缺 `"result":null` 与冻结帧格式不符 → serializeNulls()（冒烟测试抓到的）
- MDG 坑：additionalRuntimeClasspath 真实配置名是 `<runName>AdditionalRuntimeClasspath`（反编译插件确认），且须在 neoForge{} 块之后

## 验证方式

gradlew build；进程内冒烟 19 项（token/握手/能力/错误码/task 占位/审计）；M1-D Python 客户端互通 9 项；M1-E 真机

## 交接须知

- 下一步扩展点：新工具 → 实现 Tool 可挂载接口 + `XxxTools.registerAll(tools)`（见 PerceptionTools 模式）
- 已知限制：权限分级与输入限频未实现（M2 范围）；事件推送通道未实现（M2）
- 关联报告：M0-T3（schema 来源）、M1-C（第一批工具）、M1-E（真机）
