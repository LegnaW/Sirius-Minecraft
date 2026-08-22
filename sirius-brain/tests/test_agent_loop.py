"""M3-B 最小大脑循环测试：工具注册表 / 自回显过滤 / 急停 / 任务循环 / mock 双人全流程。

全部离线：
- VLM 用 ScriptedVLM（QwenVLM 子类，按剧本回放 tool_calls/文本/错误，零网络）
- bridge 用 MockBridgeServer + 剧本 JSON（tests/fixtures/two_player_scene.json，回归资产）
  跑真实 WebSocket 回环——双人剧本：另一玩家 Alex 的 chat 事件（sender uuid ≠ bot）
  驱动 bot 完整任务（感知→决策→工具→游戏内播报）
- 真实 key / 真实 VLM / 真机不出现在任何测试（M3-C 由主管验收）

项目未安装 pytest-asyncio，异步场景以 asyncio.run() 驱动（与 test_bridge_client 同口径）。
"""

import asyncio
import copy
import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from sirius_brain.agent import (
    COLLECT_BLOCK_TOOL,
    DIG_BLOCK_TOOL,
    FINISH_TOOL,
    PARTIAL_DONE_PREFIX,
    PICKUP_TOOL,
    STATUS_PREFIX,
    STOP_REPLY_TEXT,
    WALK_TO_TOOL,
    AgentConfig,
    AgentLoop,
    SelfEchoFilter,
    ToolCall,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
    UnknownToolError,
    VLMConfig,
    VLMError,
    VLMResponse,
    VLMUsage,
    QwenVLM,
    bridge_error_hint,
    default_registry,
    match_self_uuid,
)
from sirius_brain.agent.config import BridgeConfig, LoopConfig
from sirius_brain.agent.loop import MAX_TOOL_RESULT_CHARS
from sirius_brain.bridge import BridgeClient
from sirius_brain.mock import MockBridgeServer, MockScript
from sirius_brain.protocol import NotificationFrame

FIXTURE_SCENE = Path(__file__).parent / "fixtures" / "two_player_scene.json"
BOT_UUID = "11111111-1111-1111-1111-111111111111"
ALEX_UUID = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------- 测试基建


def resp_tools(*calls: tuple[str, dict], usage_total: int = 0) -> VLMResponse:
    """预排一条"模型发起 tool_calls"的 VLM 响应。"""
    return VLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id=f"call_{i}", name=name, arguments=dict(args))
                    for i, (name, args) in enumerate(calls)],
        usage=VLMUsage(prompt_tokens=usage_total, completion_tokens=0,
                       total_tokens=usage_total),
    )


def resp_text(text: str, usage_total: int = 0) -> VLMResponse:
    """预排一条纯文本 VLM 响应。"""
    return VLMResponse(content=text, finish_reason="stop",
                       usage=VLMUsage(prompt_tokens=usage_total, completion_tokens=0,
                                      total_tokens=usage_total))


class ScriptedVLM(QwenVLM):
    """假 VLM：按剧本回放（第 N 次调用回剧本第 N 条，耗尽后重复最后一条）。

    - ``captured``：每次调用收到的 messages（深拷贝，防循环侧后续裁剪影响断言）
    - ``error_from=(index, exc)``：第 index 次起抛给定异常（VLMError 路径）
    - ``block_gate=(index, threading.Event)``：第 index 次调用阻塞到事件置位
      （急停测试用它把任务"冻结"在某个检查点之间）
    """

    def __init__(self, script: list[VLMResponse], *,
                 error_from: tuple[int, Exception] | None = None,
                 block_gate: tuple[int, threading.Event] | None = None) -> None:
        super().__init__(VLMConfig(api_key="sk-test"),
                         transport=lambda request: {"status": 200, "body": {}},
                         retry_base_delay=0.0)
        self.script = list(script)
        self.error_from = error_from
        self.block_gate = block_gate
        self.captured: list[list[dict]] = []
        self.tools_seen: list[list[dict]] = []

    def chat(self, messages, tools=None, tool_choice=None, **extra):  # noqa: ANN001,ANN202
        self.captured.append(copy.deepcopy([dict(m) for m in messages]))
        if tools:
            self.tools_seen.append(copy.deepcopy(list(tools)))
        index = len(self.captured) - 1
        if self.error_from is not None and index >= self.error_from[0]:
            raise self.error_from[1]
        if self.block_gate is not None and index >= self.block_gate[0]:
            if not self.block_gate[1].wait(timeout=10.0):
                raise AssertionError("测试的 block_gate 事件从未置位")
        return self.script[min(index, len(self.script) - 1)]


class RecordingMock(MockBridgeServer):
    """记录全部入站 request 帧的 mock（断言 wire 真值：bot 到底发了什么）。"""

    def __init__(self, script: MockScript, **kwargs) -> None:
        super().__init__(script, **kwargs)
        self.requests: list[dict] = []

    async def _on_message(self, conn, raw) -> None:  # noqa: ANN001
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            msg = None
        if isinstance(msg, dict) and msg.get("type") == "request":
            self.requests.append({"method": msg.get("method"),
                                  "params": msg.get("params")})
        await super()._on_message(conn, raw)


def two_player_scene() -> MockScript:
    """双人场景剧本（回归资产 fixture：bot Sirius_Bot + 玩家 Alex）。"""
    return MockScript.from_json_file(FIXTURE_SCENE)


def make_agent(client: BridgeClient, vlm: QwenVLM, *,
               loop_config: LoopConfig | None = None) -> AgentLoop:
    config = AgentConfig(vlm=VLMConfig(api_key="sk-test"),
                         bridge=BridgeConfig(url="ws://127.0.0.1:1"),
                         loop=loop_config or LoopConfig())
    # settle 压到 20ms：command() 的 0.4+0.3s 时序保留，收尾等待缩短（离线 mock 不需要 0.5s）
    return AgentLoop(client, vlm, config, command_settle=0.02)


def spy_commands(client: BridgeClient) -> list[str]:
    """在 client 实例上包一层 command 间谍，记录全部出站文本。"""
    sent: list[str] = []
    real_command = client.command

    async def spy(text: str, settle: float = 0.5, timeout=None):  # noqa: ANN001,ANN202
        sent.append(text)
        return await real_command(text, settle=settle, timeout=timeout)

    client.command = spy  # type: ignore[method-assign]
    return sent


async def say(server: MockBridgeServer, message: str, *,
              sender: str | None = ALEX_UUID, system: bool = False) -> None:
    """另一玩家（默认 Alex）在游戏聊天发一条消息（bot 经 chat 事件收到）。"""
    data: dict = {"message": message, "system": system}
    if sender is not None:
        data["sender"] = sender
    await server.push_notification("chat", data)


async def wait_until(predicate, timeout: float = 10.0) -> bool:  # noqa: ANN001,ANN202
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def sent_texts(server: RecordingMock, method: str = "input.text") -> list[str]:
    """bot 经 wire 发出的全部某方法字符串参数（input.text 即聊天/命令文本）。"""
    return [r["params"]["string"] for r in server.requests
            if r["method"] == method and isinstance(r.get("params"), dict)]


# ---------------------------------------------------------------------- 工具注册表


class TestToolRegistry:
    def test_whitelist_and_openai_shape(self):
        registry = default_registry()
        assert registry.names() == [
            "getStats", "getGuiState", "world.query", "screenshot", "lookAt",
            "input.mouseMove", "input.click", "input.key", "input.text",
            WALK_TO_TOOL, DIG_BLOCK_TOOL, COLLECT_BLOCK_TOOL, PICKUP_TOOL,
            "command", FINISH_TOOL,
        ]
        tools = registry.openai_tools()
        assert len(tools) == 15
        for tool in tools:
            assert tool["type"] == "function"
            function = tool["function"]
            assert function["name"] in registry.names()
            assert function["description"]
            assert function["parameters"]["type"] == "object"
        # 工具表发出去的是 QwenVLM.chat 可直接消费的简写/完整形态
        assert tools[0]["function"]["name"] == "getStats"

    def test_primitive_tools_contract_descriptions(self):
        """M3.5：三原语注册在表，描述带 Numen 式契约（受理即执行/失败读建议/超时续做）。"""
        registry = default_registry()
        functions = {t["function"]["name"]: t["function"]
                     for t in registry.openai_tools()}
        walk = functions[WALK_TO_TOOL]
        assert "受理即执行" in walk["description"]
        assert "同参数重发" in walk["description"]          # 超时续走契约
        assert walk["parameters"]["required"] == ["x", "z"]  # y 可选
        dig = functions[DIG_BLOCK_TOOL]
        assert "幂等" in dig["description"]                  # 已空=成功契约
        assert dig["parameters"]["required"] == ["x", "y", "z"]
        collect = functions[COLLECT_BLOCK_TOOL]
        assert "有收获 = 成功" in collect["description"]      # 部分收契约
        assert "#tag" in collect["description"]
        # T7：拾取可配置（默认捡，挖通道/清理地形传 false）
        assert "pickup" in collect["description"]
        assert collect["parameters"]["properties"]["pickup"]["type"] == "boolean"
        assert collect["parameters"]["required"] == ["block_ids", "count"]  # pickup 可选
        ids = collect["parameters"]["properties"]["block_ids"]
        assert ids["minItems"] == 1 and ids["maxItems"] == 16
        assert collect["parameters"]["properties"]["count"] == {
            "type": "integer", "minimum": 1, "maximum": 64,
            "description": "要挖除的目标方块数"}
        # M3.6：pickup 注册在表，Numen collect_items 式契约 + 多人服礼仪话术
        pickup = functions[PICKUP_TOOL]
        assert "掉落物" in pickup["description"]
        assert "多人服礼仪" in pickup["description"]            # 只捡自己活动的掉落
        assert "磁吸" in pickup["description"]                  # 机制说明
        pickup_props = pickup["parameters"]["properties"]
        assert pickup_props["item_ids"]["minItems"] == 1
        assert pickup_props["item_ids"]["maxItems"] == 8
        assert pickup_props["radius"] == {
            "type": "integer", "minimum": 1, "maximum": 32,
            "description": "搜索半径（格），默认 12"}
        assert pickup["parameters"]["required"] == []           # 全部可选（缺省=捡全部）

    def test_primitive_params_validation_client_side(self):
        """原语参数边界在本地 pydantic 就拒绝（不浪费 bridge 往返）。"""
        registry = default_registry()
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, WALK_TO_TOOL, {"x": 1.0}))       # 缺 z
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, DIG_BLOCK_TOOL,
                                         {"x": 1.5, "y": 64, "z": 2}))         # 非整数
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, COLLECT_BLOCK_TOOL,
                                         {"block_ids": [], "count": 1}))       # 空列表
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, COLLECT_BLOCK_TOOL,
                                         {"block_ids": ["a"] * 17, "count": 1}))  # >16 条
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, COLLECT_BLOCK_TOOL,
                                         {"block_ids": ["a"], "count": 65}))   # count 上限
        # M3.6：pickup 参数边界（item_ids ≤8 条、radius 1..32）
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, PICKUP_TOOL,
                                         {"item_ids": ["a"] * 9}))             # >8 条
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, PICKUP_TOOL,
                                         {"item_ids": [], "radius": 12}))      # 空列表
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, PICKUP_TOOL, {"radius": 0}))    # 下界
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, PICKUP_TOOL, {"radius": 33}))   # 上界

    def test_default_registry_cancel_optional(self):
        """default_registry(cancel=None) 向后兼容（独立使用/测试不可取消）。"""
        registry = default_registry()
        assert WALK_TO_TOOL in registry and len(registry) == 15

    def test_parameters_from_frozen_schema_files(self):
        registry = default_registry()
        world_query = registry.get("world.query")
        assert world_query is not None
        assert set(world_query.parameters["required"]) == {"type", "range"}
        assert "$schema" not in world_query.parameters
        screenshot = registry.get("screenshot")
        assert screenshot is not None
        assert screenshot.parameters["$defs"]["ScreenshotTier"]["enum"] == ["full", "crop"]

    def test_unknown_tool_rejected(self):
        registry = default_registry()
        # look 在 schema 产物里但不在 M3 白名单；fly 完全不存在
        for name in ("look", "fly", "events.subscribe", "input.scroll"):
            with pytest.raises(UnknownToolError):
                asyncio.run(registry.execute(None, name, {}))
        assert "look" not in registry

    def test_params_validation_client_side(self):
        registry = default_registry()
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, "lookAt", {"x": 1}))  # 缺 y/z
        with pytest.raises(ValidationError):
            asyncio.run(registry.execute(None, "command", {}))  # 缺 text

    def test_extension_point(self):
        registry = default_registry()

        async def yell(client, args):  # noqa: ANN001,ANN202
            return ToolOutcome(f"YAELL:{args['text']}")

        registry.register(ToolSpec(
            name="yell", description="测试扩展工具",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            handler=yell))
        assert "yell" in registry
        assert any(t["function"]["name"] == "yell" for t in registry.openai_tools())
        outcome = asyncio.run(registry.execute(None, "yell", {"text": "hi"}))
        assert outcome.text == "YAELL:hi"


# ---------------------------------------------------------------------- 自回显过滤 / 自识别


class TestSelfEcho:
    def test_window_with_injected_clock(self):
        now = [0.0]
        flt = SelfEchoFilter(window=5.0, clock=lambda: now[0])
        flt.register("你好")
        assert flt.is_echo("你好", None, None)          # 窗内同文本（sender 未知）
        assert not flt.is_echo("别的消息", None, None)  # 窗内不同文本
        now[0] = 5.1
        assert not flt.is_echo("你好", None, None)      # 窗外放行
        # 重复登记刷新时间窗
        flt.register("你好")
        now[0] = 3.0 + 5.0
        flt.register("再说一次")
        assert flt.is_echo("你好", None, None)          # 5s 窗内仍有效
        now[0] += 5.1
        assert not flt.is_echo("再说一次", None, None)

    def test_uuid_and_other_player(self):
        flt = SelfEchoFilter(window=5.0)
        assert flt.is_echo("任何话", BOT_UUID, BOT_UUID)        # sender=自身
        assert not flt.is_echo("你好", ALEX_UUID, BOT_UUID)     # 已知他人不抑制
        assert not flt.is_echo("你好", ALEX_UUID, None)         # 自身未知也不误伤他人

    def test_match_self_uuid(self):
        stats = {"in_game": True, "position": {"x": 100.5, "y": 64.0, "z": -200.5}}
        entities = {"entities": [
            {"uuid": ALEX_UUID, "position": {"x": 103.0, "y": 64.0, "z": -198.0}},
            {"uuid": BOT_UUID, "position": {"x": 100.9, "y": 64.1, "z": -200.7}},
        ]}
        assert match_self_uuid(stats, entities) == BOT_UUID
        # 全部远于容差 → 识别不出（抑制窗兜底）
        far = {"entities": [
            {"uuid": ALEX_UUID, "position": {"x": 200.0, "y": 64.0, "z": -198.0}}]}
        assert match_self_uuid(stats, far) is None
        # 不在游戏 / 空实体 / 结构异常 → None
        assert match_self_uuid({"in_game": False}, entities) is None
        assert match_self_uuid(stats, {"entities": []}) is None
        assert match_self_uuid(stats, None) is None


# ---------------------------------------------------------------------- chat 入口过滤（单元）


class TestChatInlet:
    def _frame(self, message: str, *, sender: str | None = ALEX_UUID,
               system: bool = False) -> NotificationFrame:
        data: dict = {"message": message, "system": system}
        if sender is not None:
            data["sender"] = sender
        return NotificationFrame(event="chat", data=data,
                                 timestamp=time.time(), seq=0)

    def test_filters_and_enqueue(self):
        async def main() -> None:
            client = BridgeClient("ws://127.0.0.1:1")
            agent = AgentLoop(client, ScriptedVLM([resp_text("x")]),
                              AgentConfig(vlm=VLMConfig(api_key="sk-test")))
            agent.self_uuid = BOT_UUID
            agent._on_chat(self._frame("系统广播", system=True))
            agent._on_chat(self._frame("   "))          # 空白消息
            agent._on_chat(self._frame("我的回声", sender=BOT_UUID))  # 自身 uuid
            agent.echo.register("抑制窗内")
            agent._on_chat(self._frame("抑制窗内", sender=None))      # 窗内同文本无 sender
            assert agent._queue.qsize() == 0
            agent._on_chat(self._frame("丢一块石头给我"))
            assert agent._queue.qsize() == 1
            instruction, seq = await agent._queue.get()
            assert instruction == "丢一块石头给我" and seq == 1
            # 急停词：置标志 + 回话任务在后台执行
            agent._on_chat(self._frame("停下"))
            assert agent._stop_requested
            assert agent._stop_seq == 1
            await wait_until(lambda: not agent._background)  # 后台回话跑完
            # 大小写不敏感的英文急停
            agent._stop_requested = False
            agent._on_chat(self._frame("  Stop "))
            assert agent._stop_requested
            await agent.shutdown()

        asyncio.run(main())


# ---------------------------------------------------------------------- 单任务循环（真回环 mock）


class TestRunTask:
    def test_finish_broadcast_via_command(self):
        """指令→getStats→finish→游戏内播报：finish 的 result 走 command（wire 可见）。"""

        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            vlm = ScriptedVLM([
                resp_tools(("getStats", {})),
                resp_tools((FINISH_TOOL, {"result": "石头丢给你了"})),
            ])
            agent = make_agent(client, vlm)
            try:
                await client.connect()
                run = await agent.run_task("丢一块石头给我")
                assert run.end_reason == "finish"
                assert run.result == "石头丢给你了"
                assert run.tool_names == ["getStats", FINISH_TOOL]
                assert run.steps == 2
                assert "石头丢给你了" in sent                      # 走了 client.command
                assert "石头丢给你了" in sent_texts(server)       # wire 上真发了 input.text
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_max_steps_exhausted_broadcast(self):
        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            vlm = ScriptedVLM([resp_tools(("getStats", {}))])  # 永不 finish
            agent = make_agent(client, vlm, loop_config=LoopConfig(max_steps=3))
            try:
                await client.connect()
                run = await agent.run_task("观察三次")
                assert run.end_reason == "max_steps"
                assert run.steps == 3
                assert run.tool_names == ["getStats"] * 3
                assert any(text.startswith(PARTIAL_DONE_PREFIX) for text in sent)
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_token_budget_exhausted(self):
        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            vlm = ScriptedVLM([resp_tools(("getStats", {}), usage_total=1000)])
            agent = make_agent(client, vlm,
                               loop_config=LoopConfig(max_steps=25,
                                                      max_total_tokens=1500))
            try:
                await client.connect()
                run = await agent.run_task("烧预算的任务")
                assert run.end_reason == "budget"
                assert run.steps == 2            # 第二步后累计 2000 > 1500 即止
                assert run.tokens == 2000
                assert any(text.startswith(PARTIAL_DONE_PREFIX) for text in sent)
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_content_only_response_finishes(self):
        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            vlm = ScriptedVLM([resp_text("我先看看周围再决定。")])
            agent = make_agent(client, vlm)
            try:
                await client.connect()
                run = await agent.run_task("你好")
                assert run.end_reason == "content"
                assert run.result == "我先看看周围再决定。"
                assert "我先看看周围再决定。" in sent
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_vlm_error_ends_task(self):
        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            vlm = ScriptedVLM([resp_text("x")],
                              error_from=(0, VLMError(500, "上游炸了")))
            agent = make_agent(client, vlm)
            try:
                await client.connect()
                run = await agent.run_task("倒霉任务")
                assert run.end_reason == "error"
                assert "VLM 调用失败" in (run.error or "")
                assert any(text.startswith(PARTIAL_DONE_PREFIX) for text in sent)
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_screenshot_image_attached_and_pruned(self):
        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            vlm = ScriptedVLM([
                resp_tools(("screenshot", {"tier": "full"})),
                resp_tools(("screenshot", {"tier": "full"})),
                resp_tools((FINISH_TOOL, {"result": "看完了"})),
            ])
            agent = make_agent(client, vlm)
            try:
                await client.connect()
                run = await agent.run_task("看两眼")
                assert run.end_reason == "finish"
                # screenshot 工具结果文本是占位符，不带图像数据
                assert run.tool_calls[0].text.startswith("[图像已附]")
                # 最后一轮消息里只剩最近 1 张截图图像
                final_messages = vlm.captured[-1]
                image_parts = [
                    part for message in final_messages
                    if isinstance(message.get("content"), list)
                    for part in message["content"]
                    if isinstance(part, dict) and part.get("type") == "image_url"
                ]
                assert len(image_parts) == 1
                url = image_parts[0]["image_url"]["url"]
                assert url.startswith("data:image/jpeg;base64,")  # 魔数嗅探走通
                # 图像挂在 user 消息里（附在 tool 结果之后）
                assert any(
                    message["role"] == "user" and isinstance(message["content"], list)
                    for message in final_messages)
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_truncation_bounds(self):
        long_text = "头" * 2000 + "中" * 8000 + "尾" * 2000
        truncated = AgentLoop._truncate(long_text)
        assert len(truncated) <= MAX_TOOL_RESULT_CHARS
        assert truncated.startswith("头")
        assert truncated.endswith("尾")
        assert "已截断" in truncated
        assert AgentLoop._truncate("短文本") == "短文本"

    def test_rolling_status_replaced_not_accumulated(self):
        """M3.5 滚动状态：每步 VLM 调用前注入一条〔当前状态〕（替换式，不累积历史）。"""

        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            vlm = ScriptedVLM([
                resp_tools(("getStats", {})),
                resp_tools(("getStats", {})),
                resp_tools((FINISH_TOOL, {"result": "完事"})),
            ])
            agent = make_agent(client, vlm)
            try:
                await client.connect()
                run = await agent.run_task("走三步")
                assert run.end_reason == "finish"
                # 每一步发给 VLM 的历史里都恰有一条状态消息（首步也有，搭车从第 1 步开始）
                assert len(vlm.captured) == 3
                for messages in vlm.captured:
                    status = [m for m in messages
                              if m.get("role") == "user"
                              and isinstance(m.get("content"), str)
                              and m["content"].startswith(STATUS_PREFIX)]
                    assert len(status) == 1, "状态消息必须替换而非累积"
                    assert status[0]["content"] == (
                        f"{STATUS_PREFIX}位置(100.5,64.0,-200.5) 生命20 饥饿20 氧气300")
                # 状态消息位于历史末尾（紧跟上一轮工具结果，下一步调用前注入）
                assert vlm.captured[-1][-1]["content"].startswith(STATUS_PREFIX)
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_system_prompt_contains_layered_contract(self):
        """M3.5 系统提示分层契约：原语优先/键鼠兜底/边界契约/安全约束都真实到达 VLM。"""

        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            vlm = ScriptedVLM([resp_text("收到")])
            agent = make_agent(client, vlm)
            try:
                await client.connect()
                await agent.run_task("测试提示")
                prompt = vlm.captured[0][0]["content"]  # 首条 system 消息
                # 任务级原语优先 + 键鼠兜底的分层引导
                assert "任务级原语优先" in prompt
                for name in (WALK_TO_TOOL, DIG_BLOCK_TOOL, COLLECT_BLOCK_TOOL, PICKUP_TOOL):
                    assert name in prompt
                assert "兜底" in prompt
                # 边界契约
                assert "range ≤64" in prompt
                assert "filter 1..16" in prompt
                assert "限频 20/s" in prompt
                assert "hold_ms" in prompt
                assert "gui-scaled" in prompt
                assert "#tag" in prompt
                assert "Baritone" in prompt
                # M3.6 观察纪律（幻觉防护）：世界现状必查工具，闲聊/知识问答豁免
                assert "观察纪律" in prompt
                assert "当前世界状态" in prompt
                assert "禁止凭记忆" in prompt
                assert "感知工具" in prompt
                assert "闲聊" in prompt
                assert "知识问答" in prompt
                assert "工具结果" in prompt                        # 汇报引用豁免
                # 安全约束节保留
                assert "禁止攻击任何玩家或实体" in prompt
                assert "当前任务" in prompt and "测试提示" in prompt
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 双人全流程 / 急停


class TestTwoPlayerFlow:
    def test_full_flow_self_echo_and_new_task(self):
        """双人全流程：Alex 指令 → 感知/决策/工具 → finish 播报；自回显不触发新任务。"""

        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            vlm = ScriptedVLM([
                resp_tools(("getStats", {})),
                resp_tools(("lookAt", {"x": 103.0, "y": 64.0, "z": -198.0})),
                resp_tools(("command", {"text": "石头丢给你了，接好！"})),
                resp_tools((FINISH_TOOL, {"result": "任务完成：石头已丢向 Alex"})),
            ])
            agent = make_agent(client, vlm)
            main_task = None
            try:
                await client.connect()          # 先连接，再启动常驻循环（否则自识别撞未连接）
                main_task = asyncio.create_task(agent.run())
                # 自识别：getStats 坐标 == Sirius_Bot 实体坐标 → 拿到自身 uuid
                assert await wait_until(lambda: agent.self_uuid == BOT_UUID)
                # 玩家 Alex 下指令
                await say(server, "丢一块石头给我")
                assert await wait_until(
                    lambda: agent.last_run is not None
                    and agent.last_run.end_reason == "finish"), \
                    f"last_run={agent.last_run}"
                run = agent.last_run
                # 工具调用序列与剧本一致（lookAt 参数也原样到达）
                assert run.tool_names == ["getStats", "lookAt", "command", FINISH_TOOL]
                assert run.tool_calls[1].arguments == {"x": 103.0, "y": 64.0,
                                                       "z": -198.0}
                # finish 播报走了 command（wire 真值；播报在任务收尾异步发出，等它到线）
                assert await wait_until(
                    lambda: "任务完成：石头已丢向 Alex" in sent_texts(server))
                assert "石头丢给你了，接好！" in sent_texts(server)
                # 自回显三连：自身 uuid / 抑制窗内同文本无 sender / 系统行 —— 都不触发新任务
                calls_before = len(vlm.captured)
                await say(server, "任务完成：石头已丢向 Alex", sender=BOT_UUID)
                await say(server, "石头丢给你了，接好！", sender=None)
                await say(server, "<Alex> 加入了游戏", system=True)
                await asyncio.sleep(0.5)
                assert len(vlm.captured) == calls_before
                # 真玩家新指令照常进任务（剧本耗尽重复最后一条 finish，立即结束）
                await say(server, "再丢一块")
                assert await wait_until(lambda: len(vlm.captured) > calls_before)
                assert await wait_until(
                    lambda: agent.last_run is not None
                    and agent.last_run.end_reason == "finish"
                    and agent.last_run.instruction == "再丢一块")
            finally:
                if main_task is not None:
                    main_task.cancel()
                    await asyncio.gather(main_task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_stop_interrupts_running_task(self):
        """急停：任务执行中途玩家喊"停下" → 下一检查点断出 + 回话，任务不再继续。"""

        async def main() -> None:
            server = RecordingMock(two_player_scene(), port=0)
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            gate = threading.Event()
            vlm = ScriptedVLM(
                [resp_tools(("getStats", {}))],          # 永不 finish
                block_gate=(2, gate))                    # 第 3 次 VLM 调用冻结等指令
            agent = make_agent(client, vlm, loop_config=LoopConfig(max_steps=10))
            main_task = None
            try:
                await client.connect()          # 先连接，再启动常驻循环
                main_task = asyncio.create_task(agent.run())
                assert await wait_until(lambda: agent.self_uuid == BOT_UUID)
                await say(server, "丢一块石头给我")
                # 等任务推进到第 3 次 VLM 调用（前两步工具已执行完，正阻塞在途）
                assert await wait_until(lambda: len(vlm.captured) >= 3)
                await say(server, "停下")               # 在途 VLM 调用不打断
                gate.set()                              # 放行第 3 次调用
                assert await wait_until(
                    lambda: agent.last_run is not None
                    and agent.last_run.end_reason == "stop"), \
                    f"last_run={agent.last_run}"
                run = agent.last_run
                assert run.end_reason == "stop"
                assert run.steps == 3                    # 在途调用完成、下一个检查点断出
                assert FINISH_TOOL not in run.tool_names
                # "好的，停下了" 回话（急停回话在后台任务里发，等它上 wire）
                assert await wait_until(lambda: STOP_REPLY_TEXT in sent)
                assert await wait_until(
                    lambda: STOP_REPLY_TEXT in sent_texts(server))
                # 任务已死：不再有新的 VLM 调用
                calls_now = len(vlm.captured)
                await asyncio.sleep(0.3)
                assert len(vlm.captured) == calls_now
            finally:
                gate.set()
                if main_task is not None:
                    main_task.cancel()
                    await asyncio.gather(main_task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- M3.5 配置 / 接线 / 错误映射


class _BarrierStubClient:
    """Primitives 接口的最小无服务器 client：getGuiState 恒报被占用屏（cancel 接线验证用）。"""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def call(self, method, params=None):  # noqa: ANN001, ANN202
        if method == "getGuiState":
            return {"screen_open": True, "in_game": True,
                    "screen_class": "LevelLoadingScreen"}
        if method == "getStats":
            return {"in_game": True, "position": {"x": 0.0, "y": 64.0, "z": 0.0}}
        return {}

    async def command(self, text: str):
        self.commands.append(text)


class TestM35Wiring:
    def test_budget_default_500k(self):
        """M3.5：token 预算默认 500k（原语下沉后调用数骤降，200k 会误伤复杂探索任务）。"""
        assert LoopConfig().max_total_tokens == 500_000

    def test_bridge_error_hint_semantics(self):
        """错误码 → 建议动作映射（模型读文本可自救）。"""
        assert "range≤64" in bridge_error_hint(-32602)
        assert "filter 1..16" in bridge_error_hint(-32602)
        assert "限频" in bridge_error_hint(-32010)
        assert "20/s" in bridge_error_hint(-32010)
        assert "稍等再试" in bridge_error_hint(-32010)
        assert "input_enabled" in bridge_error_hint(-32011)   # 输入关闭语义说明
        assert "权限分级" in bridge_error_hint(-32012)         # 权限拒绝语义说明
        assert bridge_error_hint(-32000) == ""                 # 未知码不加建议

    def test_loop_default_registry_cancel_binds_stop_flag(self):
        """AgentLoop 默认注册表：原语 cancel 绑 _stop_requested（急停≤1s 的接线离线验证）。"""

        async def main() -> None:
            client = BridgeClient("ws://127.0.0.1:1")
            agent = AgentLoop(client, ScriptedVLM([resp_text("x")]),
                              AgentConfig(vlm=VLMConfig(api_key="sk-test")))
            stub = _BarrierStubClient()
            agent._stop_requested = True
            outcome = await agent.registry.execute(stub, WALK_TO_TOOL,
                                                   {"x": 10.0, "z": 8.0})
            assert "行走已中止" in outcome.text    # 屏障等待的微步里即感知急停
            assert stub.commands == []             # 尚未发 #goto，命令不丢也绝不发错

        asyncio.run(main())


# ---------------------------------------------------------------------- CLI


class TestCli:
    def write_local_md(self, path: Path) -> None:
        path.write_text(
            "# local\n\n```env\n"
            "SIRIUS_VLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n"
            "SIRIUS_VLM_API_KEY=sk-test\n"
            "SIRIUS_VLM_MODEL=qwen3.7-plus\n"
            "SIRIUS_BRIDGE_URL=ws://127.0.0.1:9999\n"
            "SIRIUS_BRIDGE_TOKEN=tok123\n"
            "```\n", encoding="utf-8")

    def test_argparser_and_config_loading(self, tmp_path: Path):
        from sirius_brain.agent.__main__ import build_argparser, load_config

        local_md = tmp_path / "local.md"
        self.write_local_md(local_md)
        args = build_argparser().parse_args(
            ["--local-md", str(local_md), "--url", "ws://127.0.0.1:8765",
             "--token", "newtok", "--max-steps", "5", "-v"])
        assert args.url == "ws://127.0.0.1:8765"
        assert args.max_steps == 5 and args.verbose
        config = load_config(args)
        assert config.vlm.api_key == "sk-test"
        assert config.vlm.model == "qwen3.7-plus"
        assert config.bridge.url == "ws://127.0.0.1:8765"     # 显式参数覆盖 env 块
        assert config.bridge.token == "newtok"
        assert config.loop.max_steps == 5
        # 不给覆盖参数时 env 块的 bridge 配置生效
        args2 = build_argparser().parse_args(["--local-md", str(local_md)])
        config2 = load_config(args2)
        assert config2.bridge.url == "ws://127.0.0.1:9999"
        assert config2.bridge.token == "tok123"
        assert config2.loop.max_steps == 25
        assert config2.loop.max_total_tokens == 500_000  # M3.5 新默认
