# M1-D 工作报告

- 任务：Python BridgeClient（大脑连接身体的统一入口，双轨同构）
- 日期：2026-08-18
- 状态：完成
- 验收：29 条新测试（累计 191 绿）；CLI 对 mock 实测；M1-E 对真 Mod 实测

## 交付物

- `sirius_brain/bridge/client.py`：BridgeClient（连接监督/重连/hello/RPC/NEKO 帧/事件分发）、BridgeError、BridgeState、合成错误码 -32000/-32001/-32002
- `sirius_brain/bridge/config.py`：BridgeConfig dataclass（from_json_file/from_env `SIRIUS_BRIDGE_*`/with_overrides）
- `sirius_brain/bridge/__main__.py`：CLI 冒烟（--url/--token/--timeout/--config/--wait/-v；配置优先级 CLI > JSON > 环境变量 > 默认）
- `tests/test_bridge_client.py`：29 条（mock 真实回环 + 裸记录服务）

## 关键决策与理由

- **hello 同步发送**（监督循环内、就绪信号后才放行 RPC）——曾用 create_task 有竞态：事件循环先唤醒等待 connect() 的调用方，首个 RPC 抢在 hello 之前出站；回归测试 test_hello_is_first_outbound_message 断言出站首帧 type=hello
- hello 回应 best-effort 等待（不阻塞调用）；mock 回 -32600 → 归类 ignored，互通无损
- websockets 17 的 connect() 默认带内建重连 → 显式 reconnect_delays=None 关闭，断线策略完全自管
- 断线时在途请求立即以 BridgeError(CODE_CONNECTION_LOST) 失败，不悬挂

## 实现要点

- RPC uuid id 配对；迟到/无主响应安全忽略（debug 日志）
- 事件按名分发（`*` 通配）、seq 乱序仅告警；未知帧类型忽略+记录（前向兼容）
- 本机连未监听端口静默丢包不回 RST：首连失败延迟=connect_timeout（默认 10s），CLI 错误信息已含提示

## 验证方式

pytest 191 绿；CLI 对 mock：能力协商 12 项、hello ignored、事件回放 seq 0-5；M1-E 对真 Mod：hello acked、12 能力、-32601、task_finished 回调

## 交接须知

- 下一步扩展点：M2 脚本重放直接用 BridgeClient.call("input.key",...)；大脑循环（M3）以此为唯一身体接口
- 已知限制：单连接（大脑多任务共享一个客户端实例）
- 关联报告：M0-T2（mock 行为契约）、M1-B（真身体）、M1-E（双轨实证同构）
