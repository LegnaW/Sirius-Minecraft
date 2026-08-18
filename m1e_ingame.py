# -*- coding: utf-8 -*-
"""M1 in-game 补充验证：进世界后的 getStats / world.query / 截图三连。"""
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
OUT = ROOT / "docs_agent/m1-evidence/m1e_ingame.jpg"


def read_token() -> str:
    m = re.search(r'token\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("token not found")
    return m.group(1)


async def main() -> None:
    client = BridgeClient(BridgeConfig(url="ws://127.0.0.1:8765", token=read_token()))
    async with client:
        stats = await client.call("getStats", {})
        print(f"[1] getStats: {stats}")
        assert stats.get("in_game") is True, "未检测到已进入世界"
        assert "health" in stats and "position" in stats

        blocks = await client.call("world.query", {"type": "blocks", "range": 6}, timeout=30)
        kinds = {}
        for b in blocks.get("blocks", []):
            kinds[b["block"]] = kinds.get(b["block"], 0) + 1
        top = sorted(kinds.items(), key=lambda kv: -kv[1])[:8]
        print(f"[2] world.query(blocks r=6): count={blocks.get('count')} truncated={blocks.get('truncated')}")
        print(f"    top blocks: {top}")
        assert blocks.get("count", 0) > 0

        ents = await client.call("world.query", {"type": "entities", "range": 32})
        print(f"[3] world.query(entities r=32): count={ents.get('count')} -> "
              f"{[(e.get('name') or e.get('type'), {k: round(v,1) for k, v in e['position'].items()}) for e in ents.get('entities', [])[:6]]}")

        shot = await client.call("screenshot", {"tier": "full"}, timeout=30)
        OUT.write_bytes(base64.b64decode(shot["image_b64"]))
        print(f"[4] screenshot: {shot['width']}x{shot['height']} -> {OUT} ({OUT.stat().st_size/1024:.0f} KB)")

    print("\nIN-GAME PASS")


if __name__ == "__main__":
    asyncio.run(main())
