"""M4 真机反射探针（3/3）：Baritone 200 格长距走 + 沿途危怪逃离——零 VLM token。

- Primitives.walk_to 走 ~200 格（真 Baritone 寻路，含地形起伏/涉水）
- ReflexScheduler 常驻：途中过水换气、贴身怪逃离全自动；记录触发时间线
"""
import asyncio
import math
import time

from sirius_brain.bridge import BridgeClient
from sirius_brain.agent.config import AgentConfig
from sirius_brain.agent.loop import AgentLoop
from sirius_brain.agent.primitives import Primitives
from sirius_brain.agent.vlm import QwenVLM


async def main() -> None:
    cfg = AgentConfig.from_local_md("../local.md")
    client = BridgeClient(cfg.bridge)
    agent = AgentLoop(client, QwenVLM(cfg.vlm), cfg)
    agent.install()
    await client.connect()
    runner = asyncio.create_task(agent.run())
    try:
        await asyncio.sleep(2)
        stats = await client.call("getStats")
        start = stats["position"]
        sx, sz = start["x"], start["z"]
        # 目标：水平 ~200 格外
        tx, tz = 120.0, 40.0
        dist0 = math.hypot(tx - sx, tz - sz)
        print(f"[0] 起点 ({sx:.1f},{start['y']:.1f},{sz:.1f}) → 目标 ({tx},{tz})"
              f"（水平 {dist0:.0f} 格）")
        prims = Primitives(agent.tools_client, poll_interval=0.5)  # M4.1：走 LoopClient——command 与反射/播报共用命令锁，杜绝 wire 交错
        walk_task = asyncio.create_task(prims.walk_to(tx, tz, timeout=420.0))
        t0 = time.perf_counter()
        seen_logs: list[str] = []
        max_disp = 0.0
        while not walk_task.done():
            await asyncio.sleep(1.0)
            stats = await client.call("getStats")
            p = stats["position"]
            disp = math.hypot(p["x"] - sx, p["z"] - sz)
            max_disp = max(max_disp, disp)
            now = list(agent.scheduler.behavior_log)
            for line in now:
                if line not in seen_logs:
                    seen_logs.append(line)
                    print(f"    t={time.perf_counter()-t0:6.1f}s "
                          f"disp={disp:5.1f} air={stats['air']:3d} 反射简报={line}")
        outcome = walk_task.result()
        elapsed = time.perf_counter() - t0
        stats = await client.call("getStats")
        p = stats["position"]
        final_d = math.hypot(p["x"] - tx, p["z"] - tz)
        print(f"[1] walk_to 返回：{outcome.text}")
        print(f"[1] 耗时 {elapsed:.0f}s，最大位移 {max_disp:.1f} 格，"
              f"终点 ({p['x']:.1f},{p['y']:.1f},{p['z']:.1f}) 距目标 {final_d:.1f} 格")
        print(f"[1] 途中反射简报 {len(seen_logs)} 条")
    finally:
        await agent.shutdown()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
