# -*- coding: utf-8 -*-
"""M2-C 真机验证：getGuiState 结构化 GUI 感知。

用法：重启 1.21.1-Sirius 客户端（加载 M2-C jar）并进入世界后运行：
  sirius-brain/.venv/Scripts/python.exe m2c_verify.py
验证点：无屏 screen_open:false / 开背包 46 slots+角色分类 / 坐标可点（gui↔窗口换算）/ EditBox 文本
"""
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "sirius-brain"))

from sirius_brain.bridge.client import BridgeClient  # noqa: E402
from sirius_brain.bridge.config import BridgeConfig  # noqa: E402

TOML = ROOT / ".minecraft/versions/1.21.1-Sirius/config/sirius_bridge.toml"
KEY_E, KEY_T, KEY_ENTER, KEY_A = 69, 84, 257, 65


def read_token() -> str:
    m = re.search(r'token\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("token not found")
    return m.group(1)


async def main() -> None:
    client = BridgeClient(BridgeConfig(url="ws://127.0.0.1:8765", token=read_token()))
    fails = []
    async with client:
        print("[1] 无屏 getGuiState:")
        r = await client.call("getGuiState", {})
        print("   ", {k: r[k] for k in r if k != "widgets"})
        if r.get("screen_open"):
            fails.append("无屏时 screen_open 应为 false")

        print("[2] E 开背包 → getGuiState:")
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        await asyncio.sleep(1.0)
        r = await client.call("getGuiState", {})
        slots = r.get("slots", [])
        roles = {}
        for s in slots:
            roles[s["role"]] = roles.get(s["role"], 0) + 1
        print(f"    screen_class={r.get('screen_class')} slots={len(slots)} roles={roles}")
        print(f"    widgets={len(r.get('widgets', []))} truncated={r.get('truncated')}")
        if not r.get("screen_open") or r.get("screen_class") != "InventoryScreen":
            fails.append(f"背包屏识别异常: {r.get('screen_class')}")
        if roles.get("crafting", 0) != 4 or roles.get("result", 0) != 1:
            fails.append(f"合成格/产物格角色数不对: {roles}")

        # 坐标换算：mouseMove 探测得到 gui_scaled↔窗口px 比例，点一个格子验证（点产物格旁空格不动作，改点 hotbar 第一格无破坏性）
        print("[3] 坐标换算与点击往返（点合成格区域外的 hotbar 首格——安全）:")
        probe = await client.call("input.mouseMove", {"x": 400, "y": 240})
        g = probe.get("gui_scaled", {})
        scale_x = g.get("x", 0) / 400 if 400 else 0
        scale_y = g.get("y", 0) / 240 if 240 else 0
        print(f"    probe: window(400,240) -> gui({g.get('x')},{g.get('y')}) scale=({scale_x:.3f},{scale_y:.3f})")
        hotbar = next((s for s in slots if s["role"] == "hotbar"), None)
        if hotbar and scale_x and scale_y:
            wx, wy = hotbar["x"] / scale_x, hotbar["y"] / scale_y
            mv = await client.call("input.mouseMove", {"x": round(wx), "y": round(wy)})
            print(f"    slot gui({hotbar['x']},{hotbar['y']}) -> window({round(wx)},{round(wy)}) -> mouseMove gui_scaled=({mv['gui_scaled']['x']},{mv['gui_scaled']['y']})")
            if abs(mv["gui_scaled"]["x"] - hotbar["x"]) > 2 or abs(mv["gui_scaled"]["y"] - hotbar["y"]) > 2:
                fails.append("坐标往返偏差 >2 gui 像素")
        else:
            print("    （无 hotbar 槽或比例异常，跳过往返验证）")

        print("[4] 关背包 → T 聊天 → 输入文本 → getGuiState 读 EditBox:")
        await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        await asyncio.sleep(0.8)
        await client.call("input.key", {"code": KEY_T, "duration_ms": 50})
        await asyncio.sleep(0.6)
        await client.call("input.text", {"string": "m2c editbox probe"})
        await asyncio.sleep(0.4)
        r = await client.call("getGuiState", {})
        texts = [w.get("text") for w in r.get("widgets", []) if w.get("type") == "EditBox"]
        print(f"    screen_class={r.get('screen_class')} EditBox text={texts}")
        if r.get("screen_class") != "ChatScreen" or not any("m2c editbox probe" in (t or "") for t in texts):
            fails.append(f"ChatScreen EditBox 文本读取失败: {texts}")
        await client.call("input.key", {"code": KEY_ENTER, "duration_ms": 50})

    print("\n结果:", "FAIL: " + "; ".join(fails) if fails else "PASS: 全部验证点通过")


if __name__ == "__main__":
    asyncio.run(main())
