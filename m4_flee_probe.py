"""M4 真机反射探针（4/4）：危怪逃离——夜晚站桩，怪贴身触发反向撤离。零 VLM。"""
import asyncio
import math
import time

from sirius_brain.bridge import BridgeClient
from sirius_brain.agent.config import AgentConfig
from sirius_brain.agent.loop import AgentLoop
from sirius_brain.agent.vlm import QwenVLM

SENT: list[str] = []


async def main() -> None:
    cfg = AgentConfig.from_local_md("../local.md")
    client = BridgeClient(cfg.bridge)
    real_command = client.command

    async def spy(text: str, settle: float = 0.5, timeout=None):  # noqa: ANN001, ANN202
        SENT.append(text)
        return await real_command(text, settle=settle, timeout=timeout)

    client.command = spy  # type: ignore[method-assign]
    agent = AgentLoop(client, QwenVLM(cfg.vlm), cfg)
    agent.install()
    await client.connect()
    runner = asyncio.create_task(agent.run())
    try:
        await asyncio.sleep(2)
        s = await client.call("getStats")
        p0 = s["position"]
        print(f"[0] 站桩 ({p0['x']:.1f},{p0['y']:.1f},{p0['z']:.1f})，等待夜里怪物靠近")
        t0 = time.perf_counter()
        seen_logs: list[str] = []
        tracked: dict[str, float] = {}
        fled = False
        while time.perf_counter() - t0 < 300:
            s = await client.call("getStats")
            p = s["position"]
            ents = await client.call("world.query", {"type": "entities", "range": 48})
            for e in ents["entities"]:
                if e.get("category") == "monster":
                    q = e["position"]
                    d = math.dist((p["x"], p["y"], p["z"]), (q["x"], q["y"], q["z"]))
                    key = e["uuid"][:8]
                    if key not in tracked or abs(tracked[key] - d) > 0.8:
                        tracked[key] = d
                        marker = " <<< 危险半径内" if d <= 1.8 else ""
                        print(f"  t={time.perf_counter()-t0:6.1f}s {e['type']}({key}) "
                              f"3D {d:5.1f}{marker}")
            now = list(agent.scheduler.behavior_log)
            for line in now:
                if line not in seen_logs:
                    seen_logs.append(line)
                    print(f"  t={time.perf_counter()-t0:6.1f}s 简报={line}")
            if any("逃离" in line for line in seen_logs):
                fled = True
                await asyncio.sleep(6)   # 观察撤离后的动态（追击→再逃循环）
                break
            await asyncio.sleep(1.0)
        print(f"[1] FLEE={'PASS' if fled else 'FAIL'}")
        print("[1] wire 命令（#stop/#goto/聊天）:", SENT[-12:])
        print("[1] 简报:", [l for l in agent.scheduler.behavior_log
                            if "逃离" in l or "危险" in l])
    finally:
        await agent.shutdown()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
