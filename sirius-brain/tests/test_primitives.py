"""M3.5 原语模块测试（T2×T4 联跑）：Primitives 对 FakeWorldBridge 全离线端到端。

- 世界用 FakeWorldBridge（T4 假世界）：可变 blocks + 假 Baritone（#goto/#stop）+
  朝向/触及判定的 input.click——原语的每条契约路径都能离线复现
- 全部 mock 服务端绑定随机空闲端口（port=0，与 test_bridge_client/test_mock_bridge
  同口径）：8765 留给真机 bridge，测试绝不与之抢端口/发生连接
- 项目未安装 pytest-asyncio，异步场景以 asyncio.run() 驱动（与 test_agent_loop 同口径）
- client.command 的收尾等待压到 20ms（0.4+0.3s 的 T→text→ENTER 时序保留——
  FakeWorldBridge 的聊天框状态机依赖它）；看门狗/静默常量用 monkeypatch 压短，
  不为测试放宽生产代码
"""

import asyncio
import math
import time

from sirius_brain.agent import PICKUP_TOOL, Primitives, default_registry
from sirius_brain.agent import primitives as primitives_module
from sirius_brain.bridge import BridgeClient
from sirius_brain.bridge.client import BridgeError
from sirius_brain.mock import FakeWorldBridge

ORIGIN = {"x": 0.0, "y": 64.0, "z": 0.0}


# ---------------------------------------------------------------------- 测试基建


def fast_commands(client: BridgeClient) -> None:
    """把 client.command 的 settle 压到 20ms（离线 mock 不需要等命令在服务器生效）。"""
    real_command = client.command

    async def fast(text: str, settle: float = 0.5, timeout=None):  # noqa: ANN001, ANN202
        return await real_command(text, settle=0.02, timeout=timeout)

    client.command = fast  # type: ignore[method-assign]


async def make_pair(server: FakeWorldBridge) -> BridgeClient:
    await server.start()
    client = BridgeClient(server.url)
    await client.connect()
    fast_commands(client)
    return client


def dist_xy(server: FakeWorldBridge, x: float, z: float) -> float:
    return math.hypot(server.position["x"] - x, server.position["z"] - z)


async def flip_flag_after(flag: dict, delay: float) -> None:
    """delay 秒后把 flag["stop"] 置 True（取消测试的急停触发器）。"""
    await asyncio.sleep(delay)
    flag["stop"] = True


class NoDigClient:
    """包装 client：把 dig 调用按旧 jar 行为拒绝（-32601 not implemented）——
    驱动 primitives 的 fallback 段循环路径（其余方法透传）。"""

    def __init__(self, inner: BridgeClient) -> None:
        self.inner = inner
        self.dig_attempts = 0

    async def call(self, method, params=None):  # noqa: ANN001, ANN202
        if method == "dig":
            self.dig_attempts += 1
            raise BridgeError(-32601, "not implemented: dig")
        return await self.inner.call(method, params)

    async def command(self, text: str, **kwargs):  # noqa: ANN202
        return await self.inner.command(text, **kwargs)


# ---------------------------------------------------------------------- FakeWorldBridge 行为（单元）


class TestFakeWorldBehavior:
    def test_world_query_filter_range_and_truncation(self):
        """filter 按 registry 名/#tag 匹配；range 立方扫描（每轴 ±ceil(range)）；cap 32。"""

        async def main() -> None:
            # 40 根云杉原木铺在 7×7 网格上（全部落在 range 16 的立方扫描内）→ 验证 cap；
            # 近处橡木命中 #logs、远处橡木被 range 排除、石头被 filter 排除
            near_cells = [(x, 64, z) for x in range(1, 8) for z in range(1, 8)]
            blocks = {pos: "spruce_log" for pos in near_cells[:40]}
            blocks[(2, 64, -3)] = "oak_log"   # range 3 立方内 → #logs 命中
            blocks[(50, 64, 0)] = "oak_log"   # 远处 → range 排除
            blocks[(1, 64, -1)] = "stone"     # 立方内但非 logs → filter 排除
            server = FakeWorldBridge(port=0, position=dict(ORIGIN), blocks=blocks)
            client = await make_pair(server)
            try:
                by_name = await client.call(
                    "world.query", {"type": "blocks", "range": 16.0,
                                    "filter": ["spruce_log"]})
                assert by_name["count"] == 32 and by_name["truncated"] is True
                assert all(b["block"] == "minecraft:spruce_log" for b in by_name["blocks"])
                # 命中按与玩家距离升序（T1 契约）：最近的在前
                dists = [math.hypot(b["x"] + 0.5, b["z"] + 0.5) for b in by_name["blocks"]]
                assert dists == sorted(dists)

                by_tag = await client.call(
                    "world.query", {"type": "blocks", "range": 3.0, "filter": ["#logs"]})
                # 立方每轴 ±3：9 格云杉 + 1 格橡木；石头与远处橡木都不在结果里
                assert {(b["x"], b["y"], b["z"]) for b in by_tag["blocks"]} == \
                    {(x, 64, z) for x in (1, 2, 3) for z in (1, 2, 3)} | {(2, 64, -3)}
                assert {b["block"] for b in by_tag["blocks"]} == \
                    {"minecraft:spruce_log", "minecraft:oak_log"}
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_tap_does_not_break_held_click_does(self):
        """25ms tap 挖不掉方块；hold_ms 达标且瞄准则挖掉（FakeWorldBridge 现实语义）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "spruce_log"})
            client = await make_pair(server)
            try:
                await client.call("lookAt", {"x": 3.5, "y": 64.5, "z": 2.5})
                await client.call("input.click", {"button": 0})  # 旧契约的 25ms tap
                assert (3, 64, 2) in server.blocks
                await client.call("input.click", {"button": 0, "hold_ms": 600})
                assert (3, 64, 2) not in server.blocks
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- walk_to


class TestWalkTo:
    def test_arrives(self):
        """常规到达：#goto 两参形式上 wire，位置推进到目标，成功话术带最终坐标。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).walk_to(10.0, 8.0)
                assert outcome.text.startswith("已走到")
                assert "(10, 64, 8)" in outcome.text
                assert "#goto 10 8" in server.submitted
                assert dist_xy(server, 10.0, 8.0) <= 0.01  # 假 Baritone 精确到达
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_timeout_sends_stop(self):
        """冻结世界（move_speed=0）→ 超时：发 #stop + "同参数重发可续走"健康超时话术。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN), move_speed=0.0)
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).walk_to(10.0, 8.0, timeout=1.0)
                assert "超时" in outcome.text
                assert "同参数重发" in outcome.text  # 建议续走而非重试（Numen 话术）
                assert "#stop" in server.submitted
                assert dist_xy(server, 10.0, 8.0) > 5.0  # 没到
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_stall_resends_goto_once(self, monkeypatch):
        """看门狗：距离无进展只重发一次 #goto（近重试档），之后等超时收尾。"""
        monkeypatch.setattr(primitives_module, "WALK_STALL_SECONDS", 0.3)

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN), move_speed=0.0)
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).walk_to(10.0, 8.0, timeout=2.0)
                assert "超时" in outcome.text
                assert server.submitted.count("#goto 10 8") == 2  # 首发 + 恰一次重发
                assert server.submitted.count("#stop") == 1
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_cancel_stops_and_reports_position(self):
        """协作式取消：微步检查点断出 → #stop 上 wire → 中止文案带当前坐标。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            flag = {"stop": False}
            try:
                flipper = asyncio.create_task(flip_flag_after(flag, 1.5))
                outcome = await Primitives(client).walk_to(
                    60.0, 0.0, cancel=lambda: flag["stop"])
                await flipper
                assert "已中止" in outcome.text
                assert "#stop" in server.submitted
                assert "当前位于" in outcome.text  # 取消话术带当前坐标（Numen 契约）
                assert 0.5 < server.position["x"] < 50.0  # 走了一段但没到
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 界面屏障（T0b 教训）


class ScreenStubClient:
    """无服务器最小 client（Primitives 只要求 call/command）：getGuiState 按 screens
    序列回放（最后一条重复）；fail_gui=True 时 getGuiState 抛错（屏障降级路径）。"""

    def __init__(self, screens: list[dict], *, fail_gui: bool = False):
        self.screens = list(screens)
        self.fail_gui = fail_gui
        self.commands: list[str] = []
        self.gui_calls = 0

    async def call(self, method, params=None):  # noqa: ANN001, ANN202
        if method == "getGuiState":
            self.gui_calls += 1
            if self.fail_gui:
                raise RuntimeError("getGuiState 不可用")
            return self.screens[min(self.gui_calls - 1, len(self.screens) - 1)]
        if method == "getStats":
            return {"in_game": True, "position": {"x": 0.0, "y": 64.0, "z": 0.0}}
        return {}

    async def command(self, text: str):
        self.commands.append(text)


class TestScreenBarrier:
    def test_waits_for_loading_screen_to_clear(self):
        """T0b 根因复现：加载屏未消失时先等（轮询 getGuiState）再发 #goto，命令不丢。"""

        async def main() -> None:
            stub = ScreenStubClient([
                {"screen_open": True, "in_game": True,
                 "screen_class": "LevelLoadingScreen"},   # 第 1 次查：屏还在
                {"screen_open": False},                     # 第 2 次查：已消失
            ])
            outcome = await Primitives(stub, poll_interval=0.02).walk_to(1.0, 0.0)
            assert stub.gui_calls == 2          # 屏障等了一轮才放行
            assert stub.commands == ["#goto 1 0"]  # 屏消失后命令才发出（T0b：之前会丢）
            assert outcome.text.startswith("已走到")

        asyncio.run(main())

    def test_barrier_timeout_blocks_goto(self, monkeypatch):
        """屏一直不消失：等到上限即教学式失败（先处理界面），绝不盲发 #goto。"""
        monkeypatch.setattr(primitives_module, "WALK_SCREEN_BARRIER_TIMEOUT", 0.05)

        async def main() -> None:
            stub = ScreenStubClient([
                {"screen_open": True, "in_game": True,
                 "screen_class": "LevelLoadingScreen"},
            ])
            outcome = await Primitives(stub, poll_interval=0.02).walk_to(10.0, 8.0)
            assert "界面被 LevelLoadingScreen 占用" in outcome.text
            assert "处理界面" in outcome.text          # 建议行动：先处理界面
            assert "重发 walkTo" in outcome.text
            assert stub.commands == []                 # 没有发出任何命令

        asyncio.run(main())

    def test_gui_query_failure_does_not_block(self):
        """getGuiState 查询失败：屏障视同无界面放行（防丢命令的措施不该反过来卡行走）。"""

        async def main() -> None:
            stub = ScreenStubClient([], fail_gui=True)
            outcome = await Primitives(stub, poll_interval=0.02).walk_to(1.0, 0.0)
            assert stub.gui_calls == 1                # 确实查过一次（失败）
            assert "#goto 1 0" in stub.commands       # 放行
            assert outcome.text.startswith("已走到")

        asyncio.run(main())


# ---------------------------------------------------------------------- dig_block


class TestDigBlock:
    def test_dig_success(self):
        """触及范围内：一次 bridge dig RPC（自带瞄准+按住）→ 方块消失，话术带 registry 名。
        T6 后走 bridge 智能原语：wire 上只有 dig 调用，无 lookAt/input.click 编排。
        M3.6 T3：破坏结果附实测掉落（drops → 话术"掉落 …×n"）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "spruce_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).dig_block(3, 64, 2)
                assert outcome.text == ("已挖掉 minecraft:spruce_log（3,64,2），"
                                        "掉落 minecraft:spruce_log×1")
                assert (3, 64, 2) not in server.blocks
                # bridge 路径：dig 一次到位（timeout_ms 封顶 30s 协议上限），无段循环
                assert len(server.digs) == 1
                assert server.digs[0]["x"] == 3 and server.digs[0]["y"] == 64
                assert server.digs[0]["z"] == 2
                assert server.digs[0]["timeout_ms"] == primitives_module.DIG_BRIDGE_TIMEOUT_MS
                assert not server.looks and not server.clicks  # 瞄准/按住在 bridge 侧
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_dig_through_occluder_notes_it(self):
        """遮挡场景：眼位→中心连线穿过树叶 → fake 连遮挡一起移除并标
        broken_via_occluder；话术带"视线先穿过遮挡物"（T6 契约）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "oak_log",
                                             (4, 65, 2): "oak_leaves"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).dig_block(3, 64, 2)
                assert outcome.text.startswith("已挖掉 minecraft:oak_log")
                assert "遮挡" in outcome.text
                assert (3, 64, 2) not in server.blocks   # 目标挖掉
                assert (4, 65, 2) not in server.blocks   # 遮挡树叶也穿掉了
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_dig_too_far_teaches_walk_first(self):
        """超触及：教学式失败（先 walkTo 旁边），不动键鼠、不发 dig，方块原样保留。
        感知范围外的目标不能被误报成"已空"——同样教先走位。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(10, 64, 10): "spruce_log",
                                             (100, 64, 100): "spruce_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).dig_block(10, 64, 10)
                assert "超出触及范围" in outcome.text
                assert "walkTo" in outcome.text  # 下一步建议：先走过去
                assert "minecraft:spruce_log" in outcome.text  # 看得见就报是什么方块
                assert (10, 64, 10) in server.blocks
                assert not server.looks and not server.clicks  # 未盲挖
                assert not server.digs  # 触及检查在本地完成，RPC 都没发

                far = await Primitives(client).dig_block(100, 64, 100)  # 感知范围外
                assert "远超触及与感知范围" in far.text
                assert "walkTo" in far.text
                assert not server.looks and not server.clicks and not server.digs
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_dig_already_gone_is_success(self):
        """目标已空：幂等成功（此前已挖掉或本就是空气）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(6, 64, 2): "spruce_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).dig_block(3, 64, 2)  # 这里没有方块
                assert "已不存在" in outcome.text
                assert not server.clicks and not server.digs
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_dig_unbreakable_teaches_tools(self):
        """挖不破（bedrock）：bridge 回 timeout → 教学"工具不足/被保护"，不无限空挖。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "bedrock"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).dig_block(3, 64, 2)
                assert "无法破坏" in outcome.text
                assert "工具不足" in outcome.text or "保护" in outcome.text
                assert (3, 64, 2) in server.blocks
                assert len(server.digs) == 1  # 一次 dig 调用就得出结论（无段循环）
                assert not server.clicks
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_dig_falls_back_to_hold_loop_on_old_bridge(self, monkeypatch):
        """旧 jar（无 dig 工具，回 -32601）：记忆后回退本地 lookAt+hold 段循环——
        T5a 修复的段递增逻辑在 fallback 路径原样保留。"""
        monkeypatch.setattr(primitives_module, "DIG_SETTLE", 0.02)
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MS", 150)
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MAX_MS", 150)

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "spruce_log"})
            client = await make_pair(server)
            no_dig = NoDigClient(client)
            prims = Primitives(no_dig)
            try:
                outcome = await prims.dig_block(3, 64, 2)
                assert outcome.text == "已挖掉 minecraft:spruce_log（3,64,2）"
                assert (3, 64, 2) not in server.blocks
                # fallback 路径走的是段循环：lookAt + input.click(hold_ms) 上 wire
                assert server.looks[-1] == (3.5, 64.5, 2.5)
                assert server.clicks[-1] == {"button": 0, "hold_ms": 150}
                assert prims._dig_supported is False  # 记忆：后续不再试调 dig
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_dig_fallback_unbreakable_after_8_segments(self, monkeypatch):
        """fallback 段循环的 8 段上限（旧路径回归）：bedrock 8 段后教学式失败。"""
        monkeypatch.setattr(primitives_module, "DIG_SETTLE", 0.02)
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MS", 150)
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MAX_MS", 150)

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "bedrock"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(NoDigClient(client)).dig_block(3, 64, 2)
                assert "无法破坏" in outcome.text
                assert "遮挡" in outcome.text or "工具不足" in outcome.text
                assert (3, 64, 2) in server.blocks
                assert len(server.clicks) == 8  # 恰好 8 段就放弃
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- collect_block


class TestCollectBlock:
    def test_full_chain_with_tag_filter(self, monkeypatch):
        """组合场景：collect 3 块 #logs（spruce×2 + oak×1）完整链路——
        query(filter=#tag) → 最近 → walk_to 邻近 → dig_block，循环到收满。
        T7 起挖后自动拾取：每挖一块捡走它掉的掉落物，话术末尾附拾取注记。"""
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MS", 150)

        async def main() -> None:
            server = FakeWorldBridge(
                port=0,
                position=dict(ORIGIN),
                blocks={(6, 64, 0): "spruce_log", (10, 64, 4): "oak_log",
                        (14, 64, 8): "spruce_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["#logs"], 3)
                assert outcome.text == "已挖到 3/3 个 #logs，已捡起 3 个掉落"
                assert not server.blocks  # 三块全挖掉
                assert not server.item_drops  # 三个掉落也全捡走（T7）
                gotos = [line for line in server.submitted if line.startswith("#goto")]
                assert len(gotos) >= 6    # 每块一次走位 + 每个掉落一次拾取走位
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_partial_collect_is_success(self, monkeypatch):
        """部分收：2/5 后范围内清空 → 仍算成功，话术说明"已无更多"。"""
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MS", 150)

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "spruce_log",
                                             (12, 64, 0): "spruce_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["spruce_log"], 5)
                assert "已挖到 2/5" in outcome.text
                assert "范围内已无更多" in outcome.text
                assert not server.blocks
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_none_found_is_teaching_failure(self):
        """空范围：教学式失败——确认 ID（含 #tag 写法）或走近些；不发起任何走位。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(3, 64, 2): "stone"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["spruce_log"], 1)
                assert "未找到" in outcome.text
                assert "#tag" in outcome.text  # 写法提示
                assert not [line for line in server.submitted if line.startswith("#goto")]
                assert (3, 64, 2) in server.blocks  # 石头无辜
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 挖后拾取（T7）


def add_drop(server: FakeWorldBridge, uuid: str, item: str,
             x: float, y: float, z: float, **flags: object) -> None:
    """往假世界塞一个掉落物实体（模拟别人掉的/环境掉的；挖出来的由 dig 自动生成）。"""
    server.item_drops[uuid] = {
        "uuid": uuid, "name": item, "type": "minecraft:item",
        "item": item, "count": 1,
        "position": {"x": x, "y": y, "z": z}, **flags,
    }


class ThirdPartyPickupClient:
    """包装 client：第 2 次 entities 查询（拾取走位后的复核）前清空掉落物表——
    模拟别人抢先捡走：驱动"实体消失 = 已拾取（无论谁捡的）"的 Numen 语义分支
    （配合 server.drop_no_absorb=True，掉落物只能被这个清空动作移除）。"""

    def __init__(self, inner: BridgeClient, server: FakeWorldBridge) -> None:
        self.inner = inner
        self.server = server
        self.entities_queries = 0

    async def call(self, method, params=None):  # noqa: ANN001, ANN202
        if (method == "world.query" and isinstance(params, dict)
                and params.get("type") == "entities"):
            self.entities_queries += 1
            if self.entities_queries == 2:
                self.server.item_drops.clear()  # 别人捡走了（含我们的目标）
        return await self.inner.call(method, params)

    async def command(self, text: str, **kwargs):  # noqa: ANN202
        return await self.inner.command(text, **kwargs)


class TestFakeWorldDrops:
    def test_dig_spawns_drop_entities_query_and_absorb(self):
        """FakeWorldBridge T7 物源：dig 掉方块 → 原地生成 item 实体（带注册名与
        count，2 格外不掉）；玩家走上去 → 轮询时吸附消失（vanilla 拾取近似）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "oak_log"})
            client = await make_pair(server)
            try:
                before = await client.call("world.query",
                                           {"type": "entities", "range": 8,
                                            "filter": ["item"]})
                assert before["count"] == 0
                await client.call("dig", {"x": 3, "y": 64, "z": 2})
                result = await client.call("world.query",
                                           {"type": "entities", "range": 8})
                assert result["count"] == 1 and not result["truncated"]
                drop = result["entities"][0]
                assert drop["type"] == "minecraft:item"
                assert drop["item"] == "minecraft:oak_log"  # 注册名（T7 契约）
                assert drop["count"] == 1
                assert "uuid" in drop and "position" in drop
                # 走到掉落物上（getStats/world.query 轮询触发吸附）→ 实体消失
                await client.command("#goto 3.5 2.5")
                await asyncio.sleep(1.2)
                after = await client.call("world.query",
                                          {"type": "entities", "range": 8})
                assert after["count"] == 0
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


class TestCollectBlockPickup:
    def test_collect_picks_up_own_drops(self):
        """T7 主场景：挖掉后掉落物躺在旁边 2 格（不掉）→ collect 顺路走过去吸附，
        话术附"已捡起"，掉落物表清空。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "oak_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["oak_log"], 1)
                assert outcome.text == "已挖到 1/1 个 oak_log，已捡起 1 个掉落"
                assert not server.blocks and not server.item_drops
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_foreign_drops_untouched(self):
        """多人服礼仪：匹配不上的掉落（别人的/树叶掉的树苗）绝对不碰——只走自己的
        目标掉落，别人的原地保留。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "oak_log"})
            add_drop(server, "foreign-sapling", "minecraft:oak_sapling", 6.5, 64.5, 2.5)
            add_drop(server, "foreign-stone", "minecraft:stone", 8.5, 64.5, 0.5)
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["oak_log"], 1)
                assert "已捡起 1 个掉落" in outcome.text  # 只捡了自己的原木
                assert set(server.item_drops) == {"foreign-sapling", "foreign-stone"}
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_drop_beyond_dig_zone_ignored(self):
        """挖点 4 格外的匹配掉落不捡（可能是别人挖的——多人服礼仪的另一面）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "oak_log"})
            add_drop(server, "far-log", "minecraft:oak_log", 11.5, 64.5, 0.5)  # 距挖点 5 格
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["oak_log"], 1)
                assert outcome.text == "已挖到 1/1 个 oak_log，已捡起 1 个掉落"
                assert set(server.item_drops) == {"far-log"}  # 远处的不碰
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_vanished_entity_counts_as_picked(self):
        """别人抢先捡走（实体在复核查询前被移除）：实体消失 = 已拾取（无论谁捡的）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "oak_log"})
            server.drop_no_absorb = True  # 物理吸附关闭：掉落物只能被 wrapper 移除
            client = await make_pair(server)
            third_party = ThirdPartyPickupClient(client, server)
            try:
                outcome = await Primitives(third_party).collect_block(["oak_log"], 1)
                assert third_party.entities_queries >= 2  # 复核查询确实发生了
                assert "已捡起 1 个掉落" in outcome.text
                assert not server.item_drops
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_unabsorbable_drop_skipped_no_loop(self):
        """skip 防死循环：走到掉落物身旁（≤1.2 格）仍没吸上 → 记 skip 后正常收尾，
        不会对着同一个实体无限走（远早于 PICKUP_TIMEOUT 兜底）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "oak_log"})
            server.drop_no_absorb = True
            client = await make_pair(server)
            try:
                started = time.monotonic()
                outcome = await Primitives(client).collect_block(["oak_log"], 1)
                elapsed = time.monotonic() - started
                assert outcome.text == "已挖到 1/1 个 oak_log"  # 没捡到：无拾取注记
                assert elapsed < primitives_module.PICKUP_TIMEOUT  # skip 生效，非超时兜底
                assert len(server.item_drops) == 1  # 够不着的还躺在地上
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_collect_without_pickup_leaves_drops(self):
        """pickup=False（挖通道/清理地形场景）：挖后不拾取，掉落物留在原地。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "oak_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["oak_log"], 1,
                                                                 pickup=False)
                assert outcome.text == "已挖到 1/1 个 oak_log"
                assert len(server.item_drops) == 1  # 掉落物原地保留
                # 也没有为拾取发起走位：#goto 只有 1 次（挖位走位）
                gotos = [line for line in server.submitted if line.startswith("#goto")]
                assert len(gotos) == 1
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


class TestPickupMethod:
    def test_pickup_sweeps_matching_drops_only(self):
        """pickup()：范围内按注册名捡匹配掉落（Numen collect_items 对应物）；
        不匹配的保留；走位顺序取离自己最近的。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            add_drop(server, "drop-a", "minecraft:oak_log", 3.5, 64.5, 0.5)
            add_drop(server, "drop-b", "minecraft:oak_log", -3.5, 64.5, 0.5)
            add_drop(server, "drop-c", "minecraft:stick", 0.5, 64.5, 4.5)
            client = await make_pair(server)
            prims = Primitives(client)
            try:
                outcome = await prims.pickup(["minecraft:oak_log"])
                assert outcome.text == "已捡起 2 个 minecraft:oak_log"
                assert set(server.item_drops) == {"drop-c"}  # 木棍不碰（礼仪）
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_pickup_zero_items_is_success(self):
        """范围内没有匹配掉落 = 成功收尾（Numen：0 件也是有效答案）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            add_drop(server, "drop-c", "minecraft:stick", 0.5, 64.5, 2.5)
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).pickup(["minecraft:diamond"])
                assert "没有可捡的" in outcome.text
                assert set(server.item_drops) == {"drop-c"}
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_pickup_tag_only_ids_rejected_gently(self):
        """#tag 条目无法在 item 注册名上展开（无 item tag 查询通道）：教学文案，
        不发起任何走位。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).pickup(["#minecraft:logs"])
                assert "非 #tag" in outcome.text
                assert not [line for line in server.submitted if line.startswith("#goto")]
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_pickup_without_ids_sweeps_all(self):
        """M3.6：缺省 item_ids = 捡范围内全部掉落（Numen collect_items 全收集；
        多人服礼仪由 VLM 工具描述约束使用场景）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            add_drop(server, "drop-a", "minecraft:oak_log", 3.5, 64.5, 0.5)
            add_drop(server, "drop-b", "minecraft:oak_log", -3.5, 64.5, 0.5)
            add_drop(server, "drop-c", "minecraft:stick", 0.5, 64.5, 4.5)
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).pickup()
                assert outcome.text == "已捡起 3 个 全部掉落物"
                assert not server.item_drops
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- dig 实测掉落（M3.6 T3）


class TestDigDrops:
    def test_dig_result_carries_new_drops_only(self):
        """fake dig 契约：broken 附 drops=[{item,count}]——挖前快照内已存在的
        （别人的）不算，already_air/timeout 不带该字段（对齐真 bridge）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position={"x": 4.5, "y": 64.0, "z": 2.5},
                                     blocks={(3, 64, 2): "oak_log", (5, 64, 4): "bedrock"})
            # 挖前就躺在挖点 4 格内的别人掉的（同 id 也不能算本次掉落）
            add_drop(server, "pre-existing", "minecraft:oak_log", 3.5, 64.5, 2.5)
            client = await make_pair(server)
            try:
                result = await client.call("dig", {"x": 3, "y": 64, "z": 2})
                assert result["result"] == "broken"
                assert result["drops"] == [{"item": "minecraft:oak_log", "count": 1}]
                timeout = await client.call("dig", {"x": 5, "y": 64, "z": 4,
                                                    "timeout_ms": 600})
                assert timeout["result"] == "timeout" and "drops" not in timeout
                air = await client.call("dig", {"x": 3, "y": 64, "z": 2})
                assert air["result"] == "already_air" and "drops" not in air
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_collect_prefers_dig_drops_over_registry_guess(self, monkeypatch):
        """匹配优先级（T3 主张）：dig 实测 drops > registry id 猜测——模组方块
        掉落物注册名与方块不同名（magic_block→apple），实测清单才能捡对。"""
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MS", 150)

        class CustomDropWorld(FakeWorldBridge):
            def _spawn_drop(self, x, y, z, block_id):
                item = "minecraft:apple" if block_id == "minecraft:magic_block" else block_id
                super()._spawn_drop(x, y, z, item)

        async def main() -> None:
            server = CustomDropWorld(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "magic_block"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["magic_block"], 1)
                # registry 猜测（magic_block）匹配不上 apple 掉落——只有实测
                # drops 才能把 apple 捡走（旧逻辑此话术不会带拾取注记）
                assert outcome.text == "已挖到 1/1 个 magic_block，已捡起 1 个掉落"
                assert not server.blocks and not server.item_drops
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_collect_skips_sweep_when_dig_reports_no_drops(self):
        """drops=[]（明确无掉落，如挖了但啥也没掉）= 不发起拾取走位——
        区别于 None（旧 jar，回落 registry 猜测再扫一遍）。"""

        class DroplessWorld(FakeWorldBridge):
            def _spawn_drop(self, x, y, z, block_id):
                pass  # 挖了但什么都不掉

        async def main() -> None:
            server = DroplessWorld(port=0, position=dict(ORIGIN),
                                   blocks={(6, 64, 0): "magic_block"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(client).collect_block(["magic_block"], 1)
                assert outcome.text == "已挖到 1/1 个 magic_block"  # 无拾取注记
                # 也没有为拾取发起走位：#goto 只有 1 次（挖位走位）
                gotos = [line for line in server.submitted if line.startswith("#goto")]
                assert len(gotos) == 1
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_collect_falls_back_to_registry_on_old_bridge(self, monkeypatch):
        """旧 jar（dig 回 -32601，fallback 段循环无 drops）：回落 registry id
        精确匹配照样能捡起掉落（兼容路径回归）。"""
        monkeypatch.setattr(primitives_module, "DIG_SETTLE", 0.02)
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MS", 150)
        monkeypatch.setattr(primitives_module, "DIG_CLICK_HOLD_MAX_MS", 150)

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(6, 64, 0): "oak_log"})
            client = await make_pair(server)
            try:
                outcome = await Primitives(NoDigClient(client)).collect_block(["oak_log"], 1)
                assert outcome.text == "已挖到 1/1 个 oak_log，已捡起 1 个掉落"
                assert not server.blocks and not server.item_drops
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())


class TestPickupTool:
    def test_registered_tool_end_to_end(self):
        """M3.6：pickup 注册进默认注册表——registry.execute 驱动真实 wire：
        指定 item_ids 只捡匹配（多人服礼仪），缺省参数捡全部。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            add_drop(server, "drop-a", "minecraft:oak_log", 3.5, 64.5, 0.5)
            add_drop(server, "drop-b", "minecraft:stick", -3.5, 64.5, 0.5)
            client = await make_pair(server)
            registry = default_registry()
            try:
                outcome = await registry.execute(client, PICKUP_TOOL,
                                                 {"item_ids": ["minecraft:oak_log"],
                                                  "radius": 8})
                assert outcome.text == "已捡起 1 个 minecraft:oak_log"
                assert set(server.item_drops) == {"drop-b"}   # 木棍不碰（礼仪）
                outcome = await registry.execute(client, PICKUP_TOOL, {})  # 缺省=捡全部
                assert outcome.text == "已捡起 1 个 全部掉落物"
                assert not server.item_drops
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())
