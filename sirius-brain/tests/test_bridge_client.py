"""Bridge 客户端对 mock 身体的真实 WebSocket 回环测试。spec §8.2 / §10.1 M1-D。

与 test_mock_bridge.py 同口径：不 mock websockets 库，每条用例在随机端口起真实
mock 服务，用 BridgeClient 走完整回环（客户端真实经历后台接收循环/hello 握手/
id 配对）。另用一个最小裸推送服务（不走 mock 帧分发）覆盖前向兼容与 seq 乱序。
项目未安装 pytest-asyncio，异步场景以 asyncio.run() 驱动。
"""

import asyncio
import json
import logging
import time

import pytest
from websockets.asyncio.server import serve

from sirius_brain.bridge import (
    CODE_CONNECTION_LOST,
    BridgeClient,
    BridgeConfig,
    BridgeError,
    BridgeState,
)
from sirius_brain.mock import (
    MockBridgeServer,
    MockScript,
    ScriptedTask,
    ScriptedToolResponse,
)
from sirius_brain.protocol import EventLevel, TaskFinishedStatus, TOOL_PARAMS


async def _wait_until(predicate, timeout: float = 3.0) -> bool:
    """轮询等待条件成立（事件回调在接收循环里触发，测试侧异步观察）。"""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


class _RawPushServer:
    """最小裸 WebSocket 推送服务：客户端一连上就按序发送预置帧（不走 mock 分发逻辑）。

    用于注入 mock 不会产生的帧：未知 type（前向兼容）、乱序 seq 的 notification。
    """

    def __init__(self, frames: list[str]) -> None:
        self._frames = frames
        self._server = None
        self.host = "127.0.0.1"
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    async def start(self) -> "_RawPushServer":
        self._server = await serve(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "_RawPushServer":
        return await self.start()

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _handle(self, ws) -> None:
        for frame in self._frames:
            await ws.send(frame)
        await ws.wait_closed()


class _RecordingServer:
    """最小裸 WebSocket 回显记录服务：记录客户端发来的全部消息（验证出站帧顺序用）。"""

    def __init__(self) -> None:
        self.received: list[dict] = []
        self._server = None
        self.host = "127.0.0.1"
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    async def start(self) -> "_RecordingServer":
        self._server = await serve(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "_RecordingServer":
        return await self.start()

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _handle(self, ws) -> None:
        async for raw in ws:
            try:
                self.received.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                self.received.append({"_unparsed": str(raw)})


class TestConnect:
    def test_capabilities_roundtrip(self):
        """连接 → capabilities()：能力清单与 T1 TOOL_PARAMS 一致 + 协议版本。"""

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    info = await client.capabilities()
            assert info.protocol_version == "1.0"
            assert {cap.name for cap in info.capabilities} == set(TOOL_PARAMS)
            assert all(cap.input_schema for cap in info.capabilities)

        asyncio.run(scenario())

    def test_state_callback_transitions(self):
        """状态回调：CONNECTING → CONNECTED →（close）DISCONNECTED。"""
        states: list[BridgeState] = []

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                client = BridgeClient(
                    server.url, on_state_change=lambda s, d: states.append(s))
                async with client:
                    assert client.connected
            assert states[0] is BridgeState.CONNECTING
            assert states[1] is BridgeState.CONNECTED
            assert states[-1] is BridgeState.DISCONNECTED

        asyncio.run(scenario())

    def test_first_connect_failure_raises_clear_error(self):
        """首连失败：立即抛 BridgeError（不自动重试），错误信息含目标地址。"""

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                url = f"ws://127.0.0.1:{server.port + 1}"  # 无人监听的端口
                client = BridgeClient(BridgeConfig(url=url, connect_timeout=2.0))
                with pytest.raises(BridgeError) as excinfo:
                    await client.connect()
                await client.close()
            assert excinfo.value.code == CODE_CONNECTION_LOST
            assert url in excinfo.value.message

        asyncio.run(scenario())

    def test_call_without_connect_raises(self):
        async def scenario():
            client = BridgeClient("ws://127.0.0.1:1")
            with pytest.raises(BridgeError):
                await client.call("getStats", timeout=1.0)
            await client.close()

        asyncio.run(scenario())


class TestToolCalls:
    def test_scripted_result_roundtrip(self):
        """剧本工具调用：result 原样返回。"""

        async def scenario():
            script = MockScript(tools={
                "getStats": ScriptedToolResponse(result={"health": 6, "food": 20}),
            })
            async with MockBridgeServer(script, port=0) as server:
                async with BridgeClient(server.url) as client:
                    result = await client.call("getStats", timeout=3.0)
            assert result == {"health": 6, "food": 20}

        asyncio.run(scenario())

    def test_unscripted_tool_generic_ok_with_params(self):
        """未编排方法：mock 回通用成功，params 被 echo（id 配对由客户端完成）。"""

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    result = await client.call(
                        "look", {"yaw": 10.0, "pitch": -5.0}, timeout=3.0)
            assert result == {"ok": True, "method": "look",
                              "echo": {"yaw": 10.0, "pitch": -5.0}}

        asyncio.run(scenario())

    def test_scripted_error_raises_bridge_error(self):
        """剧本错误分支：BridgeError 携带线上 code/message/data。"""

        async def scenario():
            script = MockScript(tools={
                "input.text": ScriptedToolResponse(
                    error={"code": -32000, "message": "GUI 未打开"}),
            })
            async with MockBridgeServer(script, port=0) as server:
                async with BridgeClient(server.url) as client:
                    with pytest.raises(BridgeError) as excinfo:
                        await client.call("input.text", {"string": "hi"}, timeout=3.0)
            assert excinfo.value.code == -32000
            assert excinfo.value.message == "GUI 未打开"

        asyncio.run(scenario())

    def test_unknown_method_not_found(self):
        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    with pytest.raises(BridgeError) as excinfo:
                        await client.call("warp", timeout=3.0)
            assert excinfo.value.code == -32601

        asyncio.run(scenario())

    def test_invalid_params_error_with_details(self):
        """参数不过 JSON Schema：-32602 + 校验明细挂在 BridgeError.data。"""

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    with pytest.raises(BridgeError) as excinfo:
                        await client.call("input.text", {}, timeout=3.0)
            assert excinfo.value.code == -32602
            assert excinfo.value.data  # 校验明细列表非空

        asyncio.run(scenario())

    def test_timeout_and_connection_still_usable(self):
        """超时抛 TimeoutError；迟到回包被安全忽略，连接可继续用。"""

        async def scenario():
            script = MockScript(tools={
                "screenshot": ScriptedToolResponse(
                    result={"tier": "full", "image_b64": "..."}, delay_ms=400),
            })
            async with MockBridgeServer(script, port=0) as server:
                async with BridgeClient(server.url) as client:
                    with pytest.raises(TimeoutError):
                        await client.call("screenshot", {"tier": "full"}, timeout=0.15)
                    await asyncio.sleep(0.45)  # 等迟到回包到达并被忽略
                    result = await client.call("getStats", timeout=3.0)
            assert result["ok"] is True

        asyncio.run(scenario())


class TestCommand:
    """M2-D command() 编排：T → text → ENTER 的出站顺序与结果/错误语义（对 mock 回环）。"""

    def test_command_sends_t_text_enter_in_order(self):
        """出站帧顺序必须严格是 input.key T(84) → input.text → input.key ENTER(257)；
        返回值为最后一步（ENTER）的 result（mock 未编排工具回通用成功，params 被 echo）。
        code 用整数键码——冻结 schema input.key.code 声明的是 integer。"""

        async def scenario():
            sent: list[dict] = []
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    original_send = client._send

                    async def recording_send(frame):
                        sent.append(json.loads(frame.model_dump_json()))
                        await original_send(frame)

                    client._send = recording_send
                    result = await client.command("/give @s diamond 1", settle=0.05)
            assert [f["method"] for f in sent if f.get("type") == "request"] == [
                "input.key", "input.text", "input.key"]
            key_frames = [f for f in sent if f.get("method") == "input.key"]
            assert key_frames[0]["params"] == {"code": 84}      # GLFW T
            assert key_frames[1]["params"] == {"code": 257}     # GLFW ENTER
            text_frames = [f for f in sent if f.get("method") == "input.text"]
            assert text_frames[0]["params"] == {"string": "/give @s diamond 1"}
            assert result["ok"] is True            # 最后一步 input.key 的通用成功
            assert result["method"] == "input.key"
            assert result["echo"] == {"code": 257}

        asyncio.run(scenario())

    def test_command_plain_chat_and_error_propagation(self):
        """无斜杠前缀按普通聊天同样处理（时序一致）；任一步被身体拒绝即抛 BridgeError。"""

        async def scenario():
            script = MockScript(tools={
                "input.key": ScriptedToolResponse(
                    error={"code": -32012, "message": "permission_denied: observe"}),
            })
            async with MockBridgeServer(script, port=0) as server:
                async with BridgeClient(server.url) as client:
                    with pytest.raises(BridgeError) as excinfo:
                        await client.command("你好，世界", settle=0.05)
            assert excinfo.value.code == -32012
            assert "permission_denied" in excinfo.value.message

        asyncio.run(scenario())


class TestHello:
    def test_token_hello_interops_with_mock(self):
        """token hello 不破坏与 mock 的互通：mock 回 -32600 未知帧错误，
        客户端按 best-effort 记录为 ignored，后续 RPC 一切正常。"""

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url, token="s3cret-token") as client:
                    hello = await client.wait_hello(timeout=3.0)
                    assert hello is not None
                    assert hello.status == "ignored"
                    info = await client.capabilities()
                    result = await client.call("getStats", timeout=3.0)
            assert info.protocol_version == "1.0"
            assert result["ok"] is True

        asyncio.run(scenario())

    def test_no_token_skips_hello(self):
        """未配置 token：不发 hello（hello_result=no-token），调用照常。"""

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    hello = await client.wait_hello(timeout=1.0)
                    result = await client.call("getStats", timeout=3.0)
            assert hello is not None and hello.status == "no-token"
            assert result["ok"] is True

        asyncio.run(scenario())

    def test_hello_is_first_outbound_message(self):
        """回归：即便 connect() 一返回就发 RPC，hello 也必须是首条出站消息。"""

        async def scenario():
            async with _RecordingServer() as server:
                async with BridgeClient(server.url, token="t0") as client:
                    # 裸记录服务不回包——capabilities 超时即可，出站顺序已被记录
                    with pytest.raises(TimeoutError):
                        await client.capabilities(timeout=0.3)
                frames = server.received
            assert frames, "客户端没有发出任何帧"
            assert frames[0].get("type") == "hello"
            assert frames[0].get("token") == "t0"
            assert frames[0].get("protocol_version") == "1.0"
            assert any(f.get("method") == "capabilities/list" for f in frames[1:])

        asyncio.run(scenario())


class TestTaskFrames:
    def test_task_finished_callback_with_special_task_id(self):
        """send_task → task_finished 回调：task_id（含特殊字符）原样回传，status 枚举化。"""
        special_id = 'T-42/綺麗 💎 "quoted" & <tag>'
        received: list = []

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    @client.on_task_finished
                    def on_finished(frame):
                        received.append(frame)

                    tid = await client.send_task("挖一组铁矿石", task_id=special_id)
                    assert tid == special_id
                    assert await _wait_until(lambda: len(received) >= 1)

        asyncio.run(scenario())
        frame = received[0]
        assert frame.task_id == special_id
        assert frame.status is TaskFinishedStatus.OK

    def test_task_id_defaults_to_uuid(self):
        received: list = []

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    client.add_task_finished_handler(received.append)
                    tid = await client.send_task("合成工作台")
                    assert await _wait_until(lambda: received)
            assert tid != ""
            assert len(tid) == 32  # uuid4().hex
            assert received[0].task_id == tid

        asyncio.run(scenario())

    def test_all_five_task_finished_statuses(self):
        """五态枚举全覆盖：ok/failed/interrupted/superseded/timeout 均以枚举送达。"""
        received: list = []

        async def scenario():
            rules = [ScriptedTask(match=f"[{s.value}]", status=s, text=f"t-{s.value}")
                     for s in TaskFinishedStatus]
            async with MockBridgeServer(MockScript(task_rules=rules), port=0) as server:
                async with BridgeClient(server.url) as client:
                    @client.on_task_finished
                    def on_finished(frame):
                        received.append(frame)

                    for status in TaskFinishedStatus:
                        await client.send_task(f"任务 [{status.value}]",
                                               task_id=f"tid-{status.value}")
                    assert await _wait_until(lambda: len(received) >= 5)

        asyncio.run(scenario())
        assert {frame.status for frame in received} == set(TaskFinishedStatus)
        for frame in received:
            assert frame.task_id == f"tid-{frame.status.value}"


class TestEvents:
    def test_notification_dispatch_with_monotonic_seq(self):
        """事件推送：handler 收到帧，seq 严格递增（0,1,2）。"""
        got: list = []

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    @client.on_event("fire")
                    def on_fire(frame):
                        got.append(frame)

                    await server.push_notification("fire", {"source": "creeper"},
                                                    EventLevel.CRITICAL)
                    await server.push_notification("fire")
                    await server.push_notification("fire", {}, EventLevel.WARNING)
                    assert await _wait_until(lambda: len(got) >= 3)

        asyncio.run(scenario())
        assert [frame.seq for frame in got] == [0, 1, 2]
        assert got[0].event == "fire"
        assert got[0].data["level"] == "CRITICAL"

    def test_subscribe_events_convenience(self):
        """subscribe_events 便捷封装：params 经协议模型构造，mock echo 回显。"""

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    result = await client.subscribe_events(["chat", "health"],
                                                           timeout=3.0)
            assert result["ok"] is True
            assert result["method"] == "events.subscribe"
            assert result["echo"]["types"] == ["chat", "health"]
            assert "min_level" not in result["echo"]  # exclude_none

        asyncio.run(scenario())

    def test_wildcard_handler_receives_all(self):
        got: list = []

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                async with BridgeClient(server.url) as client:
                    client.add_event_handler("*", got.append)
                    await server.push_notification("chat")
                    await server.push_notification("weather")
                    assert await _wait_until(lambda: len(got) >= 2)

        asyncio.run(scenario())
        assert {frame.event for frame in got} == {"chat", "weather"}


class TestForwardCompatAndSeq:
    """对 _RawPushServer（不走 mock 分发）注入 mock 不会发的帧。"""

    def test_unknown_frame_ignored_and_seq_disorder_tolerated(self, caplog):
        """未知帧类型忽略不致命；seq 乱序告警但事件仍分发；循环存活。

        裸服务在连接建立瞬间就推帧——handler 必须在 connect() 之前注册才能收到
        最早这批事件（客户端 docstring 有此约定）。
        """
        frames = [
            json.dumps({"type": "notification", "event": "odd", "data": {},
                        "timestamp": 1.0, "seq": 5}),
            json.dumps({"type": "totally_unknown", "foo": 42}),      # 前向兼容
            json.dumps({"type": "notification", "event": "odd", "data": {},
                        "timestamp": 2.0, "seq": 3}),                # 乱序（倒退）
            json.dumps({"type": "task_finished", "status": "ok",
                        "task_id": "raw-1", "text": "完成"}),
        ]
        seqs: list[int] = []
        finished: list[str] = []

        async def scenario():
            async with _RawPushServer(frames) as server:
                client = BridgeClient(server.url)

                @client.on_event("odd")
                def on_odd(frame):
                    seqs.append(frame.seq)

                @client.on_task_finished
                def on_finished(frame):
                    finished.append(frame.task_id)

                async with client:
                    assert await _wait_until(lambda: len(seqs) >= 2 and finished)

        with caplog.at_level(logging.WARNING, logger="sirius_brain.bridge.client"):
            asyncio.run(scenario())
        assert seqs == [5, 3]          # 乱序帧仍被分发
        assert finished == ["raw-1"]   # 未知帧没有打断接收循环
        assert any("乱序" in record.getMessage() for record in caplog.records)

    def test_non_json_message_ignored(self):
        """非 JSON 消息只被记录忽略，连接照常存活（RPC 对该裸服务超时，不崩）。"""
        frames = ["это не json"]

        async def scenario():
            async with _RawPushServer(frames) as server:
                async with BridgeClient(server.url) as client:
                    with pytest.raises(TimeoutError):
                        await client.call("getStats", timeout=0.5)

        asyncio.run(scenario())


class TestReconnect:
    def test_auto_reconnect_after_disconnect(self):
        """断线自动重连：RECONNECTING → CONNECTED，之后调用照常工作。"""
        states: list[BridgeState] = []

        async def scenario():
            async with MockBridgeServer(port=0) as server:
                client = BridgeClient(
                    BridgeConfig(url=server.url, reconnect_base_delay=0.05,
                                 reconnect_max_delay=0.1),
                    on_state_change=lambda s, d: states.append(s),
                )
                async with client:
                    assert (await client.call("getStats", timeout=3.0))["ok"] is True
                    await client.simulate_disconnect()
                    assert await _wait_until(
                        lambda: states.count(BridgeState.CONNECTED) >= 2)
                    result = await client.call("getStats", timeout=3.0)
            assert BridgeState.RECONNECTING in states
            assert result["ok"] is True

        asyncio.run(scenario())

    def test_pending_call_fails_on_connection_lost(self):
        """在途请求随断线立刻失败：BridgeError(CODE_CONNECTION_LOST)。"""

        async def scenario():
            script = MockScript(tools={
                "screenshot": ScriptedToolResponse(result={"tier": "full"},
                                                   delay_ms=10_000),
            })
            async with MockBridgeServer(script, port=0) as server:
                client = BridgeClient(BridgeConfig(url=server.url, max_reconnects=0))
                async with client:
                    call_task = asyncio.create_task(
                        client.call("screenshot", {"tier": "full"}, timeout=30.0))
                    await asyncio.sleep(0.05)  # 确保请求已发出
                    await server.close()       # 服务端断开
                    with pytest.raises(BridgeError) as excinfo:
                        await call_task
                    await client.close()
            assert excinfo.value.code == CODE_CONNECTION_LOST

        asyncio.run(scenario())


class TestBridgeConfig:
    """配置装载（同步纯逻辑，不起服务）。"""

    def test_from_json_file(self, tmp_path):
        path = tmp_path / "bridge.json"
        path.write_text(json.dumps({
            "url": "ws://localhost:7001",
            "token": "秘密-token",
            "request_timeout": 5,
            "max_reconnects": None,
        }, ensure_ascii=False), encoding="utf-8")
        config = BridgeConfig.from_json_file(path)
        assert config.url == "ws://localhost:7001"
        assert config.token == "秘密-token"
        assert config.request_timeout == 5.0
        assert config.max_reconnects is None

    def test_from_json_file_rejects_unknown_keys(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text('{"uurl": "ws://x"}', encoding="utf-8")
        with pytest.raises(ValueError, match="未知字段"):
            BridgeConfig.from_json_file(path)

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("SIRIUS_BRIDGE_URL", "ws://localhost:9999")
        monkeypatch.setenv("SIRIUS_BRIDGE_TOKEN", "env-token")
        monkeypatch.setenv("SIRIUS_BRIDGE_REQUEST_TIMEOUT", "1.5")
        monkeypatch.setenv("SIRIUS_BRIDGE_MAX_RECONNECTS", "none")
        config = BridgeConfig.from_env()
        assert config.url == "ws://localhost:9999"
        assert config.token == "env-token"
        assert config.request_timeout == 1.5
        assert config.max_reconnects is None

    def test_from_env_empty_token_means_none(self, monkeypatch):
        monkeypatch.setenv("SIRIUS_BRIDGE_TOKEN", "")
        assert BridgeConfig.from_env().token is None

    def test_invalid_url_rejected(self):
        with pytest.raises(ValueError, match="ws://"):
            BridgeConfig(url="http://127.0.0.1:8765")

    def test_with_overrides_skips_none_and_rejects_unknown(self):
        base = BridgeConfig()
        assert base.with_overrides(url=None, token="t").url == base.url
        with pytest.raises(ValueError, match="未知配置字段"):
            base.with_overrides(nope=1)
