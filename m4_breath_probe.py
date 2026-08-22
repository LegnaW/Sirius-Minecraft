"""M4 真机反射探针（2/2）：溺水换气——Bot 从崖上潜入深水，验证换气反射。"""
import asyncio
import time

from sirius_brain.bridge import BridgeClient
from sirius_brain.agent.config import AgentConfig
from sirius_brain.agent.loop import AgentLoop
from sirius_brain.agent.vlm import QwenVLM


async def wait_until(predicate, timeout: float = 30.0):  # noqa: ANN001, ANN202
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.2)
    return predicate()


async def main() -> None:
    cfg = AgentConfig.from_local_md("../local.md")
    client = BridgeClient(cfg.bridge)
    agent = AgentLoop(client, QwenVLM(cfg.vlm), cfg)
    agent.install()
    await client.connect()
    runner = asyncio.create_task(agent.run())
    body = agent.scheduler.body
    try:
        assert await wait_until(lambda: agent._running, 15)
        stats = await client.call("getStats")
        pos = stats["position"]
        print(f"[0] 起点 ({pos['x']:.1f},{pos['y']:.1f},{pos['z']:.1f}) air={stats['air']}")
        water = await client.call("world.query",
                                  {"type": "blocks", "range": 24,
                                   "filter": ["minecraft:water"]})
        assert water["blocks"], "24 格内没水"
        w = water["blocks"][0]
        print(f"[1] 最近水格 ({w['x']},{w['y']},{w['z']})，#goto 三参 GoalBlock 潜入")
        await client.command(f"#goto {w['x']} {w['y']} {w['z']}")
        t0 = time.perf_counter()
        min_air, entered, surfaced, logged = 300, False, False, []
        while time.perf_counter() - t0 < 120:
            stats = await client.call("getStats")
            air = stats.get("air", 300)
            min_air = min(min_air, air)
            pos = stats["position"]
            now_log = [l for l in agent.scheduler.behavior_log if "换气" in l]
            if now_log != logged:
                logged = now_log
                print(f"    t={time.perf_counter()-t0:5.1f}s air={air:3d} "
                      f"eyes={body.eyes_in_water} y={pos['y']:.0f} 简报={logged[-1]}")
            if air < 300:
                entered = True
            if any("换气完成" in l for l in logged):
                surfaced = True
                break
            if air < 60:
                print("    [!] air<60 仍未上浮，人工介入循环结束")
                break
            await asyncio.sleep(0.5)
        print(f"[2] 结果：入水={entered} min_air={min_air} surfaced={surfaced}")
        print("[2] 简报：", [l for l in agent.scheduler.behavior_log if "换气" in l])
        await client.command("#stop")
    finally:
        await agent.shutdown()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
