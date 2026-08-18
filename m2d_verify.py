# -*- coding: utf-8 -*-
"""M2-D 真机验证（look/lookAt/command）。收官验收另见 m2_final.py。

用法：重启客户端（加载 M2-D jar）进世界后运行。
验证点：look 视角变化（两次 look 截图像素差）/ lookAt 竖直向上 pitch≈-90 / command() 给苹果入包
"""
import asyncio
import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "sirius-brain"))

from sirius_brain.bridge.client import BridgeClient, BridgeError  # noqa: E402
from sirius_brain.bridge.config import BridgeConfig  # noqa: E402

TOML = ROOT / ".minecraft/versions/1.21.1-Sirius/config/sirius_bridge.toml"
OUT = ROOT / "docs_agent/m2-evidence"
KEY_E = 69


def read_token() -> str:
    m = re.search(r'token\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("token not found")
    return m.group(1)


async def shot(client, name) -> None:
    res = await client.call("screenshot", {"tier": "full", "quality": 80})
    (OUT / name).write_bytes(base64.b64decode(res["image_b64"]))
    print(f"    截图 {name}")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = BridgeClient(BridgeConfig(url="ws://127.0.0.1:8765", token=read_token()))
    fails = []
    async with client:
        print("[1] look 绝对视角（0° vs 180°，两截图对比）:")
        r1 = await client.call("look", {"yaw": 0, "pitch": 0})
        print("   ", r1)
        await asyncio.sleep(0.8)
        await shot(client, "m2d_look_0.jpg")
        r2 = await client.call("look", {"yaw": 180, "pitch": 0})
        await asyncio.sleep(0.8)
        await shot(client, "m2d_look_180.jpg")
        if not r1.get("looked") or not r2.get("looked"):
            fails.append("look 未生效")

        print("[2] lookAt 竖直向上（头顶 10 格 → pitch 应≈-90）:")
        stats = await client.call("getStats", {})
        p = stats["position"]
        r = await client.call("lookAt", {"x": p["x"], "y": p["y"] + 10, "z": p["z"]})
        print("   ", r)
        if not r.get("looked") or abs(r["pitch"] - (-90.0)) > 3:
            fails.append(f"lookAt 竖直 pitch 异常: {r.get('pitch')}")

        print("[3] command() 给 1 苹果 → 开背包验证:")
        await client.command("/give @s minecraft:apple 1")
        await asyncio.sleep(1.0)
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        await asyncio.sleep(0.8)
        gui = await client.call("getGuiState", {})
        items = [s for s in gui.get("slots", []) if s.get("item") == "minecraft:apple"]
        print(f"    apple slots: {items}")
        if not items:
            fails.append("command() /give 后未见苹果")
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})

    print("\n结果:", "FAIL: " + "; ".join(fails) if fails else "PASS（look 视角差请用 compare_shots.ps1 目验 m2d_look_0/180.jpg）")


if __name__ == "__main__":
    asyncio.run(main())
