# -*- coding: utf-8 -*-
"""M2-A 本机真机验证：hello/能力协商 → getStats →（若在游戏中）注入 E 开关背包 + 前后截图。

用法：启动 1.21.1-Sirius 实例（标题屏可验连接；进世界可验注入）后运行：
  sirius-brain/.venv/Scripts/python.exe m2a_verify.py
证据输出：docs_agent/m2-evidence/m2a_*.jpg
"""
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
KEY_E = 69  # GLFW key code


def read_token() -> str:
    m = re.search(r'token\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("token not found in " + str(TOML))
    return m.group(1)


async def shot(client: BridgeClient, name: str) -> Path:
    res = await client.call("screenshot", {"tier": "full", "quality": 80})
    path = OUT / name
    path.write_bytes(base64.b64decode(res["image_b64"]))
    print(f"  screenshot {name}: {res['width']}x{res['height']} q{res['quality']} "
          f"{'downscaled' if res['downscaled'] else 'full'} -> {path.relative_to(ROOT)}")
    return path


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = BridgeClient(BridgeConfig(url="ws://127.0.0.1:8765", token=read_token()))
    async with client:
        print(f"[1] hello: {client.hello_result.status if client.hello_result else 'n/a'}")

        info = await client.capabilities()
        names = sorted(c.name for c in info.capabilities)
        print(f"[2] capabilities: {len(names)} 项, protocol {info.protocol_version}")
        print(f"    input.*: {[n for n in names if n.startswith('input.')]}")

        stats = await client.call("getStats", {})
        print(f"[3] getStats: in_game={stats.get('in_game')}", end="")
        if stats.get("in_game"):
            print(f" health={stats.get('health')} mode={stats.get('game_mode')} pos={stats.get('position')}")
        else:
            print("（标题屏——连接验证完成；注入验证需进入世界后重跑）")
            await shot(client, "m2a_title.jpg")
            return

        print("[4] 注入 E 开关背包（事件层注入）……")
        before = await shot(client, "m2a_before.jpg")
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        await asyncio.sleep(1.5)  # 等 GUI 打开动画
        opened = await shot(client, "m2a_opened.jpg")
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        await asyncio.sleep(1.0)
        closed = await shot(client, "m2a_closed.jpg")
        print(f"[5] 完成：{before.name} / {opened.name} / {closed.name}（像素差用 compare_shots.ps1 判定）")


if __name__ == "__main__":
    asyncio.run(main())
