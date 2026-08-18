# -*- coding: utf-8 -*-
"""M1-E 真机验收：连接真 sirius-bridge Mod，能力协商 + getStats + world.query + 截图存盘。

用法：
  1. 启动 HMCL 实例 1.21.1-Sirius（进入标题界面即可，截图含标题屏）
  2. python m1e_acceptance.py [token]
     token 省略时自动从 ..\.minecraft\versions\1.21.1-Sirius\config\sirius_bridge.toml 读取
"""
import asyncio
import base64
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "sirius-brain"))

from sirius_brain.bridge.client import BridgeClient  # noqa: E402
from sirius_brain.bridge.config import BridgeConfig  # noqa: E402

TOML = ROOT / ".minecraft/versions/1.21.1-Sirius/config/sirius_bridge.toml"
OUT_DIR = ROOT / "docs_agent/m1-evidence"
URL = "ws://127.0.0.1:8765"


def read_token() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    text = TOML.read_text(encoding="utf-8")
    m = re.search(r'token\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit(f"[!] 未在 {TOML} 找到 token")
    return m.group(1)


async def main() -> None:
    token = read_token()
    print(f"[*] token: {token[:8]}...")
    client = BridgeClient(BridgeConfig(url=URL, token=token))
    async with client:
        # 1. 能力协商
        caps = await client.capabilities()
        names = [c.name for c in caps.capabilities]
        print(f"[1] 能力协商: protocol {caps.protocol_version}, {len(names)} 项: {' '.join(names)}")
        assert "screenshot" in names and "world.query" in names

        # 2. getStats（标题界面应为 in_game:false；进入世界则为完整状态）
        stats = await client.call("getStats", {})
        print(f"[2] getStats: {stats}")
        assert "in_game" in stats

        # 3. world.query（未进世界应优雅降级）
        wq = await client.call("world.query", {"type": "entities", "range": 8})
        print(f"[3] world.query(entities): {wq}")
        assert "in_game" in wq

        # 4. screenshot 存盘
        shot = await client.call("screenshot", {"tier": "full"}, timeout=30)
        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / "m1e_screenshot.jpg"
        out.write_bytes(base64.b64decode(shot["image_b64"]))
        size_kb = out.stat().st_size / 1024
        print(f"[4] screenshot: {shot['width']}x{shot['height']} q{shot.get('quality')} "
              f"downscaled={shot.get('downscaled')} -> {out} ({size_kb:.0f} KB)")
        assert shot["format"] == "jpeg" and size_kb > 5

    print("\nM1-E ACCEPTANCE PASS")


if __name__ == "__main__":
    asyncio.run(main())
