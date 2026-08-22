"""M4 真机反射探针（1/2）：溺水换气 + 等级切换（同路径）——零 VLM token。

用法：sirius-brain/ 下 ./.venv/Scripts/python.exe ../m4_reflex_probe.py

- 挂真实 AgentLoop（不派任务→VLM 零调用），调度器随 run() 常驻
- 换气：Baritone 走进 62 格外的水体（目标取水中格），头没入后 air 下降，
  反射应自动按 SPACE 上浮直到 air 回满
- 等级切换：单机世界无第二玩家（bot 自己的 chat 被自回显过滤），走
  _apply_reflex_level 同一路径（解析逻辑由单测覆盖），播报确认上 wire
"""
import asyncio
import json
import time

from sirius_brain.bridge import BridgeClient
from sirius_brain.agent.config import AgentConfig
from sirius_brain.agent.loop import AgentLoop
from sirius_brain.agent.reflexes import ReflexLevel
from sirius_brain.agent.vlm import QwenVLM

SENT: list[str] = []


async def spy_broadcasts(client: BridgeClient) -> None:
    real = client.command

    async def spy(text: str, settle: float = 0.5, timeout=None):  # noqa: ANN001, ANN202
        SENT.append(text)
        return await real(text, settle=settle, timeout=timeout)

    client.command = spy  # type: ignore[method-assign]


async def wait_until(predicate, timeout: float = 30.0):  # noqa: ANN001, ANN202
    deadline = time.perf_counter() + timeout
    last = None
    while time.perf_counter() < deadline:
        last = predicate()
        if last:
            return last
        await asyncio.sleep(0.2)
    return predicate()


async def main() -> None:
    cfg = AgentConfig.from_local_md("../local.md")
    client = BridgeClient(cfg.bridge)
    agent = AgentLoop(client, QwenVLM(cfg.vlm), cfg)
    agent.install()
    await spy_broadcasts(client)
    await client.connect()
    runner = asyncio.create_task(agent.run())
    try:
        assert await wait_until(lambda: agent._running, 15), "AgentLoop 未就绪"
        stats = await client.call("getStats")
        print(f"[0] 起点 {stats['position']} air={stats['air']} "
              f"level={agent.scheduler.level.value}")

        # ---------------- 等级切换（同路径：_apply_reflex_level）----------------
        await agent._apply_reflex_level(ReflexLevel.OBSERVER)
        assert await wait_until(lambda: any("反射等级已切换" in t for t in SENT), 10)
        assert agent.scheduler.level is ReflexLevel.OBSERVER
        print(f"[1] 等级切换 → 观察：{[t for t in SENT if '反射等级' in t][-1]}")
        await agent._apply_reflex_level(ReflexLevel.SELF_PRESERVE)
        assert await wait_until(lambda: agent.scheduler.level is ReflexLevel.SELF_PRESERVE)
        print("[1] 等级切回 → 自保（instincts 将随下个任务生效；当前反射即时生效）")
        await agent._apply_reflex_level(ReflexLevel.GUARD)
        assert await wait_until(lambda: any("预留" in t for t in SENT), 10)
        assert agent.scheduler.level is ReflexLevel.SELF_PRESERVE
        print(f"[1] L2 预留位拒绝：{[t for t in SENT if '预留' in t][-1]}")

        # ---------------- 溺水换气（零 VLM）----------------
        water = await client.call("world.query",
                                  {"type": "blocks", "range": 64,
                                   "filter": ["minecraft:water"]})
        blocks = water["blocks"]
        assert blocks, "64 格内没有水"
        # 取距自己最远命中的一半处当目标（水体内部，不是岸边）
        body = agent.scheduler.body
        target = blocks[min(20, len(blocks) - 1)]
        tx, tz = target["x"] + 0.5, target["z"] + 0.5
        print(f"[2] 水体最近 {blocks[0]['x']},{blocks[0]['y']},{blocks[0]['z']}"
              f"；游向内部点 ({tx},{tz})（共 {water['count']} 格命中）")
        await client.command(f"#goto {tx:g} {tz:g}")
        t0 = time.perf_counter()
        surfaced = False
        min_air = 300
        breath_log = []
        while time.perf_counter() - t0 < 90:
            stats = await client.call("getStats")
            air = stats.get("air", 300)
            min_air = min(min_air, air)
            pos = stats["position"]
            log_now = [l for l in agent.scheduler.behavior_log
                       if "换气" in l]
            if log_now != breath_log:
                breath_log = log_now
                print(f"    t={time.perf_counter()-t0:5.1f}s air={air:3d} "
                      f"eyes_in_water={body.eyes_in_water} pos=({pos['x']:.0f},"
                      f"{pos['y']:.0f},{pos['z']:.0f}) 简报={breath_log[-1]}")
            if any("换气完成" in l for l in breath_log):
                surfaced = True
                break
            await asyncio.sleep(0.5)
        stats = await client.call("getStats")
        print(f"[2] 结束：min_air={min_air} 当前 air={stats['air']} surfaced={surfed_ok(surfaced)}")
        await client.command("#stop")
        print("[2] 换气简报：", [l for l in agent.scheduler.behavior_log if "换气" in l])
    finally:
        await agent.shutdown()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await client.close()


def surfed_ok(surfaced: bool) -> bool:
    return surfaced


if __name__ == "__main__":
    asyncio.run(main())
