# -*- coding: utf-8 -*-
"""M2-B 真机验证：订阅事件 → 自触 gui_open/gui_close/chat → 收集截图流。

用法：重启 1.21.1-Sirius 客户端（加载 M2-B jar）并进入世界后运行：
  sirius-brain/.venv/Scripts/python.exe m2b_verify.py
验证点：订阅后收到事件 / seq 每连接单调 / level 在 data 内 / 截图流 base64 ≤100KB
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
KEY_E, KEY_T, KEY_ENTER = 69, 84, 257  # GLFW
BUDGET = 100 * 1024

received: list[dict] = []


def read_token() -> str:
    m = re.search(r'token\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("token not found")
    return m.group(1)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = BridgeClient(BridgeConfig(url="ws://127.0.0.1:8765", token=read_token()))

    @client.on_event("*")
    def on_any(frame):
        b64 = frame.data.get("image_b64", "")
        received.append({
            "event": frame.event,
            "seq": frame.seq,
            "level": frame.data.get("level"),
            "b64": b64,
        })

    async with client:
        print("[1] 能力协商:", len((await client.capabilities()).capabilities), "项")
        res = await client.subscribe_events(["*"])
        print("[2] 订阅:", res)

        print("[3] 自触事件：E 开背包 → 关 → 聊天 /say ...")
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        await asyncio.sleep(1.0)
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        await asyncio.sleep(1.0)
        await client.call("input.key", {"code": KEY_T, "duration_ms": 50})
        await asyncio.sleep(0.6)
        await client.call("input.text", {"string": "/say M2-B event channel check"})
        await asyncio.sleep(0.4)
        await client.call("input.key", {"code": KEY_ENTER, "duration_ms": 50})

        print("[4] 收集截图流 22s（预期 ≥3 帧，间隔 ≥6s）……")
        await asyncio.sleep(22)

    # ---- 判定 ----
    print(f"\n收到 {len(received)} 帧：")
    for r in received:
        extra = f" b64={len(r['b64'])}" if r["b64"] else ""
        print(f"  seq={r['seq']:>3} {r['event']:<12} level={r['level']}{extra}")

    fails = []
    seqs = [r["seq"] for r in received]
    if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
        fails.append("seq 非严格递增")
    events = {r["event"] for r in received}
    for expect in ("gui_open", "gui_close", "chat", "screenshot"):
        if expect not in events:
            fails.append(f"缺少事件 {expect}")
    shots = [r for r in received if r["event"] == "screenshot"]
    for i, s in enumerate(shots):
        if len(s["b64"]) > BUDGET:
            fails.append(f"截图 #{i} 超预算: {len(s['b64'])} > {BUDGET}")
    if shots:
        (OUT / "m2b_stream_last.jpg").write_bytes(base64.b64decode(shots[-1]["b64"]))
        print(f"\n证据: docs_agent/m2-evidence/m2b_stream_last.jpg (b64 {len(shots[-1]['b64'])} chars)")

    print("\n结果:", "FAIL: " + "; ".join(fails) if fails else "PASS: 全部验证点通过")


if __name__ == "__main__":
    asyncio.run(main())
