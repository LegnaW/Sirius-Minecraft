# -*- coding: utf-8 -*-
"""M2-A 真机验证：按 E 开背包 → 截图确认 → 再按 E 关闭（风险前置单点验证）。"""
import asyncio
import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "sirius-brain"))

from sirius_brain.bridge.client import BridgeClient  # noqa: E402
from sirius_brain.bridge.config import BridgeConfig  # noqa: E402

TOML = ROOT / ".minecraft/versions/1.21.1-Sirius/config/sirius_bridge.toml"
OUT = ROOT / "docs_agent/m2-evidence"


def read_token() -> str:
    m = re.search(r'token\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("token not found")
    return m.group(1)


async def shot(client, name: str) -> None:
    s = await client.call("screenshot", {"tier": "full"}, timeout=30)
    OUT.mkdir(exist_ok=True)
    p = OUT / name
    p.write_bytes(base64.b64decode(s["image_b64"]))
    print(f"    screenshot -> {p.name} ({p.stat().st_size/1024:.0f} KB)")


async def main() -> None:
    client = BridgeClient(BridgeConfig(url="ws://127.0.0.1:8765", token=read_token()))
    async with client:
        stats = await client.call("getStats", {})
        assert stats.get("in_game"), f"不在世界中: {stats}"
        print(f"[0] in_game, mode={stats.get('game_mode')}")

        print("[1] 按 E 开背包...")
        r = await client.call("input.key", {"code": "E"})
        print(f"    input.key -> {r}")
        await asyncio.sleep(1.0)
        await shot(client, "m2a_inventory_open.jpg")

        print("[2] 鼠标移动到屏幕中央附近...")
        r = await client.call("input.mouseMove", {"x": 427, "y": 240})
        print(f"    input.mouseMove -> {r}")
        await asyncio.sleep(0.5)

        print("[3] 再按 E 关背包...")
        r = await client.call("input.key", {"code": "E"})
        print(f"    input.key -> {r}")
        await asyncio.sleep(1.0)
        await shot(client, "m2a_inventory_closed.jpg")

    print("\nM2-A REAL-MACHINE CHECK DONE (看图确认背包开/关)")


if __name__ == "__main__":
    asyncio.run(main())
