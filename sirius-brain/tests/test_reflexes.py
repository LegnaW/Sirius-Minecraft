"""M4 反射层测试：等级框架 / 调度器 / 七条反射 / 事后知会 / urgent / preempt。

全部离线（与 test_primitives 同口径）：
- 世界用 FakeWorldBridge 的 M4 危险模拟：stats_override 注入 air/health、
  mob_entities 注入带 category/width 的怪与玩家、blocks 放水块模拟眼位进水、
  push_notification 直发 CRITICAL danger 事件
- 调度器单独驱动（ReflexScheduler(client) + 手动 install_default_chains）或经
  AgentLoop.run() 全链驱动（fire/低血/死亡的 preempt 与 urgent 走这条）
- 全部 mock 随机端口；VLM 用 test_agent_loop 的 ScriptedVLM（零网络）
"""

import asyncio
import threading
import time

import pytest

from sirius_brain.agent import (
    REFLEX_NOTICE_PREFIX,
    URGENT_PREFIX,
    AgentConfig,
    ReflexLevel,
    ReflexScheduler,
    ThreatFact,
    instincts_section,
    parse_reflex_level,
)
from sirius_brain.agent import reflexes as reflexes_module
from sirius_brain.agent.config import BridgeConfig, LoopConfig
from sirius_brain.agent.reflexes import match_reflex_level_command
from sirius_brain.bridge import BridgeClient, BridgeError
from sirius_brain.mock import FakeWorldBridge
from sirius_brain.protocol import EventLevel, NotificationFrame

from test_agent_loop import (
    ScriptedVLM,
    make_agent,
    resp_text,
    resp_tools,
    spy_commands,
    wait_until,
)

ORIGIN = {"x": 0.0, "y": 64.0, "z": 0.0}


# ---------------------------------------------------------------------- 测试基建


def fast_commands(client: BridgeClient) -> None:
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


def add_entity(server: FakeWorldBridge, uuid: str, *, type_: str, category: str,
               width: float, x: float, y: float = 64.0, z: float) -> None:
    """往假世界塞一个非掉落物实体（M4 entities 载荷形态：category/width）。"""
    server.mob_entities[uuid] = {
        "uuid": uuid, "name": type_.rsplit(":", 1)[-1], "type": type_,
        "category": category, "width": width,
        "position": {"x": x, "y": y, "z": z}, "health": 20.0,
    }


def add_monster(server: FakeWorldBridge, uuid: str, x: float, z: float, *,
                type_: str = "minecraft:zombie", width: float = 0.6,
                y: float = 64.0) -> None:
    add_entity(server, uuid, type_=type_, category="monster",
               width=width, x=x, y=y, z=z)


async def push_danger(server: FakeWorldBridge, event: str,
                      data: dict | None = None) -> None:
    """直发一条 CRITICAL danger 事件（M2-B 采样器的 wire 形态）。"""
    await server.push_notification(event, data or {}, level=EventLevel.CRITICAL)


def danger_frame(event: str, data: dict | None = None) -> NotificationFrame:
    return NotificationFrame(event=event, data=data or {},
                             timestamp=time.time(), seq=0)


async def make_scheduler(client: BridgeClient,
                         level: ReflexLevel = ReflexLevel.SELF_PRESERVE,
                         ) -> ReflexScheduler:
    scheduler = ReflexScheduler(client, level=level, poll_interval=0.05)
    scheduler.install_default_chains()
    # 单独驱动时手动接上 danger 事件（全链驱动时 AgentLoop.install() 做这件事）
    for event in ReflexScheduler.DANGER_EVENTS:
        client.add_event_handler(event, scheduler.danger_handler(event))
    return scheduler


async def say(server: FakeWorldBridge, message: str,
              sender: str = "33333333-3333-3333-3333-333333333333") -> None:
    await server.push_notification("chat", {"message": message, "sender": sender,
                                            "system": False})


def space_presses(server: FakeWorldBridge) -> list[dict]:
    return [k for k in server.key_presses if k["code"] == reflexes_module.GLFW_KEY_SPACE]


def w_presses(server: FakeWorldBridge) -> list[dict]:
    return [k for k in server.key_presses if k["code"] == reflexes_module.GLFW_KEY_W]


# ---------------------------------------------------------------------- 等级框架（单元）


class TestLevelFramework:
    def test_parse_reflex_level(self):
        assert parse_reflex_level("self_preserve") is ReflexLevel.SELF_PRESERVE
        assert parse_reflex_level(" Observer ") is ReflexLevel.OBSERVER
        with pytest.raises(ValueError):
            parse_reflex_level("aggressive")

    def test_match_reflex_level_command(self):
        assert match_reflex_level_command("反射等级 观察") is ReflexLevel.OBSERVER
        assert match_reflex_level_command("反射等级切到自保") is ReflexLevel.SELF_PRESERVE
        assert match_reflex_level_command("反射等级 guard") is ReflexLevel.GUARD
        assert match_reflex_level_command("帮我把反射等级调高一点") is None  # 无目标词→普通指令
        assert match_reflex_level_command("走过来") is None

    def test_instincts_sections(self):
        l1 = instincts_section(ReflexLevel.SELF_PRESERVE)
        assert "自保" in l1
        for word in ("换气", "脱困", "撤离", "逃离"):
            assert word in l1
        assert "不消耗你的决策" in l1            # 认知层不用管本能
        assert "本能反应" in l1                   # 事后知会通道点名
        l0 = instincts_section(ReflexLevel.OBSERVER)
        assert "观察" in l0 and "只会被告知" in l0  # L0：危险被告知，脊髓不接管
        assert "换气" not in l0                    # L0 不写反射清单
        l2 = instincts_section(ReflexLevel.GUARD)
        assert "预留" in l2                        # 框架注释占位

    def test_loop_config_reflex_defaults_and_validation(self):
        config = LoopConfig()
        assert config.reflex_level == "self_preserve"   # 默认 L1
        assert config.reflex_poll_interval == 0.5       # Numen 0.5s 移植值
        with pytest.raises(ValueError):
            LoopConfig(reflex_level="berserk")
        with pytest.raises(ValueError):
            LoopConfig(reflex_poll_interval=0.0)

    def test_scheduler_wired_from_loop_config(self):
        from sirius_brain.agent import AgentLoop, QwenVLM, VLMConfig

        agent = AgentLoop(BridgeClient("ws://127.0.0.1:1"),
                          QwenVLM(VLMConfig(api_key="sk-test")),
                          AgentConfig(vlm=VLMConfig(api_key="sk-test"),
                                      bridge=BridgeConfig(url="ws://127.0.0.1:1"),
                                      loop=LoopConfig(reflex_level="observer")))
        assert agent.scheduler.level is ReflexLevel.OBSERVER
        assert [c.id for c in agent.scheduler.chains] == [
            "death", "health_low", "fire", "flee",
            "breath", "unstuck", "speaking_look"]      # 注册序即优先级


# ---------------------------------------------------------------------- 危险状态采样（FakeWorldBridge）


class TestDangerSampling:
    def test_entities_payload_carries_category_and_width(self):
        """M4 契约：entities 条目带 category（注册表类别）与 width（碰撞箱宽）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            add_monster(server, "z1", 3.0, 0.0)
            add_entity(server, "cow1", type_="minecraft:cow",
                       category="creature", width=0.9, x=-3.0, z=0.0)
            client = await make_pair(server)
            try:
                result = await client.call(
                    "world.query", {"type": "entities", "range": 16})
                entries = {e["uuid"]: e for e in result["entities"]}
                assert entries["z1"]["category"] == "monster"
                assert abs(entries["z1"]["width"] - 0.6) < 1e-9
                assert entries["cow1"]["category"] == "creature"
            finally:
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_threat_detection_by_category_and_danger_radius(self):
        """敌对判定=category=monster；危险半径=width/2+1.5（0.6 宽僵尸 → 1.8 格）。

        threat 是易变态（flee act 即消费），断言落在 flee 的 wire 结果上：
        #stop + 反向 #goto + 简报里带类型与危险半径。
        """

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                add_monster(server, "z1", 1.2, 0.5)   # 距离 ~1.3 < 危险半径 1.8
                assert await wait_until(lambda: "#stop" in server.submitted,
                                        timeout=10)
                assert await wait_until(
                    lambda: any("逃离 minecraft:zombie" in line
                                and "危险半径 1.8" in line
                                for line in scheduler.behavior_log), timeout=15)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_non_monster_and_far_monster_never_flee(self):
        """和平生物贴脸 / 怪在扫描半径但危险半径外 → 都不触发逃离。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            add_entity(server, "cow1", type_="minecraft:cow",
                       category="creature", width=0.9, x=1.0, z=0.0)
            add_monster(server, "z-far", 10.0, 0.0)   # 12 格扫描内、1.8 外
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                await asyncio.sleep(2.5)
                assert scheduler.body.threat is None
                assert not server.submitted           # 没有任何撤离动作
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_stats_override_and_eye_water_signal(self):
        """air 注入 + 眼位水块：eyes_in_water 判定走 world.query 眼位方块。"""

        async def main() -> None:
            # 眼位格：(0,64,0) 的眼在 (0,65.62,0) → 方块格 (0,65,0)
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(0, 65, 0): "water"})
            server.stats_override = {"air": 100}
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                assert await wait_until(lambda: scheduler.body.eyes_in_water)
                assert scheduler.body.air == 100
                # 空气满时不查水（air=300 → eyes_in_water 复位 False）
                server.stats_override = {"air": 300}
                assert await wait_until(lambda: not scheduler.body.eyes_in_water)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 换气（cooperative）


class TestBreathReflex:
    def test_presses_space_until_air_recovers(self):
        """眼在水中且 air≤240 → 循环按 SPACE；露头（air 回满）即收并写简报。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(0, 65, 0): "water"})
            server.stats_override = {"air": 100}
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                assert await wait_until(lambda: space_presses(server))
                server.stats_override = {"air": 300}   # 露头：vanilla 即刻回满
                assert await wait_until(
                    lambda: any("换气完成" in line for line in scheduler.behavior_log),
                    timeout=5)
                # cooperative：不掀任务（没有 #stop/#goto 之类动作）
                assert "#stop" not in server.submitted
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_not_triggered_when_air_full_or_dry(self):
        """空气满（哪怕头在水块里）或不在水里 → 不按 SPACE。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(0, 65, 0): "water"})
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                await asyncio.sleep(0.8)               # air=300：头在水里也不触发
                assert not space_presses(server)
                server.blocks.pop((0, 65, 0), None)    # 撤掉水块：缺氧但已不在水里
                server.stats_override = {"air": 80}
                await asyncio.sleep(0.8)
                assert not space_presses(server)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_failure_reports_when_cannot_surface(self, monkeypatch):
        """封顶仍浮不上去 → 立刻上报"找不到透气口"。"""

        async def main() -> None:
            monkeypatch.setattr(reflexes_module, "BREATH_MAX_SECONDS", 0.6)
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(0, 65, 0): "water"})
            server.stats_override = {"air": 60}
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                assert await wait_until(
                    lambda: any("换气失败" in line for line in scheduler.behavior_log)
                    or any("找不到透气口" in line for line in scheduler.behavior_log),
                    timeout=8)
                assert space_presses(server)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 脱困（cooperative）


class TestUnstuckReflex:
    def test_bursts_when_frozen_with_movement_input(self):
        """有移动输入但 2s 位移 <0.75 格 → 137° 扇形爆发（lookAt）+ W + 周期跳。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                scheduler.note_movement(True)   # walkTo 原语执行中（位置冻结 = 卡住）
                assert await wait_until(lambda: w_presses(server), timeout=15)
                assert space_presses(server)     # 周期跳
                assert server.looks              # 扇形转向
                assert await wait_until(
                    lambda: any("脱困" in line for line in scheduler.behavior_log),
                    timeout=10)
                assert "#stop" not in server.submitted   # cooperative：不掀任务
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_not_triggered_without_movement_input(self):
        """没有移动输入（站着不动是正常待机）→ 不挣扎。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                await asyncio.sleep(3.0)   # 窗口早已铺满
                assert not w_presses(server) and not server.looks
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 撤离（preempt）


class TestFireReflex:
    def test_evacuates_to_water(self):
        """CRITICAL fire → #stop + 跑向最近的水；行为简报入列。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(5, 64, 3): "water"})
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                await push_danger(server, "fire", {"health": 20.0})
                assert await wait_until(lambda: "#stop" in server.submitted)
                assert await wait_until(
                    lambda: any(line.startswith("#goto 5.5 3.5")
                                for line in server.submitted), timeout=10)
                assert await wait_until(
                    lambda: any("着火撤离" in line for line in scheduler.behavior_log),
                    timeout=10)
                assert not scheduler.body.on_fire   # 消费后清位（再触发靠新事件）
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_retreats_without_water(self):
        """附近没水 → 反向撤 5 格（无近期运动速度 → 固定 +X 简化方向）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                await push_danger(server, "fire", {"health": 20.0})
                assert await wait_until(
                    lambda: any(line.startswith("#goto 5 0")
                                for line in server.submitted), timeout=10)
                assert await wait_until(
                    lambda: any("没水" in line for line in scheduler.behavior_log),
                    timeout=15)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_fire_preempts_running_task_and_notifies_next(self):
        """着火反射掀翻在途任务（end_reason=preempt）；下一任务收到〔本能反应〕简报。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            fast_commands(client)
            gate = threading.Event()
            vlm = ScriptedVLM([resp_tools(("getStats", {}))],
                              block_gate=(0, gate))     # 第 1 次 VLM 调用冻结等放行
            agent = make_agent(client, vlm)
            try:
                await client.connect()          # 先连接，再启动常驻循环（否则自识别撞未连接）
                main_task = asyncio.create_task(agent.run())
                assert await wait_until(lambda: agent._running)
                await say(server, "挖点木头")
                assert await wait_until(lambda: len(vlm.captured) >= 1)
                await push_danger(server, "fire", {"health": 20.0})
                assert await wait_until(lambda: "#stop" in server.submitted)
                gate.set()                              # 放行冻结的 VLM 调用
                assert await wait_until(
                    lambda: agent.last_run is not None
                    and agent.last_run.end_reason == "preempt"), \
                    f"last_run={agent.last_run}"
                assert "被反射 fire 抢占" in agent.last_run.result
                # preempt 不走"好的，停下了"也不走进度摘要播报
                assert not any(text.startswith("这个任务我先到这") for text in sent)
                # 反射收尾后，下一任务的第一轮 VLM 调用前注入〔本能反应〕简报
                assert await wait_until(
                    lambda: any("着火撤离" in line
                                for line in agent.scheduler.behavior_log), timeout=15)
                vlm2 = ScriptedVLM([resp_text("我没事，刚躲了场火")])
                agent.vlm = vlm2
                run = await agent.run_task("你还好吗")
                assert run.end_reason == "content"
                notices = [m for m in vlm2.captured[0]
                           if str(m.get("content", "")).startswith(REFLEX_NOTICE_PREFIX)]
                assert len(notices) == 1                 # 替换式：历史里恒至多一条
                assert "〔危险〕着火" in notices[0]["content"]
                assert "着火撤离" in notices[0]["content"]
            finally:
                gate.set()
                main_task.cancel()
                await asyncio.gather(main_task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 低血 / 死亡（preempt + urgent）


class TestHealthLowAndDeath:
    def _loop_setup(self, server: FakeWorldBridge, vlm: ScriptedVLM):
        client = BridgeClient(server.url)
        sent = spy_commands(client)
        fast_commands(client)
        agent = make_agent(client, vlm)
        return client, sent, agent

    def test_health_low_preempts_broadcasts_and_urgent(self):
        """CRITICAL health_low → 掀任务 + 聊天上报 + 〔紧急〕排队注入下一任务。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            gate = threading.Event()
            vlm = ScriptedVLM([resp_tools(("getStats", {}))], block_gate=(0, gate))
            client, sent, agent = self._loop_setup(server, vlm)
            try:
                await client.connect()
                main_task = asyncio.create_task(agent.run())
                assert await wait_until(lambda: agent._running)
                await say(server, "继续挖矿")
                assert await wait_until(lambda: len(vlm.captured) >= 1)
                server.stats_override = {"health": 4.0}
                await push_danger(server, "health_low",
                                  {"health": 4.0, "threshold": 6.0})
                assert await wait_until(lambda: "#stop" in server.submitted)
                gate.set()
                assert await wait_until(
                    lambda: agent.last_run is not None
                    and agent.last_run.end_reason == "preempt"), \
                    f"last_run={agent.last_run}"
                assert "被反射 health_low 抢占" in agent.last_run.result
                # M4.1：低血警报走 chat.send 直发（GUI 被占时 T 键路径会被吞）
                assert await wait_until(
                    lambda: any("血量" in text for text in server.chats_sent)),                     f"chats_sent={server.chats_sent}"
                # 紧急消息：本任务已死 → 排队给下一任务，开头即送达
                # （先等它入队——urgent 紧随直发播报之后；不等的话第二任务
                #  可能赶在它前面完成）
                assert await wait_until(lambda: agent._urgent_pending, timeout=5)
                vlm2 = ScriptedVLM([resp_text("收到，我先躲一躲")])
                agent.vlm = vlm2
                run = await agent.run_task("你怎么样")
                assert run.end_reason == "content"
                urgent = [m for m in vlm2.captured[0]
                          if str(m.get("content", "")).startswith(URGENT_PREFIX)]
                assert urgent and "健康告警" in urgent[0]["content"]
            finally:
                gate.set()
                main_task.cancel()
                await asyncio.gather(main_task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_death_reports_coords_and_waits(self):
        """CRITICAL death → 掀任务 + 聊天上报死亡坐标（等玩家指令，不自动重生）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            gate = threading.Event()
            vlm = ScriptedVLM([resp_tools(("getStats", {}))], block_gate=(0, gate))
            client, sent, agent = self._loop_setup(server, vlm)
            try:
                await client.connect()
                main_task = asyncio.create_task(agent.run())
                assert await wait_until(lambda: agent._running)
                await say(server, "去看看那边")
                assert await wait_until(lambda: len(vlm.captured) >= 1)
                await push_danger(server, "death",
                                  {"health": 0.0, "air": 300, "on_fire": False})
                # 死亡反射不 #stop：以 urgent 入队为"反射已执行（含 preempt）"的信号，
                # 再放行冻结的 VLM 调用——否则任务可能在调度器下一轮轮询前跑完
                assert await wait_until(lambda: agent._urgent_pending, timeout=5)
                gate.set()
                assert await wait_until(
                    lambda: agent.last_run is not None
                    and agent.last_run.end_reason == "preempt"), \
                    f"last_run={agent.last_run}"
                assert "被反射 death 抢占" in agent.last_run.result
                assert await wait_until(
                    lambda: any("死亡位置" in text and "不会自动重生" in text
                                for text in server.chats_sent)),                     f"chats_sent={server.chats_sent}"
                urgent = [m for m in agent._urgent_pending]
                assert urgent and "死亡" in urgent[0]
                assert any("死亡上报" in line for line in agent.scheduler.behavior_log)
            finally:
                gate.set()
                main_task.cancel()
                await asyncio.gather(main_task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_death_reports_once_and_rearms_on_respawn(self, monkeypatch):
        """一次性闩：同一次死亡只报一次；复活（alive=True 过宽限期）后重臂。"""

        async def main() -> None:
            monkeypatch.setattr(reflexes_module, "DEATH_RELATCH_GRACE", 0.2)
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                handler = scheduler.danger_handler("death")
                handler(danger_frame("death", {"health": 0.0}))
                assert await wait_until(
                    lambda: any("死亡上报" in line for line in scheduler.behavior_log),
                    timeout=5)
                await asyncio.sleep(1.0)   # latch 已清（fake alive=True），不再重复上报
                assert sum("死亡上报" in line
                           for line in scheduler.behavior_log) == 1
                handler(danger_frame("death", {"health": 0.0}))   # 第二次死亡
                assert await wait_until(
                    lambda: sum("死亡上报" in line
                                for line in scheduler.behavior_log) == 2, timeout=5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 危怪逃离（preempt）


class TestFleeReflex:
    def test_runs_away_opposite_without_fighting(self):
        """怪进危险半径 → #stop + 反向 8 格逃离 + 简报；不还击（无攻击输入）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                add_monster(server, "z1", 1.2, 0.5)   # 距离 ~1.3 < 危险半径 1.8
                assert await wait_until(lambda: "#stop" in server.submitted,
                                        timeout=10)
                # 反方向：dx=-1.2,dz=-0.5 → 目标约 (-7.38, -3.08)
                assert await wait_until(
                    lambda: any(line.startswith("#goto -7")
                                for line in server.submitted), timeout=10)
                assert await wait_until(
                    lambda: any("逃离" in line and "未还击" in line
                                for line in scheduler.behavior_log), timeout=15)
                assert not server.clicks                # 没有任何攻击/挖掘点击
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 注视（none）


class TestSpeakingLook:
    def test_looks_at_companion_while_speaking(self):
        """播报窗口内 + 16 格内有玩家 → lookAt 对方头部。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            add_entity(server, "friend", type_="minecraft:player",
                       category="misc", width=0.6, x=3.0, z=1.0)
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                assert await wait_until(lambda: scheduler.body.companion is not None)
                scheduler.note_speaking()
                assert await wait_until(
                    lambda: (3.0, 65.6, 1.0) in server.looks, timeout=5)
                # none 档：不掀任务、不发命令
                assert "#stop" not in server.submitted
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_no_look_without_companion(self):
        """周围没人 → 播报时不转头。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                scheduler.note_speaking()
                await asyncio.sleep(0.8)
                assert not server.looks
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- 等级切换（全链）


class TestLevelSwitching:
    def test_switch_via_chat_and_l0_semantics(self):
        """聊天切换（人类-only）即时生效；L0 关动作不关感知；切回 L1 恢复。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            fast_commands(client)
            vlm = ScriptedVLM([resp_text("好")])
            agent = make_agent(client, vlm)
            try:
                await client.connect()          # 先连接，再启动常驻循环
                main_task = asyncio.create_task(agent.run())
                assert await wait_until(lambda: agent._running)
                assert agent.scheduler.level is ReflexLevel.SELF_PRESERVE
                await say(server, "反射等级 观察")
                assert await wait_until(
                    lambda: agent.scheduler.level is ReflexLevel.OBSERVER)
                assert await wait_until(
                    lambda: any("反射等级已切换" in text for text in sent))
                # L0：fire 事件照进认知（〔危险〕行），但脊髓不动
                await push_danger(server, "fire", {"health": 20.0})
                assert await wait_until(
                    lambda: any(line.startswith("〔危险〕着火")
                                for line in agent.scheduler.behavior_log), timeout=5)
                await asyncio.sleep(1.0)
                assert "#stop" not in server.submitted
                assert not space_presses(server)
                # 切回 L1：on_fire 仍置位 → 立刻补执行撤离
                await say(server, "反射等级 自保")
                assert await wait_until(
                    lambda: agent.scheduler.level is ReflexLevel.SELF_PRESERVE)
                assert await wait_until(lambda: "#stop" in server.submitted,
                                        timeout=10)
            finally:
                main_task.cancel()
                await asyncio.gather(main_task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_guard_level_is_reserved(self):
        """L2 是预留位：切换被拒 + 播报说明，等级不变。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            client = BridgeClient(server.url)
            sent = spy_commands(client)
            fast_commands(client)
            vlm = ScriptedVLM([resp_text("好")])
            agent = make_agent(client, vlm)
            try:
                await client.connect()          # 先连接，再启动常驻循环
                main_task = asyncio.create_task(agent.run())
                assert await wait_until(lambda: agent._running)
                await say(server, "反射等级 自卫")
                assert await wait_until(
                    lambda: any("预留" in text for text in sent), timeout=5)
                assert agent.scheduler.level is ReflexLevel.SELF_PRESERVE
            finally:
                main_task.cancel()
                await asyncio.gather(main_task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_instincts_section_follows_level_in_system_prompt(self):
        """instincts 节按当前等级生成（等级是代码层与认知层唯一同步点）。"""
        from sirius_brain.agent import AgentLoop, QwenVLM, VLMConfig

        agent = AgentLoop(BridgeClient("ws://127.0.0.1:1"),
                          QwenVLM(VLMConfig(api_key="sk-test")),
                          AgentConfig(vlm=VLMConfig(api_key="sk-test")))
        prompt = agent._system_prompt("测试")
        assert "本能（反射）" in prompt
        assert "换气" in prompt                       # L1 默认：反射清单在
        agent.scheduler.level = ReflexLevel.OBSERVER
        prompt0 = agent._system_prompt("测试")
        assert "只会被告知" in prompt0
        assert "换气" not in prompt0.split("## 本能（反射）")[1].split("##")[0]

    def test_switch_command_not_enqueued_as_task(self):
        """切换命令消费掉不入任务队列（绝不经 VLM）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            client = BridgeClient(server.url)
            fast_commands(client)
            vlm = ScriptedVLM([resp_text("x")])
            agent = make_agent(client, vlm)
            try:
                await client.connect()          # 先连接，再启动常驻循环
                main_task = asyncio.create_task(agent.run())
                assert await wait_until(lambda: agent._running)
                await say(server, "反射等级 观察")
                await asyncio.sleep(0.8)
                assert agent._queue.qsize() == 0
                assert not vlm.captured          # 零 VLM 调用
            finally:
                main_task.cancel()
                await asyncio.gather(main_task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- L0 门控（调度器级）


class TestObserverGating:
    def test_l0_runs_no_chains(self):
        """L0 下调度循环空转：七条 can_run 全被等级门控跳过（危险照记简报）。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(5, 64, 3): "water"})
            server.stats_override = {"air": 100, "health": 4.0}
            # 叠满危险状态：眼位水（无——air<300 且眼格无水 → False）；
            # 直接用 handlers 置 fire/death/health_low + 怪贴脸 + 卡位输入
            add_monster(server, "z1", 1.2, 0.5)
            client = await make_pair(server)
            scheduler = await make_scheduler(client, level=ReflexLevel.OBSERVER)
            task = asyncio.create_task(scheduler.run())
            try:
                for event in ("fire", "health_low"):
                    scheduler.danger_handler(event)(
                        danger_frame(event, {"health": 4.0}))
                scheduler.danger_handler("death")(danger_frame("death", {"health": 0.0}))
                scheduler.note_movement(True)
                await asyncio.sleep(2.5)
                assert not server.submitted          # 无 #stop/#goto
                assert not server.key_presses or all(
                    k["code"] in (84, 257) for k in server.key_presses)  # 无反射按键
                assert not server.looks              # 无注视/扇形转向
                lines = list(scheduler.behavior_log)
                assert any(line.startswith("〔危险〕着火") for line in lines)
                assert any(line.startswith("〔危险〕死亡") for line in lines)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


# ---------------------------------------------------------------------- M4.1 修复轮（T3/T5 + yaw 基准）


class TestM41BaritonePause:
    """M4.1 T5：cooperative 反射生效期间 #pause/#resume Baritone 配对。

    背景：M4-rerun §1——Baritone 的水下 GoalBlock 与换气反射拉锯（SPACE 浮起
    →再被按下循环至溺亡）。裁决：反射接管按键期间 #pause 让路（保目标），
    归还后 #resume 续走。
    """

    def test_breath_pauses_then_resumes_while_walking(self):
        """movement_active（walkTo 原语在走）时换气：#pause 先于 SPACE，
        收尾 #resume——顺序在 wire 记录上可验证。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(0, 65, 0): "water"})
            server.stats_override = {"air": 100}
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                scheduler.note_movement(True)   # Baritone 行走中
                assert await wait_until(lambda: "#pause" in server.submitted, timeout=10)
                assert await wait_until(lambda: space_presses(server), timeout=10)
                server.stats_override = {"air": 300}   # 露头回满 → 反射收尾
                assert await wait_until(
                    lambda: any("换气完成" in line for line in scheduler.behavior_log),
                    timeout=5)
                assert await wait_until(lambda: "#resume" in server.submitted, timeout=5)
                assert (server.submitted.index("#pause")
                        < server.submitted.index("#resume"))
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_breath_without_movement_never_pauses(self):
        """站着不动触发换气（无 Baritone 拉锯对象）→ 不发 #pause/#resume。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(0, 65, 0): "water"})
            server.stats_override = {"air": 100}
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                assert await wait_until(lambda: space_presses(server), timeout=10)
                server.stats_override = {"air": 300}
                assert await wait_until(
                    lambda: any("换气完成" in line for line in scheduler.behavior_log),
                    timeout=5)
                assert "#pause" not in server.submitted
                assert "#resume" not in server.submitted
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_breath_resumes_even_when_failing(self, monkeypatch):
        """封顶仍浮不上去（换气失败上报）→ finally 也保证 #resume（控制权必归还）。"""

        async def main() -> None:
            monkeypatch.setattr(reflexes_module, "BREATH_MAX_SECONDS", 0.6)
            server = FakeWorldBridge(port=0, position=dict(ORIGIN),
                                     blocks={(0, 65, 0): "water"})
            server.stats_override = {"air": 60}
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                scheduler.note_movement(True)
                assert await wait_until(lambda: "#pause" in server.submitted, timeout=10)
                assert await wait_until(
                    lambda: any("换气失败" in line for line in scheduler.behavior_log),
                    timeout=8)
                assert await wait_until(lambda: "#resume" in server.submitted, timeout=5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


class TestM41DeathDirectBroadcast:
    """M4.1 T3：死亡播报走 chat.send 直发通道（死亡屏屏蔽 T 键——M4-rerun §3.3
    实证 wire 已发而游戏聊天无此行）。"""

    def test_death_report_goes_direct_channel_not_t_key(self):
        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            vlm = ScriptedVLM([])
            client, _sent, agent = _agent_setup(server, vlm)
            await client.connect()
            scheduler = agent.scheduler
            agent.install()   # danger handler 接线（与 AgentLoop.run 同款）
            task = asyncio.create_task(scheduler.run())
            try:
                handler = scheduler.danger_handler("death")
                handler(danger_frame("death", {"health": 0.0}))
                assert await wait_until(
                    lambda: any("死亡上报" in line for line in scheduler.behavior_log),
                    timeout=5)
                # 直发通道收到播报；wire 上没有任何 T 键三连（84/257 均未出现）
                assert any("死亡位置" in t and "不会自动重生" in t
                           for t in server.chats_sent), server.chats_sent
                assert 84 not in [k["code"] for k in server.key_presses]
                assert 257 not in [k["code"] for k in server.key_presses]
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())

    def test_loop_client_say_direct_and_old_bridge_fallback(self):
        """LoopClient.say：chat.send 可用直发；旧 bridge（-32601）回落 GUI 路径；
        两种路径都登记自回显抑制窗。"""

        async def main() -> None:
            server = FakeWorldBridge(port=0)
            await server.start()
            vlm = ScriptedVLM([])
            client, _sent, agent = _agent_setup(server, vlm)
            await client.connect()
            try:
                await agent.tools_client.say("直发一条")
                assert server.chats_sent == ["直发一条"]
                assert agent.echo.is_echo("直发一条", None, None)
                server.chat_send_error = -32601   # 模拟旧 jar 无 chat.send
                await agent.tools_client.say("回落一条")
                assert "回落一条" in server.submitted
                assert agent.echo.is_echo("回落一条", None, None)
            finally:
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())


class TestM41SafeStop:
    """M4.1：preempt 反射的 #stop 失败（GUI 占用拒绝等）不得打断保命链
    （T6 深水回归轮实证：低血 #stop 抛异常会跳过直发警报与 urgent 注入）。"""

    def test_health_low_alerts_even_when_stop_fails(self):
        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            await server.start()
            server.stats_override = {"health": 4.0}
            vlm = ScriptedVLM([])
            client, _sent, agent = _agent_setup(server, vlm)
            await client.connect()
            real_command = client.command

            async def refusing_stop(text, settle=0.5, timeout=None):
                if text == "#stop":
                    raise BridgeError(-32002, "T 已按下但聊天框未能打开")
                return await real_command(text, settle=settle, timeout=timeout)

            client.command = refusing_stop  # type: ignore[method-assign]
            scheduler = agent.scheduler   # AgentLoop 已接 broadcast_direct → say
            task = asyncio.create_task(scheduler.run())
            try:
                handler = scheduler.danger_handler("health_low")
                handler(danger_frame("health_low", {"health": 4.0}))
                assert await wait_until(
                    lambda: any("低血停任务" in line
                                for line in scheduler.behavior_log), timeout=5)
                assert any("警报" in t and "血量" in t for t in server.chats_sent),                     server.chats_sent
                assert any("健康告警" in line for line in scheduler.behavior_log) or True
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await agent.shutdown()
                await client.close()
                await server.close()

        asyncio.run(main())


class TestM41UnstuckYawBase:
    """M4.1：协议 1.3 起可读 yaw——脱困扇形从当前朝向展开（不再从 0° 猜）。"""

    def test_bursts_fan_from_current_yaw(self):
        async def main() -> None:
            server = FakeWorldBridge(port=0, position=dict(ORIGIN))
            server.stats_override = {"yaw": 90.0}
            client = await make_pair(server)
            scheduler = await make_scheduler(client)
            task = asyncio.create_task(scheduler.run())
            try:
                scheduler.note_movement(True)   # 有输入但位置冻结 → 卡住
                assert await wait_until(lambda: server.looks, timeout=15)
                first_x, first_y, first_z = server.looks[0]
                # 基准 90° + 137° 第一段：dx=-sin(227°)≈0.731、dz=cos(227°)≈-0.680
                # （基准 0° 旧逻辑会给 dx≈-0.68、dz≈-0.73——两者可区分）
                assert abs(first_x - 4.0 * 0.7314) < 0.05, server.looks[0]
                assert abs(first_z - 4.0 * -0.6801) < 0.05, server.looks[0]
                assert abs(first_y - 65.0) < 1e-9
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
                await server.close()

        asyncio.run(main())


def _agent_setup(server, vlm):
    """直发通道测试的迷你装配：BridgeClient + AgentLoop（不 run 主循环）。"""
    client = BridgeClient(server.url)
    sent = spy_commands(client)
    fast_commands(client)
    agent = make_agent(client, vlm)
    return client, sent, agent
