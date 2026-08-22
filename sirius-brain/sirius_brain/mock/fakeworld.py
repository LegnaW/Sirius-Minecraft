"""T4 假世界 bridge：MockBridgeServer 上叠加可变世界状态，原语离线端到端可测。

维护的状态（构造后测试可直接改写）：
- ``position``：玩家脚底坐标 dict(x,y,z)；假 Baritone 推进它
- ``blocks``：``{(int x, int y, int z) → registry 名}``；input.click 按条件删除
- ``yaw``/``pitch``：lookAt 记录的朝向
- ``submitted``/``looks``/``clicks``：聊天行 / lookAt 目标 / 点击参数的 wire 记录（断言用）

行为映射（未覆盖方法经 ``tool_result`` 钩子回落 MockScript 通用成功）：
- getStats      → 回 position（结构对照 tests/fixtures/two_player_scene.json）
- world.query   → blocks 表按 range 立方扫描 + filter（registry 名或 ``#tag``，内置
                  logs/planks 两张 tag 表），按与玩家距离平方升序，cap 32 + truncated
                  （T1 v1.1 契约的 Python 侧镜像）；type=entities 走 T7 掉落物表
                  （item 注册名 + count + 玩家走近 ~1 格吸附消失）
- lookAt        → 记录朝向（由目标点反解 yaw/pitch，MC 欧拉角约定）
- dig（T6）     → bridge 智能挖掘的模拟：自带瞄准（朝目标中心转头）；触及外
                  ToolError(-32602)（文案含"触及"，与真 bridge 一致）；已空
                  already_air；bedrock timeout 不移除；眼位→中心连线上有遮挡块时
                  连遮挡块一起移除并标 broken_via_occluder；**挖掉的方块原地生成
                  掉落物实体**（T7，挖啥掉啥 count 1）；broken 结果附 drops——挖点
                  4 格内新出现的 item 实体聚合（挖前快照 diff，M3.6 T3 对齐真 bridge）
- command 路径  → BridgeClient.command 的 T→text→ENTER 三连：input.key 开聊天框、
                  input.text 暂存文本、input.key ENTER 提交；``#goto x [y] z`` 启动
                  假 Baritone 协程（每 0.5s 前进 4.3 m/s，到达即停），``#stop`` 停止
- input.click   → 左键且按住达标（hold_ms≥100，25ms tap 挖不掉方块）且目标方块在
                  触及距离（眼位→中心 ≤4.5）且朝向已对准（夹角 ≤30°）→ 从 blocks
                  删除并生成掉落物（T7）；bedrock 永不可破坏（"挖不破"场景）
- 掉落物吸附   → getStats / world.query 轮询时检查：玩家与掉落物距离 ≤1 格（vanilla
                  ItemEntity.playerTouch 的近似）→ 实体消失（=被捡走）；条目带
                  ``no_absorb`` 标记的除外（"走到身上也不吸附"的 skip 测试场景）
"""

from __future__ import annotations

import asyncio
import itertools
import math
from typing import Any

from .server import INVALID_PARAMS, MockBridgeServer, ToolError

# ---------------------------------------------------------------------- 常量

#: 假 Baritone 推进节拍（秒）：与 Primitives.poll_interval 同量级，轮询能看到逐格推进
MOVE_INTERVAL = 0.5
#: 步行速度（m/s）：MC 疾走+跳跃的真实均速（2.15 格/节拍）
MOVE_SPEED = 4.3
#: 玩家眼位高度（格）：触及/朝向判定从眼睛出发
EYE_HEIGHT = 1.62
#: 触及距离（格，眼位→方块中心）：与 Primitives.DIG_REACH 同源
REACH = 4.5
#: 朝向对准容差（度）：lookAt 直指方块中心时夹角≈0，30° 吸走浮点误差与"看着附近"
AIM_TOLERANCE_DEG = 30.0
#: 按住时长下限（毫秒）：低于它的点击按 25ms tap 处理——挖不掉任何非即碎方块
MIN_BREAK_HOLD_MS = 100
#: fake dig 的模拟挖掘耗时（毫秒）：bridge 侧监视按住的等价物（测试不需要真等）
DIG_SIM_ELAPSED_MS = 200
#: fake dig 的视线采样步长（格）：眼位→中心连线找遮挡块
DIG_OCCLUDER_STEP = 0.25
#: fake dig 掉落观察半径（格）：与 Java 侧 DigContracts.DROPS_SCAN_RADIUS 同源——
#: 破坏后扫挖点附近该半径内**新出现**的 item 实体聚合成 drops（M3.6 T3）
DIG_DROP_SCAN_RADIUS = 4.0
#: world.query 结果条数上限：与 Java 侧 BLOCKS_CAP 一致
BLOCKS_CAP = 32
#: world.query entities 结果条数上限：与 Java 侧 ENTITIES_CAP 一致（T7）
ENTITIES_CAP = 128
#: 掉落物被玩家走近吸附的距离（格）：vanilla ItemEntity 拾取半径 ~1 格的近似（T7）
ITEM_ABSORB_RADIUS = 1.0
#: 永不可破坏的方块（"挖不破"教学场景：工具不足/保护规则的现实对应物）
UNBREAKABLE_BLOCKS = {"minecraft:bedrock"}

# 内置 tag 表（前缀 # 匹配；vanilla logs/planks 两组足够覆盖测试与演示）
_LOG_IDS = {
    "minecraft:oak_log", "minecraft:spruce_log", "minecraft:birch_log",
    "minecraft:jungle_log", "minecraft:acacia_log", "minecraft:dark_oak_log",
    "minecraft:mangrove_log", "minecraft:cherry_log",
    "minecraft:crimson_stem", "minecraft:warped_stem",
}
_PLANK_IDS = {log.replace("_log", "_planks").replace("_stem", "_planks")
              for log in _LOG_IDS}
BLOCK_TAGS: dict[str, set[str]] = {
    "#minecraft:logs": _LOG_IDS,
    "#logs": _LOG_IDS,
    "#minecraft:planks": _PLANK_IDS,
    "#planks": _PLANK_IDS,
}

# BridgeClient.command 编排用的 GLFW 键码（与 bridge/client.py 一致；ESC 是
# M4.1 发送失败后的输入框清理键）
GLFW_KEY_T = 84
GLFW_KEY_ENTER = 257
GLFW_KEY_ESCAPE = 256


def _normalized_block_id(block_id: str) -> str:
    """补 ``minecraft:`` 前缀（测试里写短名更顺手，wire 上恒为全名）。"""
    return block_id if ":" in block_id else f"minecraft:{block_id}"


def _filter_matches(block_id: str, entry: str) -> bool:
    """单个 filter 条目匹配：registry 名（短名自动补前缀）或 ``#tag``（T1 契约）。"""
    if entry.startswith("#"):
        tag = entry[1:]
        if ":" not in tag:
            tag = f"minecraft:{tag}"
        return block_id in BLOCK_TAGS.get(f"#{tag}", set())
    return block_id == _normalized_block_id(entry)


class FakeWorldBridge(MockBridgeServer):
    """可变世界 mock：getStats/world.query/lookAt/command(假 Baritone)/input.click 有状态。"""

    def __init__(
        self,
        *,
        position: dict[str, float] | None = None,
        blocks: dict[tuple[int, int, int], str] | None = None,
        move_speed: float = MOVE_SPEED,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        super().__init__(host=host, port=port)
        self.position: dict[str, float] = dict(position or {"x": 0.0, "y": 64.0, "z": 0.0})
        self.blocks: dict[tuple[int, int, int], str] = {
            tuple(int(v) for v in key): _normalized_block_id(value)
            for key, value in (blocks or {}).items()
        }
        #: 掉落物实体表（T7）：uuid → world.query entities 条目形态的 dict
        #: （uuid/name/type=minecraft:item/item/count/position；测试可直接增删改写）
        self.item_drops: dict[str, dict[str, Any]] = {}
        #: 非掉落物实体表（M4 危险模拟）：uuid → entities 条目形态的 dict，
        #: 额外带 category（monster/creature/misc...）与 width（碰撞箱宽）——
        #: 对齐 M4 后真 bridge 的 entities 载荷（flee 的敌对判定/危险半径输入）
        self.mob_entities: dict[str, dict[str, Any]] = {}
        #: getStats 字段覆盖（M4 危险模拟）：health/air/food/alive 等可直接注入
        self.stats_override: dict[str, Any] = {}
        #: _spawn_drop 生成的掉落是否带 no_absorb 标记（测试开关："走到身上也
        #: 不吸附、只能被外部移除"的场景——skip 防死循环 / 第三方捡走测试）
        self.drop_no_absorb = False
        self._drop_seq = 0
        #: 假 Baritone 步行速度（m/s）；0 = 冻结世界（超时/看门狗场景用）
        self.move_speed = move_speed
        self.yaw = 0.0
        self.pitch = 0.0
        # wire 记录（断言用）
        self.submitted: list[str] = []          # ENTER 提交的聊天行（含 # 命令）
        self.texts: list[str] = []              # M4.1：input.text 的 wire 记录
        self.chats_sent: list[str] = []         # M4.1：chat.send 直发的 wire 记录
        self.looks: list[tuple[float, float, float]] = []
        self.clicks: list[dict[str, Any]] = []
        self.digs: list[dict[str, Any]] = []    # T6：dig 工具调用的 wire 记录
        self.key_presses: list[dict[str, Any]] = []  # M4：input.key 的 wire 记录
        #: chat.send 模拟错误码（None=正常受理；-32601=模拟旧 jar 无此工具，
        #: 测试直发通道的回落路径）
        self.chat_send_error: int | None = None
        # 聊天框状态机（BridgeClient.command 的 T→text→ENTER 三连）
        self._chat_open = False
        self._pending_text: str | None = None
        self._mover_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 分发

    async def tool_result(self, method: str, params: dict[str, Any]) -> Any:
        if method == "getStats":
            return self._result_get_stats()
        if method == "world.query":
            return self._result_world_query(params)
        if method == "lookAt":
            return self._result_look_at(params)
        if method == "dig":
            return self._result_dig(params)
        if method == "input.click":
            return self._result_click(params)
        if method == "input.key":
            return self._result_key(params)
        if method == "input.text":
            return self._result_text(params)
        if method == "getGuiState":
            return self._result_gui_state()
        if method == "chat.send":
            return self._result_chat_send(params)
        return await super().tool_result(method, params)  # 未覆盖 → 通用成功

    # ------------------------------------------------------------------ 感知

    def _result_get_stats(self) -> dict[str, Any]:
        """getStats：结构对照 two_player_scene.json（位置换成活的）。

        M4 危险模拟：stats_override 直接覆盖输出字段（health/air/alive 等），
        眼位水检测靠 blocks 表里放 minecraft:water（真实语义——getStats 没有
        水/火字段，反射层本来就用 world.query 眼位方块判水）。
        """
        self._absorb_items()  # 轮询点 = 吸附时机（原语行走中轮询 getStats）
        result = {
            "in_game": True,
            "health": 20.0,
            "food": 20,
            "saturation": 5.0,
            "air": 300,
            "xp_level": 0,
            "xp_progress": 0.0,
            "position": dict(self.position),
            "dimension": "minecraft:overworld",
            "game_mode": "survival",
            "effects": [],
            "alive": True,
        }
        result.update(self.stats_override)
        return result

    def _result_world_query(self, params: dict[str, Any]) -> dict[str, Any]:
        self._absorb_items()  # 轮询点 = 吸附时机（掉落物查询/方块复核前先结算物理）
        if params.get("type") == "entities":
            return self._result_world_query_entities(params)
        range_ = float(params.get("range", 16))
        radius = math.ceil(range_)
        px, py, pz = self.position["x"], self.position["y"], self.position["z"]
        cx, cy, cz = int(math.floor(px)), int(math.floor(py)), int(math.floor(pz))
        filters = params.get("filter") or None
        matches: list[tuple[float, dict[str, Any]]] = []
        for (x, y, z), block_id in self.blocks.items():
            # 立方扫描（与 Java scanBlocks 同口径：中心块坐标 ± ceil(range) 每轴）
            if abs(x - cx) > radius or abs(y - cy) > radius or abs(z - cz) > radius:
                continue
            if filters and not any(_filter_matches(block_id, f) for f in filters):
                continue
            dist_sq = ((x + 0.5 - px) ** 2 + (y + 0.5 - py) ** 2 + (z + 0.5 - pz) ** 2)
            matches.append((dist_sq, {"x": x, "y": y, "z": z, "block": block_id}))
        # 命中后按与玩家距离平方升序（T1 契约），cap 32 + truncated
        matches.sort(key=lambda item: item[0])
        truncated = len(matches) > BLOCKS_CAP
        top = [block for _, block in matches[:BLOCKS_CAP]]
        return {"blocks": top, "count": len(top), "truncated": truncated}

    def _result_world_query_entities(self, params: dict[str, Any]) -> dict[str, Any]:
        """world.query(entities)：掉落物 + mob 实体表 → 与 Java filterEntities 同口径。

        掉落物条目带 item 注册名与 count（T7 bridge 契约）；M4 起两类实体都带
        category/width（掉落物按真 bridge 语义报 misc/0.25，mob_entities 里的
        注入值原样透传——flee 的敌对判定/危险半径输入）。filter 按实体 type
        registry 名匹配（短名自动补 minecraft: 前缀）；range 为与玩家的平方 3D
        距离判定；cap 128 + truncated。
        """
        range_ = float(params.get("range", 16))
        raw_filters = params.get("filter") or []
        wanted_types = {_normalized_block_id(str(f)) for f in raw_filters} or None
        px, py, pz = self.position["x"], self.position["y"], self.position["z"]
        max_dist_sq = range_ * range_
        out: list[dict[str, Any]] = []
        truncated = False
        all_entities = itertools.chain(self.item_drops.values(),
                                       self.mob_entities.values())
        for drop in all_entities:
            if wanted_types is not None and drop["type"] not in wanted_types:
                continue
            pos = drop["position"]
            dx, dy, dz = pos["x"] - px, pos["y"] - py, pos["z"] - pz
            if dx * dx + dy * dy + dz * dz > max_dist_sq:
                continue
            if len(out) >= ENTITIES_CAP:
                truncated = True  # 又一个范围内的匹配被 cap 丢掉
                break
            entry = {"uuid": drop["uuid"], "name": drop["name"], "type": drop["type"],
                     "position": dict(pos)}
            if drop.get("item") is not None:  # item 实体专属字段（T7）
                entry["item"] = drop["item"]
                entry["count"] = int(drop.get("count", 1))
            # M4 注册表类别 + 碰撞箱宽：真 bridge 对一切实体输出；fake 里
            # 掉落物按真语义固定 misc/0.25，mob_entities 的注入值原样透传
            entry["category"] = str(drop.get("category") or "misc")
            entry["width"] = float(drop.get("width", 0.25))
            out.append(entry)
        return {"entities": out, "count": len(out), "truncated": truncated}

    def _spawn_drop(self, x: int, y: int, z: int, block_id: str) -> None:
        """方块被挖掉 → 原地生成掉落物实体（挖啥掉啥、count 1；T7 拾取链路的物源）。

        name 字段真机会是客户端本地化显示名（不可用于匹配），fake 直接用注册名占位；
        可匹配性全靠 item 字段（与 T7 bridge 契约一致）。
        """
        self._drop_seq += 1
        uuid = f"item-drop-{self._drop_seq}"
        self.item_drops[uuid] = {
            "uuid": uuid,
            "name": block_id,
            "type": "minecraft:item",
            "item": block_id,
            "count": 1,
            "position": {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5},
            "no_absorb": self.drop_no_absorb,
        }

    def _absorb_items(self) -> None:
        """玩家走到掉落物 ~1 格内 → 实体消失（vanilla ItemEntity.playerTouch 的近似）。

        在 getStats / world.query 轮询时结算——原语行走中轮询 getStats，等效"路过
        即吸附"。no_absorb 标记的条目例外（"走到身上也不吸附"的 skip 测试场景）。
        """
        px, py, pz = self.position["x"], self.position["y"], self.position["z"]
        for uuid, drop in list(self.item_drops.items()):
            if drop.get("no_absorb"):
                continue
            pos = drop["position"]
            if math.dist((px, py, pz), (pos["x"], pos["y"], pos["z"])) <= ITEM_ABSORB_RADIUS:
                del self.item_drops[uuid]

    def _result_look_at(self, params: dict[str, Any]) -> dict[str, Any]:
        """lookAt：记录朝向（由目标反解 yaw/pitch，MC 欧拉角：yaw 0=+Z、pitch 正=低头）。"""
        tx, ty, tz = float(params["x"]), float(params["y"]), float(params["z"])
        self.looks.append((tx, ty, tz))
        return self._aim_at(tx, ty, tz)

    def _aim_at(self, tx: float, ty: float, tz: float) -> dict[str, Any]:
        """把朝向设为眼位→(tx,ty,tz)（bridge 平滑瞄准终态的等价物）。"""
        ex, ey, ez = self._eye()
        dx, dy, dz = tx - ex, ty - ey, tz - ez
        horizontal = math.hypot(dx, dz)
        self.yaw = math.degrees(math.atan2(-dx, dz))
        self.pitch = -math.degrees(math.atan2(dy, horizontal)) if horizontal else (-90.0 if dy > 0 else 90.0)
        return {
            "in_game": True, "looked": True,
            "yaw": round(self.yaw, 1), "pitch": round(self.pitch, 1),
            "distance": round(math.sqrt(dx * dx + dy * dy + dz * dz), 1),
        }

    def _result_dig(self, params: dict[str, Any]) -> dict[str, Any]:
        """dig（T6 bridge 智能挖掘的 fake）：自带瞄准；触及外 -32602；遮挡穿透标注。

        与真 bridge 的契约对齐：already_air 幂等成功；不可破坏 bedrock → timeout
        不移除；眼位→中心连线穿过的第一个实心块算遮挡（连同目标一起移除，标
        broken_via_occluder=true——真 bridge 是"先破遮挡再轮到目标"的监视按住）。
        M3.6 T3：broken 结果附 ``drops:[{item,count}]``——挖点 DIG_DROP_SCAN_RADIUS
        内**新出现**的 item 实体聚合（先快照再 diff，别人先掉的排除；真 bridge
        破坏后等 20 tick 再扫，fake 世界同步无延迟直接扫）。already_air/timeout
        不带 drops（与真 bridge 一致）。
        """
        self.digs.append(dict(params))
        x, y, z = int(params["x"]), int(params["y"]), int(params["z"])
        target = (x, y, z)
        timeout_ms = int(params.get("timeout_ms") or 15000)
        if target not in self.blocks:
            return {"in_game": True, "result": "already_air", "block": None,
                    "elapsed_ms": 5}
        ex, ey, ez = self._eye()
        center = (x + 0.5, y + 0.5, z + 0.5)
        dist = math.sqrt((center[0] - ex) ** 2 + (center[1] - ey) ** 2 + (center[2] - ez) ** 2)
        if dist > REACH:
            raise ToolError(
                INVALID_PARAMS,
                f"dig target ({x},{y},{z}) is {dist:.1f} blocks away - beyond the {REACH}-block reach."
                f" Walk closer first（超出触及范围，先 walkTo 到它旁边）")
        block_id = self.blocks[target]
        if block_id in UNBREAKABLE_BLOCKS:
            self._aim_at(*center)  # 瞄了、按了、挖不动：超时不移除
            return {"in_game": True, "result": "timeout", "block": block_id,
                    "elapsed_ms": timeout_ms}
        occluder = self._occluder_on_line(ex, ey, ez, center, target)
        self._aim_at(*center)  # bridge 的平滑瞄准终态
        seen_before = self._drop_uuids_near(center)  # 挖前快照（diff 掉别人的）
        del self.blocks[target]
        self._spawn_drop(x, y, z, block_id)  # 挖啥掉啥（T7；遮挡树叶不掉落）
        if occluder is not None:
            self.blocks.pop(occluder, None)  # 先穿遮挡（真 bridge 监视按住的自然顺序）
        result: dict[str, Any] = {"in_game": True, "result": "broken", "block": block_id,
                                  "elapsed_ms": DIG_SIM_ELAPSED_MS * (2 if occluder else 1)}
        if occluder is not None:
            result["broken_via_occluder"] = True
        result["drops"] = self._drops_since(center, seen_before)
        return result

    def _drop_uuids_near(self, center: tuple[float, float, float],
                         radius: float = DIG_DROP_SCAN_RADIUS) -> set[str]:
        """挖点 radius 格内现存掉落物的 uuid 集（挖前快照，diff 基线）。"""
        seen = set()
        for uuid, drop in self.item_drops.items():
            pos = drop["position"]
            if math.dist((pos["x"], pos["y"], pos["z"]), center) <= radius:
                seen.add(uuid)
        return seen

    def _drops_since(self, center: tuple[float, float, float],
                     seen_before: set[str],
                     radius: float = DIG_DROP_SCAN_RADIUS) -> list[dict[str, Any]]:
        """快照之后新出现在挖点 radius 格内的掉落物，按注册名聚合成 [{item,count}]。"""
        aggregated: dict[str, int] = {}
        for uuid, drop in self.item_drops.items():
            if uuid in seen_before:
                continue  # 挖之前就躺着的（别人的）：不算本次挖掘的掉落
            pos = drop["position"]
            if math.dist((pos["x"], pos["y"], pos["z"]), center) > radius:
                continue
            aggregated[str(drop["item"])] = aggregated.get(str(drop["item"]), 0) + int(drop.get("count", 1))
        return [{"item": item, "count": count} for item, count in aggregated.items()]

    def _occluder_on_line(self, ex: float, ey: float, ez: float,
                          center: tuple[float, float, float],
                          target: tuple[int, int, int]) -> tuple[int, int, int] | None:
        """眼位→方块中心连线上的第一个实心非目标块（0.25 格步进采样）。"""
        steps = max(1, int(math.dist((ex, ey, ez), center) / DIG_OCCLUDER_STEP))
        for i in range(1, steps):
            ratio = i / steps
            px = ex + (center[0] - ex) * ratio
            py = ey + (center[1] - ey) * ratio
            pz = ez + (center[2] - ez) * ratio
            cell = (int(math.floor(px)), int(math.floor(py)), int(math.floor(pz)))
            if cell == target or cell not in self.blocks:
                continue
            return cell
        return None

    # ------------------------------------------------------------------ 输入

    def _result_key(self, params: dict[str, Any]) -> dict[str, Any]:
        code = int(params.get("code", -1))
        self.key_presses.append({"code": code,
                                 "duration_ms": int(params.get("duration_ms", 0))})
        if code == GLFW_KEY_T:
            self._chat_open = True
        elif code == GLFW_KEY_ENTER:
            self._chat_open = False
            line, self._pending_text = self._pending_text, None
            if line is not None:
                self.submitted.append(line)
                if line.startswith("#"):
                    self._handle_baritone(line)
        elif code == GLFW_KEY_ESCAPE:
            # M4.1：ESC 丢弃聊天输入框残留（BridgeClient.command 发送失败后的清理）
            self._chat_open = False
            self._pending_text = None
        return {"injected": True, "key": f"glfw:{code}", "glfw_key": code,
                "modifiers": list(params.get("modifiers") or []),
                "duration_ms": int(params.get("duration_ms", 0)),
                "release_scheduled": True, "screen_open": self._chat_open}

    def _result_text(self, params: dict[str, Any]) -> dict[str, Any]:
        self._pending_text = str(params.get("string", ""))
        self.texts.append(self._pending_text)
        return {"delivered": True, "length": len(self._pending_text),
                "delivered_all": True}

    def _result_gui_state(self) -> dict[str, Any]:
        """getGuiState 的聊天状态机映射（M4.1 T1：BridgeClient.command 的时序确认源）。

        形态对齐真 bridge GuiContracts：无屏 → ``{"screen_open": false}``；
        聊天框 → ``{"screen_open": true, "screen_class": "ChatScreen", ...}``。
        """
        if not self._chat_open:
            return {"in_game": True, "screen_open": False}
        return {"in_game": True, "screen_open": True,
                "screen_class": "ChatScreen", "slots": []}

    def _result_chat_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """chat.send 直发（M4.1 T3）：绕开 T 键 GUI 的聊天通道——死亡屏占用时的
        播报路径。``chat_send_error`` 模拟旧 jar（-32601）测回落。"""
        if self.chat_send_error is not None:
            raise ToolError(self.chat_send_error,
                            f"chat.send unavailable (simulated {self.chat_send_error})")
        text = str(params.get("string", ""))
        self.chats_sent.append(text)
        if text.startswith("#"):
            self._handle_baritone(text)
        return {"in_game": True, "sent": True, "length": len(text)}

    def _result_click(self, params: dict[str, Any]) -> dict[str, Any]:
        self.clicks.append(dict(params))
        button = int(params.get("button", -1))
        hold_ms = int(params.get("hold_ms") or 0)
        broke: list[dict[str, Any]] = []
        if button == 0 and hold_ms >= MIN_BREAK_HOLD_MS:
            target = self._aimed_block()
            if target is not None:
                block_id = self.blocks[target]
                if block_id not in UNBREAKABLE_BLOCKS:
                    del self.blocks[target]
                    self._spawn_drop(*target, block_id)  # T7：挖掉即掉落
                    broke.append({"x": target[0], "y": target[1], "z": target[2],
                                  "block": block_id})
        result: dict[str, Any] = {"clicked": True, "button": button,
                                  "count": int(params.get("count", 1)),
                                  "screen_open": False}
        if broke:
            result["broke"] = broke
        return result

    # ------------------------------------------------------------------ 假 Baritone

    def _handle_baritone(self, line: str) -> None:
        """聊天行以 # 开头 → 客户端侧命令（不达服务器）：这里只模拟 goto/stop。"""
        tokens = line.split()
        try:
            if tokens[0] == "#goto" and len(tokens) in (3, 4):
                if len(tokens) == 4:
                    x, y, z = float(tokens[1]), float(tokens[2]), float(tokens[3])
                else:  # 两参形式：y 由寻路器落地面，这里保持当前高度（平地世界）
                    x, z = float(tokens[1]), float(tokens[2])
                    y = self.position["y"]
                self._start_mover(x, y, z)
            elif tokens[0] == "#stop":
                self._stop_mover()
        except (ValueError, IndexError):
            pass  # 参数烂掉 → 静默忽略（真实客户端也只是回一行用法提示）

    def _start_mover(self, x: float, y: float, z: float) -> None:
        self._stop_mover()
        task = asyncio.create_task(self._mover(x, y, z))
        self._track(task)  # 登记 _pending：close() 时统一取消，不留悬挂任务
        self._mover_task = task

    def _stop_mover(self) -> None:
        if self._mover_task is not None and not self._mover_task.done():
            self._mover_task.cancel()
        self._mover_task = None

    async def _mover(self, tx: float, ty: float, tz: float) -> None:
        """假 Baritone：每节拍朝目标推进 move_speed×间隔 格，到达即停。"""
        step = max(0.0, self.move_speed) * MOVE_INTERVAL
        try:
            while True:
                await asyncio.sleep(MOVE_INTERVAL)
                px, py, pz = self.position["x"], self.position["y"], self.position["z"]
                dx, dz = tx - px, tz - pz
                horizontal = math.hypot(dx, dz)
                if horizontal <= step:
                    self.position = {"x": tx, "y": ty, "z": tz}  # 到达（含 step=0 且已到位）
                    return
                if step <= 0:
                    continue  # 冻结世界（move_speed=0）：位置不动，循环空转等 #stop
                ratio = step / horizontal
                self.position = {
                    "x": px + dx * ratio,
                    "y": py + (ty - py) * min(1.0, ratio),  # 高度按比例滑向目标（平地即不变）
                    "z": pz + dz * ratio,
                }
        except asyncio.CancelledError:
            raise  # #stop / 新 #goto / close() 的取消路径

    # ------------------------------------------------------------------ 瞄准判定

    def _eye(self) -> tuple[float, float, float]:
        return self.position["x"], self.position["y"] + EYE_HEIGHT, self.position["z"]

    def _aimed_block(self) -> tuple[int, int, int] | None:
        """视线指向的方块：触及距离内、与视线夹角最小且 ≤ 容差的那个（None = 没瞄上）。"""
        ex, ey, ez = self._eye()
        yaw_rad = math.radians(self.yaw)
        pitch_rad = math.radians(self.pitch)
        # MC 视线方向：yaw 0=+Z（南）、90=-X（西）；pitch 正=低头
        direction = (-math.sin(yaw_rad) * math.cos(pitch_rad),
                     -math.sin(pitch_rad),
                     math.cos(yaw_rad) * math.cos(pitch_rad))
        best: tuple[float, tuple[int, int, int]] | None = None
        for (x, y, z) in self.blocks:
            to = (x + 0.5 - ex, y + 0.5 - ey, z + 0.5 - ez)
            length = math.sqrt(to[0] ** 2 + to[1] ** 2 + to[2] ** 2)
            if length > REACH or length == 0:
                continue
            cos_angle = (direction[0] * to[0] + direction[1] * to[1] + direction[2] * to[2]) / length
            angle = math.degrees(math.acos(max(-1.0, min(1.0, cos_angle))))
            if angle <= AIM_TOLERANCE_DEG and (best is None or angle < best[0]):
                best = (angle, (x, y, z))
        return best[1] if best is not None else None


__all__ = [
    "BLOCKS_CAP",
    "BLOCK_TAGS",
    "DIG_OCCLUDER_STEP",
    "DIG_DROP_SCAN_RADIUS",
    "DIG_SIM_ELAPSED_MS",
    "ENTITIES_CAP",
    "EYE_HEIGHT",
    "FakeWorldBridge",
    "ITEM_ABSORB_RADIUS",
    "MIN_BREAK_HOLD_MS",
    "MOVE_INTERVAL",
    "MOVE_SPEED",
    "REACH",
    "UNBREAKABLE_BLOCKS",
]
