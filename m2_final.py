# -*- coding: utf-8 -*-
"""M2 里程碑收官验收：纯脚本重放"按 E 开背包 → 拖木头 → 合成工作台"。

技术路线（M2 全链路）：command(/give) → input.key E → getGuiState 结构化定位
→ 坐标换算 → input.click 拖拽（左键取/放，右键放单个）→ 终态断言 crafting_table 入包。
用法：进世界（生存+作弊）后运行。失败即中止并留证（截图+GUI 状态）。
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
KEY_E = 69
LEFT, RIGHT = 0, 1


def read_token() -> str:
    m = re.search(r'token\s*=\s*"([^"]+)"', TOML.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("token not found")
    return m.group(1)


class Abort(Exception):
    pass


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = BridgeClient(BridgeConfig(url="ws://127.0.0.1:8765", token=read_token()))
    fails = []
    async with client:
        scale = {"x": 0.5, "y": 0.5}

        def win(gx: float, gy: float) -> dict:
            return {"x": round(gx / scale["x"]), "y": round(gy / scale["y"])}

        async def gui(tag: str):
            r = await client.call("getGuiState", {})
            if not r.get("screen_open"):
                raise Abort(f"{tag}: 背包未打开")
            return r

        async def shot(name: str):
            res = await client.call("screenshot", {"tier": "full", "quality": 80})
            (OUT / name).write_bytes(base64.b64decode(res["image_b64"]))
            print(f"    📷 {name}")

        def slot_center(s: dict) -> dict:
            return {"x": s["x"] + 8, "y": s["y"] + 8}  # 16x16 格取中心

        async def click_slot(s: dict, button: int, tag: str):
            c = slot_center(s)
            await client.call("input.mouseMove", win(c["x"], c["y"]))
            await asyncio.sleep(0.15)
            r = await client.call("input.click", {"button": button})
            await asyncio.sleep(0.45)
            if not r.get("clicked", r.get("injected", True)):
                raise Abort(f"{tag}: 点击未注入 {r}")

        try:
            print("[1] /give 1 原木 + 沉降:")
            await client.command("/give @s minecraft:oak_log 1")
            await asyncio.sleep(1.2)

            print("[2] E 开背包 + 结构化定位:")
            await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
            await asyncio.sleep(0.8)
            probe = await client.call("input.mouseMove", {"x": 400, "y": 240})
            g = probe["gui_scaled"]
            scale["x"], scale["y"] = g["x"] / 400, g["y"] / 240
            print(f"    scale=({scale['x']:.3f},{scale['y']:.3f})")
            state = await gui("定位")
            log_slot = next((s for s in state["slots"]
                             if s.get("item") == "minecraft:oak_log" and s["role"] in ("player", "hotbar")), None)
            craft_slots = [s for s in state["slots"] if s["role"] == "crafting"]
            result_slot = next((s for s in state["slots"] if s["role"] == "result"), None)
            if not log_slot or len(craft_slots) != 4 or not result_slot:
                raise Abort(f"定位失败: log={log_slot} craft={len(craft_slots)} result={result_slot}")
            print(f"    原木槽 idx={log_slot['index']} 合成格×4 产物格 idx={result_slot['index']}")
            await shot("m2_final_1_open.jpg")

            print("[3] 拖原木入合成格（左键取→移→左键放）:")
            await click_slot(log_slot, LEFT, "取原木")
            await click_slot(craft_slots[0], LEFT, "放原木入合成格")
            state = await gui("取板")
            if next(s for s in state["slots"] if s["role"] == "result").get("item") != "minecraft:oak_planks":
                raise Abort("合成格未产出木板: " + str(
                    next(s for s in state["slots"] if s["role"] == "result")))
            await shot("m2_final_2_planks.jpg")

            print("[4] 取 4 板 → 放回背包空格:")
            empty = next((s for s in state["slots"]
                          if s["role"] in ("player", "hotbar") and not s.get("item")), None)
            if not empty:
                raise Abort("背包已满，无空格放木板")
            await click_slot(next(s for s in state["slots"] if s["role"] == "result"), LEFT, "取木板")
            await click_slot(empty, LEFT, "放木板")
            state = await gui("放板后")
            planks = next((s for s in state["slots"]
                           if s.get("item") == "minecraft:oak_planks" and s["role"] in ("player", "hotbar")), None)
            if not planks:
                raise Abort("木板未入包")
            print(f"    木板槽 idx={planks['index']} count={planks['count']}")

            print("[5] 木板入 2x2（右键放单个×3 + 左键放最后 1）:")
            await click_slot(planks, LEFT, "取木板")
            for cs in craft_slots[:3]:
                await click_slot(cs, RIGHT, "右键放单板")
            await click_slot(craft_slots[3], LEFT, "放最后板")
            state = await gui("取台")
            if next(s for s in state["slots"] if s["role"] == "result").get("item") != "minecraft:crafting_table":
                raise Abort("2x2 未产出工作台: " + str(
                    next(s for s in state["slots"] if s["role"] == "result")))
            await shot("m2_final_3_table_result.jpg")

            print("[6] 取工作台 → 放入背包 → 关背包:")
            await click_slot(next(s for s in state["slots"] if s["role"] == "result"), LEFT, "取工作台")
            await click_slot(empty, LEFT, "放工作台")
            await client.call("input.key", {"code": KEY_E, "duration_ms": 50})

            print("[7] 终态断言（重新开背包清点）:")
            await asyncio.sleep(0.8)
            await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
            await asyncio.sleep(0.8)
            final = await gui("终态")
            await shot("m2_final_4_final_inventory.jpg")
            tables = [s for s in final["slots"] if s.get("item") == "minecraft:crafting_table"]
            print(f"    crafting_table 槽: {tables}")
            if not tables:
                fails.append("终态未见 crafting_table")
            await client.call("input.key", {"code": KEY_E, "duration_ms": 50})
        except Abort as e:
            fails.append(str(e))
            try:
                await shot("m2_final_abort.jpg")
            except Exception:
                pass
        except Exception as e:
            fails.append(f"异常: {type(e).__name__}: {e}")

    print("\n===== M2 收官验收:", "FAIL: " + "; ".join(fails) if fails else "PASS =====")


if __name__ == "__main__":
    asyncio.run(main())
