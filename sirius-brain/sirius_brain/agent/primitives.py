"""M3.5 任务级复合动作原语：意图进、结果出（Numen 式契约），执行下沉确定性代码。

背景（session 2026-08-20）：M3 真机闭环"不智能"——动作粒度太低，VLM 被迫当小脑，
砍树 22 步耗尽预算。本模块把"走到/挖掉/收集 N 个"这类高频复合意图下沉为
一次原语调用：同步阻塞期间零 VLM 调用，LLM 只做意图层决策。

设计要点：
- **同步阻塞 + 协作式取消**（非 Numen 式异步受理）：AgentLoop 本就串行执行工具，
  同步原语让 60s 行走只占 1 个 tool call 位。急停经 ``cancel: Callable[[], bool]``
  在微步循环（poll_interval≈0.5s）每步检查，保证 ≤1s 生效；触发后按场景收尾
  （walk 发 ``#stop`` 停 Baritone），并返回带当前坐标的中止文案
- **结果话术即契约**（Numen 手段）：成功带数字（走到哪/挖了几个）、失败带下一步
  建议（先 walkTo 邻近 / 同参数重发可续走 / 确认 ID 写法）、取消带当前坐标——
  VLM 读文本就能自救，不需要额外结构化通道
- **世界复核靠 world.query**：getStats 读不到脚下以外 的方块，一切"目标还在不在"
  的判定都走 world.query（T1 后支持 filter，按与玩家距离升序返回）
- **挖后拾取（T7）**：collect_block 默认挖掉后顺路捡匹配掉落（走过去让 vanilla
  吸附，Numen CollectItemsCompanionTask 语义：实体消失=已拾取、skip 防死循环、
  匹配不上的绝对不碰——多人服只捡自己挖出的）；pickup 参数可关
- 本模块自包含、不碰 loop.py / tools.py 注册表（T3 另行接入）；client 只要求
  具备 BridgeClient 的 call()/command() 接口（测试可注入 mock）
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .tools import ToolOutcome

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------- 常量
# 取值理由集中说明（调参先读注释；Numen 参照：MoveToCompanionTask/GoToThenDoTask）

#: walk_to 默认超时（秒）：步行 4.3 m/s 时 120s ≈ 500 格，覆盖 Baritone 常规寻路
WALK_TIMEOUT = 120.0
#: walk_to 到达判定：与目标水平距离 ≤2 格即算到达（Baritone #goto 本就停在目标附近 1-2 格，
#: 抠到 0.5 格会被碰撞箱/地形起伏反复"差一点"）
WALK_ARRIVE_DIST = 2.0
#: 距离无进展看门狗（秒）：Baritone 绕路/卡跳沿时距离可能暂时不减，15s 足以区分
#: "绕路中"与"真卡死"；触发后只重发一次 #goto（Numen 近重试档，MoveToCompanionTask）
WALK_STALL_SECONDS = 15.0
#: 发 #goto 前的界面屏障等待上限（秒）。T0b 教训（reports/M3.5-T0b.md）：quickPlay
#: 入世后世界加载屏未消失时，T 键打不开聊天框，#goto 静默丢失——等屏消失再发令
WALK_SCREEN_BARRIER_TIMEOUT = 10.0

#: dig_block 默认超时（秒）：递增 hold 的 8 段最坏 ≈ 3.5+7+8×6=58s，75s 上限留余量
DIG_TIMEOUT = 75.0
#: 挖掘触及距离（格，脚底坐标到方块中心）：MC 生存交互距离 4.5（与 FakeWorldBridge
#: 的 eye→center ≤4.5 判定同源）；超出则先移动（Numen GoToThenDoTask 的 OUT_OF_REACH 教学）
DIG_REACH = 4.5
#: 单段挖掘的按住时长（毫秒）。**必须 ≥ 目标徒手破坏时间**——vanilla 机制松键后
#: 挖掘进度清零，跨段不累积，段太短永远破不了。徒手 oak_log hardness 2.0 → 2.0×1.5=3.0s
#: （有斧时才是 0.3-0.6s），基准段 3500ms 覆盖无遮挡徒手木类（M3.5 T5a 真机教训：
#: 600ms 八段全空，见 docs_agent/reports/M3.5-T5a.md）。**遮挡场景按段递增**：
#: 视线先穿 k 格树叶（0.35s/格）再达树干，需 hold ≥ 0.35k+3.0s——段 n 的 hold
#: = min(DIG_CLICK_HOLD_MS×n, DIG_CLICK_HOLD_MAX_MS)，第 3 段起 8s（真机实证
#: 8s 可穿透 2 格树叶 + 树干）。协议上限 10000。石头徒手 7.5s 仍超段长——那是
#: "给工具再挖"的预期教学失败，不是本层要解决的
DIG_CLICK_HOLD_MS = 3500
#: 递增 hold 的封顶（毫秒）：第 3 段起固定用这个值（8s 真机实证可穿透常见遮挡）
DIG_CLICK_HOLD_MAX_MS = 8000
#: 每段挖掘后等服务端方块移除同步回来的静默（秒）
DIG_SETTLE = 0.4
#: 连续挖掘段数上限：8 段 ×3.9s ≈ 31s 仍不破 → 判定挖不动（被遮挡/工具不足/
#: 保护规则），给教学式失败而不是无限空挖
DIG_MAX_SEGMENTS = 8
#: bridge dig 工具的协议超时上限（毫秒，schema v1.2 timeout_ms ≤30000）；
#: brain 侧 DIG_TIMEOUT=75s 超出部分留给 fallback 段循环
DIG_BRIDGE_TIMEOUT_MS = 30000

#: collect_block 的扫描半径（格）：与 bridge world.query 的 MAX_RANGE 对齐（Java 侧
#: 超过 64 直接 -32602，本地常量避免无谓往返）
COLLECT_RANGE = 64.0
#: collect_block 走位目标：目标方块旁 ±1.5 格的邻位点（不到方块本身上，也不出触及范围）
COLLECT_NEAR_OFFSET = 1.5

# ---------------------------------------------------------------------- 挖后拾取（T7）
# 参照 Numen CollectItemsCompanionTask：磁吸靠 vanilla 自身（走近 ~1 格自动拾取），
# 本层只负责"走到掉落物旁"；实体消失 = 已拾取（无论谁捡的）；够不着的记 skip 防死循环；
# 0 件也是正常收尾

#: 掉落物扫描半径（格）：world.query entities 的查询范围——比拾取判定圈大一圈，
#: 先看到再走近
DROP_QUERY_RANGE = 12.0
#: 只捡"自己挖出来的"：掉落物与挖点的最大距离（格）。多人服礼仪——散在 4 格外的
#: 匹配掉落可能是其他玩家挖的/扔的，绝对不碰
DROP_NEAR_DIG_DIST = 4.0
#: 一次拾取流程的整体超时（秒）：Numen 式"每轮有捡到或跳过（进度）就继续，没有就收"，
#: 20s 兜住"数个掉落 × 每个走位 2-3s"的常见规模
PICKUP_TIMEOUT = 20.0
#: "够不着"判定（格）：玩家与掉落物的水平距离 ≤ 此值仍未吸附 → skip（vanilla 拾取
#: 半径 ~1 格；1.2 = Numen PICKUP_REACH_SQR=1.5 开方，留 0.2 容差）
PICKUP_ARRIVE_DIST = 1.2
#: 走位返回后等吸附的静默上限（秒）：walk_to 在距目标 ≤2 格（WALK_ARRIVE_DIST）即返回，
#: Baritone 自身还在朝精确位置收尾——给它时间走完最后 1 格触发 vanilla 吸附
PICKUP_SETTLE_SECONDS = 3.0
#: 单个掉落物的走位尝试上限：第一次没吸上（#goto 收尾可能停在 1.2-2.0 格之间）换
#: 最新坐标再走一次，仍不行 = 够不着（skip），防同点死循环
PICKUP_MAX_ATTEMPTS = 2

#: 取消回调类型：返回 True 即请求中止（急停检查点）
CancelFlag = Callable[[], bool] | None


@dataclass(frozen=True)
class _StepResult:
    """原语内部微步骤结果（public 方法把它包装成 ToolOutcome 文本）。

    ok=False 时 text 已是"失败 + 下一步建议"的教学文案，可直接上抛给 VLM。
    drops（M3.6 T3）：dig 破坏确认后 bridge 实测的掉落清单 [{item,count}]——
    broken 且新版 bridge 才有；None = 无此信息（旧 jar/未破坏），调用方回落
    registry id 匹配；[] = 明确无掉落（如树叶没掉苗），不要回落。
    """

    ok: bool
    text: str
    drops: list[dict[str, Any]] | None = None


def _fmt(value: float) -> str:
    """坐标 → 命令参数文本：10.0→"10"、10.5→"10.5"（避免 #goto 10.000000 之类噪声）。"""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"


def _dist2(ax: float, ay: float, az: float, bx: float, by: float, bz: float) -> float:
    return (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2


class Primitives:
    """任务级复合动作原语集合（walk_to / dig_block / collect_block）。

    用法（T3 接入后由工具注册表包装；当前可独立驱动）::

        prims = Primitives(client, poll_interval=0.5)
        outcome = await prims.walk_to(103.0, -198.0, cancel=loop.stop_requested)
    """

    def __init__(self, client: Any, *, poll_interval: float = 0.5) -> None:
        #: BridgeClient（或测试 mock）：要求 call(method, params) / command(text)
        self.client = client
        #: 微步轮询间隔（秒）：也是取消检查的粒度（≤1s 急停的来源）
        self.poll_interval = poll_interval
        #: bridge 是否支持 dig 工具（T6 v1.2）：None=未探测；False=确认不支持
        #: （试调回 -32601 后记忆，后续直接走旧 hold 段循环 fallback）
        self._dig_supported: bool | None = None

    # ------------------------------------------------------------------ walk_to

    async def walk_to(self, x: float, z: float, y: float | None = None,
                      timeout: float = WALK_TIMEOUT,
                      cancel: CancelFlag = None) -> ToolOutcome:
        """走到 (x, z)（y 可选，给了走三维目标）：Baritone #goto + 轮询到位。

        - 成功："已走到 (x,y,z)，距目标 n 格"
        - 15s 无进展重发一次 #goto；超时发 #stop + "同参数重发可续走"
        - 取消：发 #stop + 当前坐标
        """
        result = await self._walk_to(x, z, y=y, timeout=timeout, cancel=cancel)
        return ToolOutcome(result.text)

    async def _walk_to(self, x: float, z: float, *, y: float | None = None,
                       timeout: float = WALK_TIMEOUT,
                       cancel: CancelFlag = None) -> _StepResult:
        # 0) 界面屏障（T0b 教训）：加载/覆盖屏未消失时聊天框打不开，#goto 会静默
        #    丢失——先等屏消失再发令（collect_block 的走位也经此路径，同样受保护）
        barrier = await self._wait_screen_clear(cancel=cancel)
        if barrier is not None:
            return barrier
        # y 缺省走两参形式（Baritone 自动落地面），给了走三参形式
        goto_cmd = (f"#goto {_fmt(x)} {_fmt(y)} {_fmt(z)}" if y is not None
                    else f"#goto {_fmt(x)} {_fmt(z)}")
        position = await self._position()
        if position is None:
            return _StepResult(
                False, "无法开始行走：getStats 未返回有效位置；先观察（getStats）确认已在游戏中")
        await self.client.command(goto_cmd)
        logger.info("walk_to → %s（从 %.1f,%.1f,%.1f 出发）", goto_cmd, *position)

        started = time.monotonic()
        best_dist = math.inf
        last_progress = started
        resent = False  # 看门狗只重发一次（Numen 近重试档）
        while True:
            if cancel is not None and cancel():
                return await self._abort_walk(x, z)
            position = await self._position()
            now = time.monotonic()
            if position is not None:
                px, py, pz = position
                dist = math.hypot(px - x, pz - z)  # 到达判定只看水平距离（y 由寻路器解）
                logger.debug("walk_to 轮询：位置 %.1f,%.1f,%.1f 距目标 %.2f 格", px, py, pz, dist)
                if dist <= WALK_ARRIVE_DIST:
                    logger.info("walk_to 到达：%.1f,%.1f,%.1f（距目标 %.2f 格）", px, py, pz, dist)
                    return _StepResult(
                        True, f"已走到 ({_fmt(px)}, {_fmt(py)}, {_fmt(pz)})，距目标 {dist:.1f} 格")
                if dist < best_dist - 0.05:  # 有实质进展（0.05 格吸走浮点/原地踏步抖动）
                    best_dist = dist
                    last_progress = now
                elif not resent and now - last_progress > WALK_STALL_SECONDS:
                    resent = True
                    last_progress = now
                    logger.info("walk_to %.0fs 无进展（距目标 %.1f 格），重发一次 %s",
                                WALK_STALL_SECONDS, dist, goto_cmd)
                    await self.client.command(goto_cmd)
            if now - started > timeout:
                # 健康超时话术（Numen MoveToCompanionTask）：路程仍在推进但超预算 → 续走可行
                await self.client.command("#stop")
                where = (f"{_fmt(position[0])}, {_fmt(position[1])}, {_fmt(position[2])}"
                         if position else "未知")
                remain = f"{math.hypot(position[0] - x, position[2] - z):.1f} 格" if position else "未知距离"
                logger.warning("walk_to 超时（%.0fs）：仍在 %s，距目标 %s，已发 #stop",
                               timeout, where, remain)
                return _StepResult(
                    False,
                    f"行走超时（{timeout:.0f}s）：现在位于 ({where})，距目标仍 {remain}。"
                    f"路程仍在推进但超出本次预算；同参数重发 walkTo 可续走，或改用更近的途经点分段走")
            await asyncio.sleep(self.poll_interval)

    async def _abort_walk(self, x: float, z: float) -> _StepResult:
        """取消行走：发 #stop 停 Baritone，报当前坐标（续走/换路的决策依据）。"""
        await self.client.command("#stop")
        position = await self._position()
        logger.info("walk_to 被取消（目标 %.1f,%.1f），已发 #stop，当前 %s", x, z, position)
        if position is None:
            return _StepResult(False, "行走已中止（#stop 已发送），当前坐标不可用")
        px, py, pz = position
        dist = math.hypot(px - x, pz - z)
        return _StepResult(
            False, f"行走已中止（#stop 已发送）：当前位于 ({_fmt(px)}, {_fmt(py)}, {_fmt(pz)})，"
                   f"距目标 {dist:.1f} 格；需要继续时同参数重发 walkTo 即可续走")

    async def _wait_screen_clear(self, *, cancel: CancelFlag = None,
                                 timeout: float | None = None) -> _StepResult | None:
        """发 #goto 前的界面屏障：轮询等待任意已打开的 screen 消失（T0b 教训）。

        判定按 getGuiState 的 ``screen_open``（非 null screen 就等）：具体类名里哪些算
        "加载/覆盖类"不可靠（模组可自定义 screen 类），宁可多等一轮也不丢命令。
        返回 None = 已无界面，可以发令；返回 _StepResult = 失败文案（等待超时/被取消）。
        """
        if timeout is None:
            timeout = WALK_SCREEN_BARRIER_TIMEOUT
        started = time.monotonic()
        seen: str | None = None  # 最近一次观测到的占用界面（解除时留档日志用）
        while True:
            screen_class = await self._screen_class()
            if screen_class is None:
                if seen is not None:
                    logger.info("walk_to 屏障解除：等待 %.1fs 后 %s 已消失",
                                time.monotonic() - started, seen)
                return None
            seen = screen_class
            if cancel is not None and cancel():
                logger.info("walk_to 屏障等待中被取消：界面仍被 %s 占用", screen_class)
                return _StepResult(
                    False, f"行走已中止：等待界面 {screen_class} 消失期间收到停止指令"
                           f"（尚未开始行走，未发 #goto）")
            if time.monotonic() - started > timeout:
                logger.warning("walk_to 屏障等待 %.0fs 超时：界面仍被 %s 占用，不发 #goto",
                               timeout, screen_class)
                return _StepResult(
                    False, f"界面被 {screen_class} 占用（等待 {timeout:.0f}s 未消失），"
                           f"此时发命令会丢失；先用 getGuiState 查看并处理界面"
                           f"（必要时按 ESC 关闭），再重发 walkTo")
            await asyncio.sleep(self.poll_interval)

    async def _screen_class(self) -> str | None:
        """getGuiState → 当前占用屏幕的类名（无屏 → None）。

        调用失败视同无屏放行（屏障是尽力而为的防丢命令措施，不该反过来阻塞行走）。
        """
        try:
            result = await self.client.call("getGuiState")
        except Exception as exc:  # noqa: BLE001
            logger.warning("getGuiState 调用失败（屏障视同无界面）：%s", exc)
            return None
        if isinstance(result, dict) and result.get("screen_open"):
            return str(result.get("screen_class") or "unknown")
        return None

    # ------------------------------------------------------------------ dig_block

    async def dig_block(self, x: int, y: int, z: int,
                        timeout: float = DIG_TIMEOUT,
                        cancel: CancelFlag = None) -> ToolOutcome:
        """挖掉 (x,y,z) 的方块（T6 起优先走 bridge dig 智能原语）。

        - 复核存在/触及 → 一次 `dig` RPC（bridge 侧平滑瞄准 300deg/s + 监视按住 +
          遮挡穿透 + 安全检查）→ 按 result 字段直译话术
        - 已空 → 直接成功（幂等）；超触及 → "先 walkTo 到它旁边"教学失败
        - bridge 无 dig 能力（-32601，旧 jar）→ 回退本地 lookAt+hold 段循环
        - timeout/not_digging/blocked_* → 教学：换工具/观察四周/处理隐患
        """
        result = await self._dig_block(x, y, z, timeout=timeout, cancel=cancel)
        return ToolOutcome(result.text)

    async def _dig_block(self, x: int, y: int, z: int, *,
                         timeout: float = DIG_TIMEOUT,
                         cancel: CancelFlag = None,
                         block_id: str | None = None) -> _StepResult:
        # 0) 取自身坐标：触及判定与"查询半径要盖住目标"的复核都依赖它
        position = await self._position()
        if position is None:
            return _StepResult(
                False, f"无法挖掘 ({x},{y},{z})：getStats 未返回有效位置；先观察（getStats）确认状态")
        dist = math.sqrt(_dist2(position[0], position[1], position[2], x + 0.5, y + 0.5, z + 0.5))
        # 1) 存在性复核：getStats 读不到脚下以外的方块，用 world.query 的立方扫描拿
        #    目标坐标的现方块。查询半径取 min(距离+1.5, 64)——盖住目标（"看不到"≠
        #    "不存在"，远处未命中应教学先走位，而不是误报"已空"）；后续每段挖掘的
        #    复核复用同一半径。**截断防御**（M3.5 T5a 真机教训）：Java 侧无 filter
        #    查询的 cap=512 截断发生在距离排序之前——truncated 且未命中不代表"不
        #    存在"（近处目标也会被截掉）。带 block_id 的 filter 复核（cap 32、按距离
        #    升序）不受此影响；无 id 又被截断时保守视为存在，走挖掘流程空挥无害
        scan_range = min(dist + 1.5, COLLECT_RANGE)
        target, reliable = await self._block_at(x, y, z, range_=scan_range,
                                                block_id=block_id)
        if target is None:
            if dist > COLLECT_RANGE:
                logger.info("dig_block 远超感知范围：%.1f 格 > %.0f，放弃并建议先走位",
                            dist, COLLECT_RANGE)
                return _StepResult(
                    False, f"目标 ({x},{y},{z}) 距离 {dist:.1f} 格，远超触及与感知范围"
                           f"（{COLLECT_RANGE:.0f} 格）；先 walkTo 到它附近再挖")
            if reliable:  # 查询完整（未截断）确实看不到 → 真不存在
                return _StepResult(
                    True, f"目标方块 ({x},{y},{z}) 已不存在（此前已挖掉或本就是空气），无需再挖")
            # 截断导致不可信：保守继续（block_id 未知时话术用占位名，复核仍走 filter 无从
            # 谈起——段循环的无 filter 复核不可信时同样视为"还在"，由 8 段上限兜底）
            block_id = block_id or "未知方块"
            logger.warning("dig_block 存在性复核被 cap 截断遮挡：(%d,%d,%d) 按 id=%s 继续尝试",
                           x, y, z, block_id)
        else:
            block_id = target["block"]
        logger.info("dig_block 开始：(%d,%d,%d) 的 %s（距 %.1f 格）", x, y, z, block_id, dist)

        # 2) 触及距离检查：太远不给"盲挖"，教学先走过去（旅行归 walkTo）
        if dist > DIG_REACH:
            logger.info("dig_block 距离不足：%.1f 格 > %.1f，放弃并建议先走位", dist, DIG_REACH)
            return _StepResult(
                False, f"目标 ({x},{y},{z}) 的 {block_id} 距离 {dist:.1f} 格，超出触及范围（{DIG_REACH} 格）；"
                       f"先 walkTo 到它旁边（±1.5 格）再挖")

        # 3) bridge dig 优先（T6 v1.2 智能原语：平滑瞄准 + 监视按住 + 遮挡穿透，
        #    vanilla 松键清进度的坑在 bridge 侧一次按住内解决）。bridge 没有该能力
        #    （试调回 -32601）时记忆并走下方旧 hold 段循环 fallback
        if self._dig_supported is not False:
            bridged = await self._dig_via_bridge(x, y, z, timeout=timeout,
                                                 cancel=cancel, block_id=block_id)
            if bridged is not None:
                return bridged

        # 4) fallback 段循环（v1.1 及以前的行为，原样保留）：看准 → 按住左键（时长按
        #    段递增，穿透树叶类遮挡）→ 等 hold 真正结束 → 复核消失
        started = time.monotonic()
        for segment in range(1, DIG_MAX_SEGMENTS + 1):
            if cancel is not None and cancel():
                px, py, pz = position  # 步骤 0 已保证非空，循环内刷新也只会更不空
                logger.info("dig_block 被取消：(%d,%d,%d) 第 %d 段后中止", x, y, z, segment)
                return _StepResult(
                    False, f"挖掘已中止：当前位于 ({_fmt(px)}, {_fmt(py)}, {_fmt(pz)})，"
                           f"目标 ({x},{y},{z}) 的 {block_id} 尚未破坏")
            hold_ms = min(DIG_CLICK_HOLD_MS * segment, DIG_CLICK_HOLD_MAX_MS)
            await self.client.call("lookAt", {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5})
            await self.client.call("input.click", {"button": 0, "hold_ms": hold_ms})
            # hold 的 RELEASE 由 bridge 端延迟调度（InputTools SCHEDULER）——必须等
            # 本段按住真正结束再复核/发下一段：否则下一段的 PRESS 堆叠在按住中途，
            # 且上一段迟到的 RELEASE 会触发 stopAttack 清掉挖掘进度（M3.5 T5a 真机
            # 教训：settle 0.4s 时 8 段全空）
            await asyncio.sleep(hold_ms / 1000 + DIG_SETTLE)
            # 复核消失：带 block_id 的 filter 复核（cap 32、距离升序）可信；
            # 无 id 时若结果被截断则"未命中"不可信（视为还在，8 段上限兜底）
            cur, cur_reliable = await self._block_at(x, y, z, range_=scan_range,
                                                     block_id=block_id)
            if cur is None and cur_reliable:
                logger.info("dig_block 完成：(%d,%d,%d) 的 %s（第 %d 段）", x, y, z, block_id, segment)
                return _StepResult(True, f"已挖掉 {block_id}（{x},{y},{z}）")
            if time.monotonic() - started > timeout:
                break
            position = await self._position() or position  # 挖掘间隙也刷新坐标（取消话术用）
        logger.warning("dig_block %d 段仍未破坏 (%d,%d,%d) 的 %s", DIG_MAX_SEGMENTS, x, y, z, block_id)
        return _StepResult(
            False, f"无法破坏 ({x},{y},{z}) 的 {block_id}：连续 {DIG_MAX_SEGMENTS} 段挖掘后仍在"
                   f"（可能被遮挡/工具不足/保护规则）；建议 screenshot 观察四周，或换一个目标")

    async def _dig_via_bridge(self, x: int, y: int, z: int, *,
                              timeout: float,
                              cancel: CancelFlag,
                              block_id: str | None) -> _StepResult | None:
        """调 bridge dig 原语并按 result 字段翻译话术；返回 None = bridge 不支持（回退）。

        结果语义（schema v1.2 / DigContracts）：broken/already_air/timeout/not_digging/
        blocked_liquid/blocked_falling——字段直译成教学话术，VLM 读文本即可自救。
        阻塞期间按 poll_interval 检查取消：急停立即返回（bridge 侧按住会自然走完
        ≤timeout_ms 的余量，等价于人类松手延迟）。
        """
        params: dict[str, Any] = {"x": x, "y": y, "z": z,
                                  "timeout_ms": min(int(timeout * 1000), DIG_BRIDGE_TIMEOUT_MS)}
        started = time.monotonic()
        call = asyncio.create_task(self.client.call("dig", params))
        try:
            # asyncio.wait（非 wait_for）：轮询期间绝不取消底层调用，只在 cancel 时取消
            while not call.done():
                await asyncio.wait({call}, timeout=self.poll_interval)
                if call.done():
                    break
                if cancel is not None and cancel():
                    call.cancel()
                    px, py, pz = await self._position() or (0.0, 0.0, 0.0)
                    logger.info("dig（bridge）被取消：(%d,%d,%d) 仍在进行中，提前返回", x, y, z)
                    return _StepResult(
                        False, f"挖掘已中止：当前位于 ({_fmt(px)}, {_fmt(py)}, {_fmt(pz)})，"
                               f"目标 ({x},{y},{z}) 的 {block_id or '方块'} 的 bridge 挖掘"
                               f"已请求停止（客户端侧按住会自然结束）")
            result = call.result()
        except Exception as exc:  # noqa: BLE001 —— bridge 错误帧 → 教学话术/回退
            code = getattr(exc, "code", None)
            message = str(getattr(exc, "message", "") or exc)
            if code == -32601:  # not implemented：旧 jar 没有 dig —— 记忆并回退
                self._dig_supported = False
                logger.info("bridge 无 dig 工具（-32601），回退本地 hold 段循环")
                return None
            if code == -32602 and "触及" in message:
                return _StepResult(
                    False, f"目标 ({x},{y},{z}) 的 {block_id or '方块'} 超出触及范围（{DIG_REACH} 格）；"
                           f"先 walkTo 到它旁边（±1.5 格）再挖")
            logger.warning("bridge dig 调用失败（code=%s）：%s", code, message)
            return _StepResult(
                False, f"挖掘请求被 bridge 拒绝（code={code}：{message}）；"
                       f"可 screenshot 观察现状后重试或换目标")
        self._dig_supported = True
        if not isinstance(result, dict):
            return _StepResult(False, f"挖掘 ({x},{y},{z}) 返回了无法解析的结果；建议重试")
        return self._translate_dig_result(x, y, z, block_id, result,
                                          elapsed=time.monotonic() - started)

    def _translate_dig_result(self, x: int, y: int, z: int,
                              block_id: str | None, result: dict[str, Any],
                              *, elapsed: float) -> _StepResult:
        """bridge dig 的 result 字段 → 契约话术（成功带数字、失败带下一步建议）。"""
        kind = str(result.get("result") or "")
        block = str(result.get("block") or block_id or "方块")
        via_occluder = bool(result.get("broken_via_occluder"))
        reason = result.get("reason")
        drops = self._extract_drops(result)
        if kind == "broken":
            note = "（视线先穿过遮挡物挖通的）" if via_occluder else ""
            # 掉落随话术播报（经验实测清单，模组方块零硬编码——VLM 据此能直接
            # 回答"挖到了什么"）；drops=None（旧 jar）不提，[] 如实说无掉落
            drop_note = ""
            if drops is not None:
                drop_note = ("，掉落 " + "、".join(
                    f"{d['item']}×{d['count']}" for d in drops)) if drops else "，无掉落"
            logger.info("dig（bridge）完成：(%d,%d,%d) 的 %s%s%s", x, y, z, block, note, drop_note)
            return _StepResult(True, f"已挖掉 {block}（{x},{y},{z}）{note}{drop_note}",
                               drops=drops)
        if kind == "already_air":
            return _StepResult(
                True, f"目标方块 ({x},{y},{z}) 已不存在（此前已挖掉或本就是空气），无需再挖")
        if kind == "timeout":
            return _StepResult(
                False, f"无法破坏 ({x},{y},{z}) 的 {block}：持续按住至超时仍未破"
                       f"（工具不足或被保护规则）；建议换合适的工具/镐，或换一个目标")
        if kind == "not_digging":
            return _StepResult(
                False, f"未能挖 ({x},{y},{z}) 的 {block}：没能对准目标或被完全遮挡"
                       f"（客户端始终没进入挖掘状态）；建议 screenshot 观察四周，或走近换角度再试")
        if kind in ("blocked_liquid", "blocked_falling"):
            return _StepResult(
                False, f"拒绝挖掘 ({x},{y},{z}) 的 {block}：安全检查未通过——{reason or kind}"
                       f"（先处理隐患或换一个目标）")
        return _StepResult(
            False, f"挖掘 ({x},{y},{z}) 返回未知结果 {kind!r}（耗时 {elapsed:.1f}s）；"
                   f"建议 screenshot 观察现状后重试")

    @staticmethod
    def _extract_drops(result: dict[str, Any]) -> list[dict[str, Any]] | None:
        """dig 返回的 drops 字段 → 规整后的 ``[{item,count}]``。

        None = 字段缺失（旧 jar v1.2 或非 broken 结果），调用方回落 registry id
        匹配；字段存在则只收形态健全的条目（畸形条目丢弃，整表空 = 真无掉落）。
        """
        drops = result.get("drops")
        if not isinstance(drops, list):
            return None
        shaped: list[dict[str, Any]] = []
        for entry in drops:
            if not isinstance(entry, dict):
                continue
            item, count = entry.get("item"), entry.get("count")
            if isinstance(item, str) and isinstance(count, int) and not isinstance(count, bool):
                shaped.append({"item": item, "count": count})
        return shaped

    # ------------------------------------------------------------------ collect_block

    async def collect_block(self, block_ids: list[str], count: int,
                            pickup: bool = True,
                            cancel: CancelFlag = None) -> ToolOutcome:
        """收集 count 个指定方块：query 最近 → 走到旁边 → 挖掉，循环到收满或清空。

        pickup=True（默认）时每次挖掉后顺路捡起匹配的掉落物（走过去让 vanilla
        吸附）；挖通道/清理地形等不要掉落物的场景传 False。

        收尾契约（Numen MineCompanionTask）：
        - destroyed ≥ count → "已挖到 n/count 个 <ids>"（捡到掉落则附"，已捡起 m 个掉落"）
        - 0 < destroyed < count → "已挖到 n/count；范围内已无更多…"（仍算成功）
        - destroyed == 0 → 失败："范围 64 格内未找到 <ids>；确认 ID（含 #tag 写法）或走近些"
        """
        result = await self._collect_block(block_ids, count, pickup=pickup, cancel=cancel)
        return ToolOutcome(result.text)

    async def _collect_block(self, block_ids: list[str], count: int,
                             pickup: bool = True,
                             cancel: CancelFlag = None) -> _StepResult:
        label = ",".join(block_ids)
        if count < 1:
            return _StepResult(False, f"collect 数量必须 ≥1，收到 {count}")
        destroyed = 0
        picked = 0  # 顺路捡起的掉落物件数（T7）
        stop_reason = ""  # 收尾文案分叉：query 空 / 走位失败 / 挖掘失败 / 取消
        while destroyed < count:
            if cancel is not None and cancel():
                stop_reason = "已中止"
                break
            # 1) 感知：filter 过滤后的候选按与玩家距离升序（T1 契约），取最近
            blocks, _ = await self._query_blocks(block_ids)
            if not blocks:
                stop_reason = "范围内已无更多" if destroyed else ""
                break
            position = await self._position()
            if position is None:
                stop_reason = "getStats 不可用"
                break
            px, py, pz = position
            nearest = min(blocks, key=lambda b: _dist2(px, py, pz,
                                                       b["x"] + 0.5, b["y"] + 0.5, b["z"] + 0.5))
            bx, by, bz = nearest["x"], nearest["y"], nearest["z"]
            # 2) 走位：目标方块旁 ±1.5 格的四个邻点里挑离自己最近的（少走冤枉路，
            #    落点必然在触及范围内）
            candidates = [(bx + COLLECT_NEAR_OFFSET, bz), (bx - COLLECT_NEAR_OFFSET, bz),
                          (bx, bz + COLLECT_NEAR_OFFSET), (bx, bz - COLLECT_NEAR_OFFSET)]
            wx, wz = min(candidates, key=lambda c: math.hypot(px - c[0], pz - c[1]))
            logger.debug("collect_block：%s 最近候选 (%d,%d,%d)，走位到 (%s,%s)",
                         label, bx, by, bz, _fmt(wx), _fmt(wz))
            walk = await self._walk_to(wx, wz, cancel=cancel)
            if not walk.ok:
                stop_reason = f"走位未成功（{walk.text}）"
                break
            # 3) 挖掘（cancel 已透传；挖掉才计数）。block_id 透传给 dig 的复核：
            #    filter 复核（cap 32 距离升序）不受无 filter cap 512 截断 bug 影响
            dig = await self._dig_block(bx, by, bz, cancel=cancel,
                                        block_id=nearest["block"])
            if dig.ok:
                destroyed += 1
                if pickup:
                    # 挖后拾取（T7）：掉落物落在挖掉的方块旁 1-2 格，vanilla 拾取半径
                    # ~1 格需走过去吸附。匹配集优先级（M3.6 T3）：dig 实测 drops >
                    # registry id（挖掉的方块 id + block_ids 纯 id 项；#tag 无法在
                    # item 注册名上展开，忽略）
                    drop_ids = [nearest["block"]] + [b for b in block_ids
                                                     if not b.startswith("#")]
                    picked += await self._collect_drops(
                        (bx + 0.5, by + 0.5, bz + 0.5), drop_ids,
                        drops=dig.drops, cancel=cancel)
                logger.info("collect_block 进度：%d/%d 个 %s（累计拾取 %d 件掉落）",
                            destroyed, count, label, picked)
            else:
                stop_reason = f"挖掘受阻（{dig.text}）"
                break

        # 收尾契约（话术分叉见 docstring；捡到掉落时在末尾附拾取注记）
        pickup_note = f"，已捡起 {picked} 个掉落" if picked > 0 else ""
        if destroyed >= count:
            logger.info("collect_block 完成：%d/%d 个 %s（拾取 %d 件掉落）",
                        destroyed, count, label, picked)
            return _StepResult(True, f"已挖到 {destroyed}/{count} 个 {label}{pickup_note}")
        if destroyed > 0:
            return _StepResult(
                True, f"已挖到 {destroyed}/{count} 个 {label}{pickup_note}；"
                      f"{stop_reason or '范围内已无更多'}，可接受这个结果，或走远后再试")
        if stop_reason:  # 一个都没挖到且不是"没找到"：把微步骤的教学建议原样上抛
            return _StepResult(False, f"未挖到任何 {label}：{stop_reason}")
        return _StepResult(
            False, f"范围 {COLLECT_RANGE:.0f} 格内未找到 {label}；请确认方块 ID "
                   f"（支持 #tag 写法，如 #minecraft:logs），或走近一些再试")

    # ------------------------------------------------------------------ 拾取（T7）

    async def _collect_drops(self, dug_pos: tuple[float, float, float],
                             item_ids: list[str],
                             drops: list[dict[str, Any]] | None = None,
                             cancel: CancelFlag = None) -> int:
        """挖后拾取：捡走 dug_pos 附近（DROP_NEAR_DIG_DIST 内）、匹配的掉落物，
        返回捡到件数（0 也是正常收尾——掉落被烧毁/被别人先捡）。

        匹配集优先级（M3.6 T3）：**dig 实测 drops**（bridge 破坏后实测掉落实体，
        经验主义——模组方块掉什么就捡什么，零硬编码）> registry id 精确匹配
        （item_ids，旧 jar 无 drops 字段时的兼容回落）。drops=[] 是明确无掉落，
        直接收 0 不回落（避免把别人的同 id 掉落误当自己的捡走）。

        多人服礼仪：匹配集外的掉落绝对不碰（别人的掉落/树叶掉的树苗等）；#tag 条目
        无法在 item 注册名上展开（无 item tag 查询通道），保守忽略。
        """
        if drops is not None:
            wanted = {str(d["item"]) for d in drops if isinstance(d.get("item"), str)}
        else:
            wanted = {i for i in item_ids if not i.startswith("#")}
        if not wanted:
            return 0
        picked, _skipped = await self._sweep_drops(
            wanted, center=dug_pos, near_limit=DROP_NEAR_DIG_DIST, cancel=cancel)
        return picked

    async def pickup(self, item_ids: list[str] | None = None,
                     radius: float = DROP_QUERY_RANGE,
                     timeout: float = PICKUP_TIMEOUT,
                     cancel: CancelFlag = None) -> ToolOutcome:
        """捡起身边 radius 格内的掉落物（Numen collect_items 对应物，M3.6 注册为 VLM 工具）。

        走到每个匹配掉落物旁让 vanilla 吸附；实体消失 = 已拾取（无论谁捡的）。
        item_ids 给定时只捡注册名匹配的（#tag 条目无法展开，见 _collect_drops）；
        缺省（None）捡范围内**全部**掉落——多人服礼仪靠调用方（VLM 工具描述）约束：
        只对明确属于自己活动的掉落使用缺省形式。0 件也是成功（"范围内没有可捡的"）。
        """
        if item_ids is None:
            wanted: set[str] | None = None
            label = "全部掉落物"
        else:
            wanted = {i for i in item_ids if not i.startswith("#")}
            label = ",".join(item_ids)
            if not wanted:
                return ToolOutcome(f"pickup 需要至少一个非 #tag 的物品注册名，收到 {label}")
        picked, _skipped = await self._sweep_drops(
            wanted, center=None, near_limit=None, radius=radius, timeout=timeout,
            cancel=cancel)
        if picked > 0:
            return ToolOutcome(f"已捡起 {picked} 个 {label}")
        return ToolOutcome(f"范围 {radius:.0f} 格内没有可捡的 {label}")

    async def _sweep_drops(self, wanted: set[str] | None, *,
                           center: tuple[float, float, float] | None,
                           near_limit: float | None,
                           radius: float = DROP_QUERY_RANGE,
                           timeout: float = PICKUP_TIMEOUT,
                           cancel: CancelFlag = None) -> tuple[int, int]:
        """掉落物清扫核心（Numen CollectItemsCompanionTask 状态机的轮询版）。

        SCAN（查匹配掉落，无 → 收）→ APPROACH（walk_to 到它旁边）→ 实体消失 =
        已拾取（无论谁捡的）；走到身旁（≤PICKUP_ARRIVE_DIST）仍没吸上 / 走不过去
        → skip 该实体（防死循环）。每轮有捡到或跳过即有进度，无进度或超时即收。
        wanted=None 是"全部掉落"（pickup 缺省形式）；center+near_limit 给定时只捡
        中心点 near_limit 格内的（挖后拾取的"只捡自己挖出的"约束）；缺省时整个
        radius 圈内都算（pickup() 语义）。返回 (捡到件数, 跳过件数)。
        """
        picked = 0
        skipped = 0
        skip_uuids: set[str] = set()
        attempts: dict[str, int] = {}
        started = time.monotonic()
        while True:
            if cancel is not None and cancel():
                logger.info("拾取被取消：已捡 %d 件，跳过 %d 件", picked, skipped)
                break
            if time.monotonic() - started > timeout:
                logger.info("拾取 %.0fs 收（已捡 %d 件，跳过 %d 件）",
                            timeout, picked, skipped)
                break
            drops = await self._query_drops(radius=radius)
            position = await self._position()
            if drops is None or position is None:
                logger.warning("拾取中止：掉落物查询 %s，位置 %s",
                               "失败" if drops is None else "正常",
                               "不可用" if position is None else "正常")
                break
            candidates = []
            for drop in drops:
                if drop["uuid"] in skip_uuids:
                    continue
                if wanted is not None and drop["item"] not in wanted:
                    continue  # 匹配集外：别人的/不相干的掉落绝对不碰（多人服礼仪）
                if (near_limit is not None and center is not None
                        and _dist2(drop["x"], drop["y"], drop["z"], *center)
                        > near_limit * near_limit):
                    continue  # 挖点 4 格外：可能是别人的，不碰（多人服礼仪）
                candidates.append(drop)
            if not candidates:
                break  # 全部消失 / 全部 skip 完 / 本来就没有 → 收
            # 最近的先捡（Numen nearestItem）；走位途中路过其他掉落也会被 vanilla 吸走
            target = min(candidates,
                         key=lambda d: _dist2(position[0], position[1], position[2],
                                              d["x"], d["y"], d["z"]))
            before = {drop["uuid"] for drop in candidates}
            walk = await self._walk_to(target["x"], target["z"], cancel=cancel)
            if not walk.ok:
                skip_uuids.add(target["uuid"])  # 走不过去（Numen nav FAILED → skip）
                skipped += 1
                continue
            # 走位返回 ≠ 停稳：walk_to 在 ≤2 格即返回，Baritone 还在收尾——轮询等
            # before 里的实体消失（吸附发生）或静默超时
            after = await self._settle_drops(before, radius=radius, cancel=cancel)
            after_ids = {drop["uuid"] for drop in after}
            gone = before - after_ids
            picked += len(gone)  # 消失即已拾取（无论谁捡的，Numen 语义）
            if target["uuid"] in gone:
                logger.info("拾取：捡到 %s（本轮共 %d 件消失）", target["item"], len(gone))
                continue
            # 目标还在：换最新坐标再试一次（#goto 收尾半径 2 格可能停在 1.2-2.0 之间）
            attempts[target["uuid"]] = attempts.get(target["uuid"], 0) + 1
            position = await self._position() or position
            near = math.hypot(position[0] - target["x"], position[2] - target["z"])
            if (near > PICKUP_ARRIVE_DIST
                    and attempts[target["uuid"]] < PICKUP_MAX_ATTEMPTS):
                continue
            skip_uuids.add(target["uuid"])  # 就在身旁仍没吸上 / 重走也没用 → 够不着
            skipped += 1
            logger.info("拾取：跳过 %s（距离 %.1f 格仍没吸附，第 %d 次尝试后放弃）",
                        target["item"], near, attempts[target["uuid"]])
        return picked, skipped

    async def _settle_drops(self, before_ids: set[str], *,
                            radius: float,
                            cancel: CancelFlag = None) -> list[dict[str, Any]]:
        """走位后的吸附静默：轮询掉落物查询，直到 before_ids 全部消失（吸上了）或
        静默超时。返回最后一次查询结果（调用方据此判定谁消失了）。"""
        deadline = time.monotonic() + PICKUP_SETTLE_SECONDS
        drops: list[dict[str, Any]] = []
        while True:
            drops = await self._query_drops(radius=radius) or []
            remaining = {drop["uuid"] for drop in drops} & before_ids
            if not remaining:
                return drops
            if cancel is not None and cancel():
                return drops
            if time.monotonic() > deadline:
                return drops
            await asyncio.sleep(self.poll_interval)

    async def _query_drops(self, *, radius: float = DROP_QUERY_RANGE
                           ) -> list[dict[str, Any]] | None:
        """world.query(entities, filter=[minecraft:item]) → 掉落物条目列表。

        条目带 T7 的 item 注册名与 count；没有 item 字段的条目（旧 jar）被丢弃——
        无法按注册名匹配的掉落绝对不碰（礼仪优先于覆盖率）。返回 None = 查询失败。
        """
        try:
            result = await self.client.call(
                "world.query", {"type": "entities", "range": radius,
                                "filter": ["minecraft:item"]})
        except Exception as exc:  # noqa: BLE001 —— 感知失败：让上层收手而不是瞎走
            logger.warning("world.query(entities) 调用失败：%s", exc)
            return None
        entities = result.get("entities") if isinstance(result, dict) else None
        if not isinstance(entities, list):
            return None
        drops: list[dict[str, Any]] = []
        for entry in entities:
            if not isinstance(entry, dict):
                continue
            pos = entry.get("position")
            item = entry.get("item")
            if not isinstance(pos, dict) or not isinstance(item, str):
                continue
            try:
                x, y, z = float(pos["x"]), float(pos["y"]), float(pos["z"])
            except (KeyError, TypeError, ValueError):
                continue
            uuid = str(entry.get("uuid") or f"{item}@{x:.1f},{y:.1f},{z:.1f}")
            drops.append({"uuid": uuid, "item": item,
                          "count": int(entry.get("count") or 1),
                          "x": x, "y": y, "z": z})
        return drops

    # ------------------------------------------------------------------ 感知辅助

    async def _position(self) -> tuple[float, float, float] | None:
        """getStats → 脚底坐标（不可用时 None；调用方决定失败文案）。"""
        try:
            result = await self.client.call("getStats")
        except Exception as exc:  # noqa: BLE001 —— 感知失败降级为 None，由上层翻译
            logger.warning("getStats 调用失败：%s", exc)
            return None
        if not isinstance(result, dict) or not result.get("in_game"):
            return None
        pos = result.get("position")
        if not isinstance(pos, dict):
            return None
        try:
            return float(pos["x"]), float(pos["y"]), float(pos["z"])
        except (KeyError, TypeError, ValueError):
            return None

    async def _query_blocks(self, filters: list[str] | None = None,
                            range_: float = COLLECT_RANGE
                            ) -> tuple[list[dict[str, Any]], bool]:
        """world.query(type=blocks, filter?) → (方块列表, 结果是否完整)。

        bridge 侧已按与玩家距离升序返回（T1 契约），这里只做形态防御。
        truncated=True 表示远处被 cap 截断：带 filter 时（cap 32）最近候选仍可信；
        **无 filter 时（cap 512）Java 侧截断发生在排序前**（M3.5 T5a 真机铁证：
        range=5.5 时 3.71 格近的目标也被截掉）——截断后的"未命中"不可作存在性结论。
        """
        params: dict[str, Any] = {"type": "blocks", "range": range_}
        if filters:
            params["filter"] = list(filters)
        try:
            result = await self.client.call("world.query", params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("world.query 调用失败（filter=%s）：%s", filters, exc)
            return [], True  # 查询本身失败：不截断语义，调用方按"空且完整"处理
        truncated = bool(result.get("truncated")) if isinstance(result, dict) else False
        blocks = result.get("blocks") if isinstance(result, dict) else None
        if not isinstance(blocks, list):
            return [], True
        shaped = [b for b in blocks if isinstance(b, dict)
                  and all(isinstance(b.get(key), (int, float)) for key in ("x", "y", "z"))
                  and isinstance(b.get("block"), str)]
        return shaped, not truncated

    async def _block_at(self, x: int, y: int, z: int, *,
                        range_: float,
                        block_id: str | None = None) -> tuple[dict[str, Any] | None, bool]:
        """目标坐标的现方块 → (方块或 None, 未命中是否可信)。

        未命中可信当且仅当查询**未被截断**：真机实证（M3.5 T5a）Java 侧 cap 截断
        发生在距离排序之前——无 filter（cap 512）与带 filter（cap 32）都会把**近处
        方块**截掉，truncated=True 时"目标不在结果里"不能推出"目标不存在"。
        未截断时结果完整且升序，未命中即可信判无。调用方保证查询半径盖住目标。
        """
        filters = [block_id] if block_id else None
        blocks, complete = await self._query_blocks(filters, range_=range_)
        for block in blocks:
            if (block["x"], block["y"], block["z"]) == (x, y, z):
                return block, True
        return None, complete


__all__ = [
    "CancelFlag",
    "Primitives",
    "COLLECT_NEAR_OFFSET",
    "COLLECT_RANGE",
    "DIG_BRIDGE_TIMEOUT_MS",
    "DIG_CLICK_HOLD_MS",
    "DIG_CLICK_HOLD_MAX_MS",
    "DIG_MAX_SEGMENTS",
    "DIG_REACH",
    "DIG_SETTLE",
    "DIG_TIMEOUT",
    "DROP_NEAR_DIG_DIST",
    "DROP_QUERY_RANGE",
    "PICKUP_ARRIVE_DIST",
    "PICKUP_MAX_ATTEMPTS",
    "PICKUP_SETTLE_SECONDS",
    "PICKUP_TIMEOUT",
    "WALK_ARRIVE_DIST",
    "WALK_SCREEN_BARRIER_TIMEOUT",
    "WALK_STALL_SECONDS",
    "WALK_TIMEOUT",
]
