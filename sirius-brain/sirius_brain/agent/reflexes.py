"""M4 反射层：等级框架（L0/L1/L2 预留）+ 调度器 + L1 七条脊髓反射。

设计来自 spec ``docs_agent/session/2026-08-20-M4.md``（Numen CompanionBrain 的
asyncio 移植）+ 三份精读报告的触发阈值。核心主张：

- **脊髓不过大脑**：溺水上浮/着火撤离/危怪逃离/卡位挣脱全部在确定性代码里完成，
  全程零 VLM token；认知层只在事后收到一行简报（behavior_log flush）或一条
  〔紧急〕消息（death/低血），不做事前请示
- **等级是严格单调能力束**（Numen 删空闸教训：不做每条反射的开关）——L0 观察
  （关动作不关感知）/ L1 自保（默认，七条全开）/ L2 自卫（枚举位预留，客户端
  战斗模块后置独立轮次）。升级人类-only：聊天里说"反射等级 观察/自保"，
  绝不经 VLM 工具表
- **调度器**：0.5s 轮询协程，按注册序问 ``can_run``，首个 True 胜出（层内单候选，
  结构性消灭比较）；轮询为主，M2-B 的 CRITICAL 事件只作加速器/边沿置位
- **打断三档**：``none``（旁路，如转头看主人）/ ``cooperative``（短暂接管按键后
  归还，任务不停，如换气/脱困）/ ``preempt``（经 AgentLoop.request_preempt 掀翻
  当前任务，如撤离/逃离/低血/死亡）——真客户端多输入天然共存，preempt 才需要
  掀任务
- **边沿自持**：每条反射自己管理触发窗/冷却/一次性闩（死亡的 reported 闩在复活
  后重臂）；fire/health_low 的抖动抑制主要靠 bridge 侧 5s 事件冷却 + 本地小冷却
- **先归位再宣布**：反射 act 结束后才写 behavior_log（下一轮 VLM 调用前替换式
  flush 成一条〔本能反应〕消息，尾 500 字符）；无持久化、无检索——纯消息

信号源（实事求是版）：
- getStats 轮询（2Hz）：position/health/air/in_game/alive——**没有** on_fire、
  没有眼位水、没有 yaw（schema v1.2 实测字段就这些）
- 眼在水中：air<300 时才花一次 world.query(blocks, filter=water) 查眼位方块
  （空气满时连这一次都省）；结果被截断时保守视为在水里（M3.5 T5a 教训：截断
  发生在排序前，"眼位格不在结果里"不可信，而被水包围时截断恰恰是常态）
- on_fire / dead / health_low：CRITICAL 事件置位（M2-B 的 danger 采样器，
  边沿 + 5s 冷却）；dead 另有 getStats.alive=False 的轮询兜底与复活重臂
- 危怪：entities 载荷的 category=="monster"（T1 注册表数据，模组怪自动归队）
  + width（危险半径 = width/2 + 1.5，Numen Menace 的简化：不查 attribute/
  攻击距离，近战怪碰撞箱宽近似——远程怪（ skeleton 等）会偏保守，报告注明）
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Literal

from sirius_brain.protocol import NotificationFrame

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------- 等级框架


class ReflexLevel(Enum):
    """反射能力等级（严格单调能力束：L0 ⊂ L1 ⊂ L2）。

    - OBSERVER（L0 观察）：关动作不关感知——危险事件照常进认知（〔危险〕行 +
      〔紧急〕消息），脊髓不接管
    - SELF_PRESERVE（L1 自保，默认）：七条反射全开
    - GUARD（L2 自卫）：**预留枚举位**，实现后置独立轮次（客户端战斗模块是
      最难件）；接入时必须同步修订 loop 的安全约束节（"禁止攻击"）与 instincts
    """

    OBSERVER = "observer"
    SELF_PRESERVE = "self_preserve"
    GUARD = "guard"


#: 各等级的中文播报名（切换确认/instincts 共用）
LEVEL_LABELS: dict[ReflexLevel, str] = {
    ReflexLevel.OBSERVER: "观察（L0）",
    ReflexLevel.SELF_PRESERVE: "自保（L1）",
    ReflexLevel.GUARD: "自卫（L2，预留）",
}

#: 聊天切换词 → 等级（人类-only 通道；大小写不敏感由匹配端 lower() 保证）
LEVEL_SWITCH_WORDS: dict[str, ReflexLevel] = {
    "观察": ReflexLevel.OBSERVER,
    "observer": ReflexLevel.OBSERVER,
    "l0": ReflexLevel.OBSERVER,
    "自保": ReflexLevel.SELF_PRESERVE,
    "self_preserve": ReflexLevel.SELF_PRESERVE,
    "l1": ReflexLevel.SELF_PRESERVE,
    "自卫": ReflexLevel.GUARD,
    "guard": ReflexLevel.GUARD,
    "l2": ReflexLevel.GUARD,
}

#: 切换命令的特征前缀（消息里含它才尝试解析等级）
REFLEX_LEVEL_PREFIX = "反射等级"

#: LoopConfig.reflex_level 合法值（guard 允许配置但切换时会被拒——预留位）
CONFIG_LEVEL_VALUES = ("observer", "self_preserve", "guard")


def parse_reflex_level(value: str) -> ReflexLevel:
    """配置字符串 → ReflexLevel（错值抛 ValueError，宁可启动失败不静默降级）。"""
    try:
        return ReflexLevel(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"reflex_level 须为 {'/'.join(CONFIG_LEVEL_VALUES)}，got {value!r}") from exc


def match_reflex_level_command(message: str) -> ReflexLevel | None:
    """聊天消息 → 目标反射等级；不是切换命令返回 None（当普通指令走）。

    识别"反射等级"前缀 + 任一切换词（如"反射等级 观察"/"反射等级切到自保"）；
    含前缀但没认出目标词 → None（交给 VLM 当普通对话处理）。
    """
    text = message.strip().lower()
    if REFLEX_LEVEL_PREFIX not in text:
        return None
    for word, level in LEVEL_SWITCH_WORDS.items():
        if word in text:
            return level
    return None


def instincts_section(level: ReflexLevel) -> str:
    """系统提示的 <本能> 节（等级是代码层与认知层唯一同步点）。

    L0：不写反射清单，只说"危险只会被告知"；L1：清单 +『你的本能会自动换气/
    脱困/撤离/逃离，触发时无需惊讶，它们不消耗你的决策』；L2：预留框架注释
    （接入时需同步修订安全约束）。
    """
    if level is ReflexLevel.OBSERVER:
        return (
            "## 本能（反射）\n"
            "当前反射等级：观察（L0）。你没有本能反射，一切自理——危险发生时\n"
            "只会被告知（消息里出现〔危险〕/〔紧急〕前缀的简报），如何处置完全\n"
            "由你决定。\n"
        )
    if level is ReflexLevel.GUARD:
        # 预留位：L2 接入时此节与 loop 的安全约束（"禁止攻击"）必须同步修订
        return (
            "## 本能（反射）\n"
            "当前反射等级：自卫（L2，预留位——尚未实现，行为等同自保）。\n"
        )
    return (
        "## 本能（反射）\n"
        "当前反射等级：自保（L1）。你的身体有脊髓级本能——换气/脱困/撤离/逃离\n"
        "会自动执行：触发时无需惊讶，它们不消耗你的决策，也不占用你的工具调用。\n"
        "- 换气：眼在水中且氧气不足时，自动按住上浮\n"
        "- 脱困：想走却走不动时，自动扇形转向爆发 + 跳跃挣脱\n"
        "- 撤离：着火时，自动停下任务去找水或反向撤离\n"
        "- 逃离：敌对怪贴身时，自动反向逃离（只逃不攻击）\n"
        "死亡与低血会以〔紧急〕消息通知你并中止当前任务；其余反射动作事后你会\n"
        "在消息里看到〔本能反应〕简报。\n"
    )


# ---------------------------------------------------------------------- 身体状态

#: 玩家眼位高度（格）：与 FakeWorldBridge.EYE_HEIGHT 同源（vanilla 1.62）
EYE_HEIGHT = 1.62
#: 换气触发：氧气 ≤240/300（精读实测值；air==300 为满）
BREATH_AIR_THRESHOLD = 240
#: 换气动作上限（秒）：封顶还浮不上去 → 立刻上报失败
BREATH_MAX_SECONDS = 10.0
#: 每次上浮按键的按住时长（毫秒）；循环续按直到脱离。
#: M4.1 压测修正（深水死亡归因）：旧值 400ms 按 / 500ms 歇的低占空比在深水柱
#: 净上浮≈0（按住上升段被歇息下沉段吃光）；改高占空比——按满 800ms 等释放
#: 再复查，间隙只剩一次 getStats 往返（T6 深水场景回归 PASS 的来源）
BREATH_PRESS_MS = 800
#: 脱困窗口（秒）：40 tick 精读实测值
UNSTUCK_WINDOW = 2.0
#: 脱困位移阈值（格）：窗口内位移 <0.75 判卡住
UNSTUCK_MIN_DISPLACEMENT = 0.75
#: 脱困冷却（秒）：爆发后给身体一点时间再判
UNSTUCK_COOLDOWN = 6.0
#: 脱困扇形爆发段数
UNSTUCK_BURSTS = 3
#: 扇形转向步进（度）：Numen unstuck 的 137° 黄金角
UNSTUCK_TURN_DEG = 137.0
#: 撤离找水半径（格）
FIRE_WATER_SEARCH_RADIUS = 20.0
#: 撤离无水时反向撤退距离（格）
FIRE_RETREAT_BLOCKS = 5.0
#: 撤离行走上限（秒）
FIRE_WALK_TIMEOUT = 12.0
#: 低血上报冷却（秒）：主要防事件抖动（bridge 侧已有 5s 边沿冷却）
HEALTH_LOW_COOLDOWN = 8.0
#: 危怪扫描半径（格）
FLEE_SCAN_RADIUS = 12.0
#: 危险半径基数（格）：danger = FLEE_DANGER_BASE + width/2
FLEE_DANGER_BASE = 1.5
#: 逃离距离（格）：反方向走 8 格
FLEE_ESCAPE_BLOCKS = 8.0
#: 逃离行走上限（秒）
FLEE_WALK_TIMEOUT = 10.0
#: 逃离冷却（秒）
FLEE_COOLDOWN = 6.0
#: 注视主人：播报后转头窗口（秒）与主人搜索半径（格）
SPEAKING_WINDOW_SECONDS = 2.5
SPEAKING_LOOK_RADIUS = 16.0
#: 注视转头最小间隔（秒）：一个播报窗口内只转一次
SPEAKING_LOOK_MIN_INTERVAL = 1.2
#: 死亡闩的复活观察期（秒）：alive=True 持续超过它才清闩（防轮询/事件竞态）
DEATH_RELATCH_GRACE = 5.0

# GLFW 键码（冻结 schema input.key.code 为整数；与 bridge/client.py 同源）
GLFW_KEY_W = 87
GLFW_KEY_SPACE = 32

#: 打断级别（spec 三档）
InterruptKind = Literal["none", "cooperative", "preempt"]


@dataclass
class ThreatFact:
    """进入危险半径的敌对怪快照（flee 的输入）。"""

    uuid: str
    type: str
    position: tuple[float, float, float]
    width: float
    distance: float

    @property
    def danger_radius(self) -> float:
        return FLEE_DANGER_BASE + self.width / 2.0


@dataclass
class BodyState:
    """反射层的身体快照（调度器 2Hz 采样 + CRITICAL 事件即时置位）。

    position_window 是 (monotonic, (x,y,z)) 的滑动窗（脱困位移判定用）；
    threat/companion 由低频实体采样维护（每 2 轮一次）。
    """

    in_game: bool = False
    position: tuple[float, float, float] | None = None
    health: float = 20.0
    air: int = 300
    on_fire: bool = False          # CRITICAL fire 事件置位；撤离后清位等下一条
    dead: bool = False             # CRITICAL death 事件 / getStats.alive=False
    health_low: bool = False       # CRITICAL health_low 事件置位；上报后清位
    eyes_in_water: bool = False    # air<300 时 world.query 眼位方块
    threat: ThreatFact | None = None
    companion: tuple[float, float, float] | None = None
    movement_active: bool = False  # walkTo/collectBlock 原语执行中（=有移动输入）
    yaw: float | None = None       # getStats.yaw（M4.1：协议 1.3 起上报；None=旧 bridge）
    position_window: deque = field(
        default_factory=lambda: deque(maxlen=12))


def _fmt(value: float) -> str:
    """坐标 → 命令参数文本（与 primitives._fmt 同规则：10.0→"10"）。"""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"


def _dist3(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _position_of(result: Any) -> tuple[float, float, float] | None:
    """getStats/world.query 条目 → 坐标元组（形态防御）。"""
    if not isinstance(result, dict):
        return None
    pos = result.get("position")
    if not isinstance(pos, dict):
        return None
    try:
        return float(pos["x"]), float(pos["y"]), float(pos["z"])
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------- 反射基类


class ReflexChain:
    """反射链基类：id/所属等级束/打断级别 + can_run/act 两个钩子。

    子类约定：``can_run`` 必须纯净（只读 BodyState 与自身边沿状态，不发 IO）；
    ``act`` 结束前把该说的写进 ``scheduler.log``（先归位再宣布）。
    """

    id: str = "chain"
    level: ReflexLevel = ReflexLevel.SELF_PRESERVE
    interrupt: InterruptKind = "cooperative"

    def __init__(self, scheduler: "ReflexScheduler") -> None:
        self.scheduler = scheduler

    def can_run(self, s: BodyState) -> bool:  # noqa: ARG002
        return False

    async def act(self, s: BodyState) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------- 调度器


class ReflexScheduler:
    """0.5s 轮询调度协程：采样身体 → 按注册序问 can_run → 首个胜出执行。

    用法（AgentLoop 接线后的完整形态）::

        scheduler = ReflexScheduler(loop.tools_client, level=...,
                                    preempt=loop.request_preempt,
                                    urgent=loop.inject_urgent,
                                    self_uuid=lambda: loop.self_uuid)
        scheduler.install_default_chains()
        asyncio.create_task(scheduler.run())   # AgentLoop.run() 里随任务队列并行
    """

    #: M2-B danger 事件集（CRITICAL；订阅与 handler 注册共用）
    DANGER_EVENTS = ("death", "fire", "health_low", "drown")

    def __init__(
        self,
        client: Any,
        *,
        level: ReflexLevel = ReflexLevel.SELF_PRESERVE,
        poll_interval: float = 0.5,
        preempt: Callable[[str], None] | None = None,
        urgent: Callable[[str], None] | None = None,
        self_uuid: Callable[[], str | None] | None = None,
        broadcast: Callable[[str], Any] | None = None,
        broadcast_direct: Callable[[str], Any] | None = None,
    ) -> None:
        self.client = client
        self.level = level
        self.poll_interval = poll_interval
        self._preempt = preempt or (lambda reason: None)
        self._urgent = urgent or (lambda text: None)
        self._self_uuid = self_uuid or (lambda: None)
        # 呼吸失败等"立刻上报"用；缺省退化成 log（单元测试不必带完整 loop）
        self._broadcast = broadcast
        # 死亡等 GUI 屏蔽场景的直发通道（M4.1 T3，chat.send 绕开 T 键）；
        # 缺省回落 broadcast
        self._broadcast_direct = broadcast_direct
        self.body = BodyState()
        # 位置窗按轮询间隔定容：固定 maxlen 会在快轮询（测试 0.05s）下装不满
        # 2s 判定窗。容量取 6s 的样本数（≥12 兜底极慢轮询）
        self.body.position_window = deque(
            maxlen=max(12, int(round(6.0 / max(poll_interval, 1e-3)))))
        self.chains: list[ReflexChain] = []
        #: 事后知会缓冲（AgentLoop 每轮 VLM 调用前 flush 尾部）
        self.behavior_log: deque[str] = deque(maxlen=200)
        #: 反射 act 执行中（AgentLoop 的任务门：busy 期间不开新任务）
        self.busy = False
        self._running = False
        self._speaking_until = 0.0
        self._entity_tick = 0
        self._dead_latch = False
        self._dead_latch_at = 0.0
        # M4.1 T5：cooperative 反射期间的 Baritone 暂停态（pause/resume 配对标记）
        self._baritone_paused = False

    # ------------------------------------------------------------------ 装配

    def install_default_chains(self) -> None:
        """按优先级注册 L1 七条（注册序即 can_run 询问序，首个 True 胜出）。"""
        for chain_cls in (DeathReflex, HealthLowReflex, FireReflex, FleeReflex,
                          BreathReflex, UnstuckReflex, SpeakingLookReflex):
            self.chains.append(chain_cls(self))

    def danger_handler(self, event: str) -> Callable[[NotificationFrame], None]:
        """CRITICAL 事件 → BodyState 置位 + 〔危险〕行（L0 也照记：关动作不关感知）。"""
        def handler(frame: NotificationFrame) -> None:
            data = frame.data or {}
            if event == "death":
                self._dead_latch = True
                self._dead_latch_at = time.monotonic()
                self.body.dead = True
                self.log(f"〔危险〕死亡事件（health={data.get('health')}）")
            elif event == "fire":
                self.body.on_fire = True
                self.log("〔危险〕着火了（CRITICAL fire）")
            elif event == "health_low":
                self.body.health_low = True
                self.log(f"〔危险〕生命值过低 health={data.get('health')}"
                         f"（阈值 {data.get('threshold', 6)}）")
            elif event == "drown":
                # 加速器：换气反射本体靠轮询（air/眼位水），这里只补一行认知简报
                self.log(f"〔危险〕溺水警报 air={data.get('air')}")
        return handler

    # ------------------------------------------------------------------ 外部注记

    def note_speaking(self, seconds: float = SPEAKING_WINDOW_SECONDS) -> None:
        """LoopClient.command 发出一条聊天后调用：打开注视窗口。"""
        self._speaking_until = time.monotonic() + seconds

    def note_movement(self, active: bool) -> None:
        """AgentLoop 在 walkTo/collectBlock 原语执行前后调用（脱困的"有输入"信号）。"""
        self.body.movement_active = active

    def log(self, line: str) -> None:
        """事后知会：反射动作/危险感知追加单行（下一轮 VLM 调用前 flush）。"""
        self.behavior_log.append(line)
        logger.info("反射简报入列：%s", line)

    def preempt(self, reason: str) -> None:
        """preempt 档反射经 AgentLoop 掀翻当前任务。"""
        self._preempt(reason)

    def urgent(self, text: str) -> None:
        """〔紧急〕消息（death/低血）：经 AgentLoop 注入活动任务或排队给下个任务。"""
        self._urgent(text)

    async def broadcast(self, text: str) -> None:
        """反射的聊天上报（缺省退化成 log——单元测试/无 loop 场景）。"""
        if self._broadcast is not None:
            result = self._broadcast(text)
            if asyncio.iscoroutine(result):
                await result
        else:
            self.log(f"〔本能〕{text}")

    async def broadcast_direct(self, text: str) -> None:
        """直发聊天播报（M4.1 T3）：经 bridge 的 chat.send 通道绕开 T 键 GUI——
        死亡屏打开时 T 键唤不起聊天框（M4-rerun §3.3：死亡播报 wire 已发、
        游戏聊天无此行）。未接线时回落 broadcast（GUI 路径）。"""
        if self._broadcast_direct is not None:
            result = self._broadcast_direct(text)
            if asyncio.iscoroutine(result):
                await result
        else:
            await self.broadcast(text)

    # ------------------------------------------------------------------ Baritone 让路（M4.1 T5）

    async def safe_command(self, text: str) -> None:
        """尽力发一条命令：失败只记日志、不抛出。

        T6 压测实证（深水回归轮）：低血反射的 #stop 被 GUI 占用拒绝
        （-32002）时抛异常会**跳过后续的直发警报与 urgent 注入**——保命链的
        停止动作不该有能力打断警报。preempt 反射的关键路径统一走此口。
        """
        try:
            await self.client.command(text)
        except Exception as exc:  # noqa: BLE001 —— 命令失败降级为日志
            logger.warning("反射命令 %r 发送失败（继续后续动作）：%s", text, exc)

    async def pause_baritone(self) -> None:
        """cooperative 反射生效期间暂停 Baritone 寻路（``#pause``）。

        T5 裁决背景：cooperative 反射只接管按键、不掀任务也不撤目标——水下
        GoalBlock 会把换气反射刚浮起的 bot 再按下去（M4-rerun 拉锯实证）。
        #pause 让 Baritone 停下按键但保住目标，反射结束后 #resume 续走
        （比 #stop+事后不重启优：walk 任务的原语还在等续走）。幂等；发送失败
        只记日志——暂停不成也不该阻断保命动作本身。
        """
        if self._baritone_paused:
            return
        self._baritone_paused = True
        try:
            await self.client.command("#pause")
            logger.info("Baritone 已 #pause（cooperative 反射接管按键）")
        except Exception as exc:  # noqa: BLE001
            logger.warning("#pause 发送失败（反射动作继续）：%s", exc)

    async def resume_baritone(self) -> None:
        """``pause_baritone`` 的对偶（只在真暂停过时发，幂等）。

        期间若 preempt 反射已 ``#stop``（目标撤销），#resume 无目标可续、是
        无害空操作——照发不误，逻辑上仍配对。
        """
        if not self._baritone_paused:
            return
        self._baritone_paused = False
        try:
            await self.client.command("#resume")
            logger.info("Baritone 已 #resume（cooperative 反射归还控制）")
        except Exception as exc:  # noqa: BLE001
            logger.warning("#resume 发送失败：%s", exc)

    # ------------------------------------------------------------------ 主循环

    async def run(self) -> None:
        """常驻调度协程：采样 → 依序选链执行（单候选）→ 睡 poll_interval。"""
        self._running = True
        logger.info("ReflexScheduler 启动：等级=%s 反射=%s 轮询=%.2fs",
                    self.level.value, ",".join(c.id for c in self.chains),
                    self.poll_interval)
        while self._running:
            try:
                await self.sample_body()
                if self.body.in_game:
                    for chain in self.chains:
                        if chain.level.value > self.level.value:
                            continue  # 等级门控：L0 下七条全跳过
                        if not chain.can_run(self.body):
                            continue
                        logger.info("反射触发：%s（interrupt=%s）",
                                    chain.id, chain.interrupt)
                        self.busy = True
                        try:
                            await chain.act(self.body)
                        except Exception:  # noqa: BLE001 —— 单条反射异常不杀调度器
                            logger.exception("反射 %s 执行异常", chain.id)
                        finally:
                            self.busy = False
                        break  # 层内单候选：本轮有链执行即结束
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 采样异常不杀调度器
                logger.exception("反射调度轮异常")
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------ 采样

    async def sample_body(self) -> None:
        """getStats（2Hz）→ 位置/生命/氧气/存活；air<300 时查眼位水；低频实体采样。"""
        try:
            stats = await self.client.call("getStats")
        except Exception as exc:  # noqa: BLE001 —— 感知失败跳过本轮，旧状态留用
            logger.debug("反射采样 getStats 失败：%s", exc)
            return
        if not isinstance(stats, dict) or not stats.get("in_game"):
            self.body.in_game = False
            return
        self.body.in_game = True
        position = _position_of(stats)
        if position is not None:
            self.body.position = position
            self.body.position_window.append((time.monotonic(), position))
        # 朝向（M4.1：协议 1.3 起上报 yaw——unstuck 扇形基准/转头诊断数据源；
        # 旧 bridge 无此字段 → None，使用方各自回落）
        yaw = stats.get("yaw")
        self.body.yaw = float(yaw) if isinstance(yaw, (int, float)) else None
        try:
            self.body.health = float(stats.get("health", 20.0))
        except (TypeError, ValueError):
            pass
        try:
            self.body.air = int(stats.get("air", 300))
        except (TypeError, ValueError):
            pass
        # 死亡闩：事件置位 / alive=False 轮询兜底；复活（alive=True）持续超过宽限期才清
        alive = stats.get("alive")
        now = time.monotonic()
        if alive is False:
            self._dead_latch = True
            self._dead_latch_at = now
        elif alive is True and self._dead_latch and now - self._dead_latch_at > DEATH_RELATCH_GRACE:
            self._dead_latch = False
        self.body.dead = self._dead_latch
        # 眼位水：只在氧气不满时花一次 world.query（游泳常态下白查是浪费）
        self.body.eyes_in_water = False
        if position is not None and self.body.air < 300:
            self.body.eyes_in_water = await self.eyes_in_water(position)
        # 实体采样降频（每 2 轮一次）：威胁怪 + 附近玩家
        self._entity_tick += 1
        if self._entity_tick % 2 == 0:
            await self.sample_entities()

    async def eyes_in_water(self, position: tuple[float, float, float]) -> bool:
        """眼位方块是否是水（world.query blocks filter=water）。

        截断保守为 True：被水包围时 cap 截断是常态，而"眼位格不在截断结果里"
        不可信（M3.5 T5a：Java 侧截断发生在距离排序之前）。调用方已保证 air<300，
        误报的代价只是多按几下 SPACE，漏报的代价是溺死。
        """
        eye_cell = (math.floor(position[0]),
                    math.floor(position[1] + EYE_HEIGHT),
                    math.floor(position[2]))
        try:
            result = await self.client.call(
                "world.query", {"type": "blocks", "range": 3.0,
                                "filter": ["minecraft:water"]})
        except Exception as exc:  # noqa: BLE001 —— 感知失败保守不动
            logger.debug("眼位水检测失败：%s", exc)
            return False
        if not isinstance(result, dict):
            return False
        if result.get("truncated"):
            return True
        blocks = result.get("blocks")
        if not isinstance(blocks, list):
            return False
        for block in blocks:
            if isinstance(block, dict) and (
                    block.get("x"), block.get("y"), block.get("z")) == eye_cell:
                return True
        return False

    async def sample_entities(self) -> None:
        """entities 载荷（每秒一次）→ 威胁怪（category=monster 进入危险半径）+ 附近玩家。"""
        self.body.threat = None
        self.body.companion = None
        position = self.body.position
        if position is None:
            return
        try:
            result = await self.client.call(
                "world.query", {"type": "entities", "range": FLEE_SCAN_RADIUS})
        except Exception as exc:  # noqa: BLE001
            logger.debug("反射实体采样失败：%s", exc)
            return
        entities = result.get("entities") if isinstance(result, dict) else None
        if not isinstance(entities, list):
            return
        self_uuid = self._self_uuid()
        nearest_companion: tuple[float, tuple[float, float, float]] | None = None
        for entry in entities:
            if not isinstance(entry, dict):
                continue
            entity_position = _position_of(entry)
            if entity_position is None:
                continue
            uuid = str(entry.get("uuid") or "")
            if self_uuid is not None and uuid == self_uuid:
                continue  # 自己（world.query 的实体表里有本地玩家）
            distance = _dist3(position, entity_position)
            if entry.get("category") == "monster":
                try:
                    width = float(entry.get("width") or 0.6)
                except (TypeError, ValueError):
                    width = 0.6
                danger = FLEE_DANGER_BASE + width / 2.0
                if distance <= danger:
                    threat = ThreatFact(uuid=uuid, type=str(entry.get("type") or "monster"),
                                        position=entity_position, width=width,
                                        distance=distance)
                    if self.body.threat is None or distance < self.body.threat.distance:
                        self.body.threat = threat
            elif (entry.get("type") == "minecraft:player"
                  and distance <= SPEAKING_LOOK_RADIUS
                  and (nearest_companion is None or distance < nearest_companion[0])):
                nearest_companion = (distance, entity_position)
        if nearest_companion is not None:
            self.body.companion = nearest_companion[1]

    # ------------------------------------------------------------------ 反射共用工龄

    async def fresh_position(self) -> tuple[float, float, float] | None:
        """即时位置（act 里行走轮询用；body.position 最多陈旧一个轮询周期）。"""
        try:
            return _position_of(await self.client.call("getStats"))
        except Exception:  # noqa: BLE001
            return None

    async def walk_briefly(self, x: float, z: float, *, timeout: float,
                           arrive: float = 2.0) -> tuple[bool, tuple[float, float, float] | None]:
        """反射用短途行走：#goto + 轮询到位（不走 Primitives 的长超时/看门狗——
        反射的撤退不需要那套契约，且超时即收，绝不恋战）。返回 (是否到位, 末位置)。"""
        await self.client.command(f"#goto {_fmt(x)} {_fmt(z)}")
        started = time.monotonic()
        position = self.body.position
        while time.monotonic() - started < timeout:
            await asyncio.sleep(self.poll_interval)
            position = await self.fresh_position() or position
            if position is not None and math.hypot(position[0] - x, position[2] - z) <= arrive:
                return True, position
        return False, position


# ---------------------------------------------------------------------- L1 七条反射


class BreathReflex(ReflexChain):
    """换气：眼在水中且 air≤240/300 → 循环按住 SPACE 上浮（cooperative）。

    任务不停（cooperative：短暂接管按键后归还）；上浮成功即收，封顶
    （BREATH_MAX_SECONDS）仍浮不上去 → 立刻聊天上报找不到透气口。
    """

    id = "breath"
    interrupt = "cooperative"

    def can_run(self, s: BodyState) -> bool:
        # 死亡时一切自保无意义（真机教训：溺死尸体还按 SPACE 刷了 18 次简报）
        return (not s.dead and s.eyes_in_water
                and s.air <= BREATH_AIR_THRESHOLD)

    async def act(self, s: BodyState) -> None:
        # M4.1 T5：Baritone 正在走（水下 GoalBlock 之类）时先 #pause 让路，
        # 换气完 #resume 续走——消灭"浮起→再按下"拉锯；act 异常也保证归还
        if s.movement_active:
            await self.scheduler.pause_baritone()
        try:
            await self._press_until_surfaced()
        finally:
            await self.scheduler.resume_baritone()

    async def _press_until_surfaced(self) -> None:
        started = time.monotonic()
        presses = 0
        surfaced = False
        while time.monotonic() - started < BREATH_MAX_SECONDS:
            press_at = time.monotonic()
            await self.scheduler.client.call(
                "input.key", {"code": GLFW_KEY_SPACE,
                              "duration_ms": BREATH_PRESS_MS})
            presses += 1
            # 等本次按住真正结束（RELEASE 由 bridge 延迟调度）再复查——
            # 高占空比上浮，且避免下一次 PRESS 被上一次的延迟 RELEASE 掐断
            await asyncio.sleep(max(0.0, BREATH_PRESS_MS / 1000
                                    - (time.monotonic() - press_at)) + 0.05)
            # 复查氧气：vanilla 露头即回满，air==300 即视为脱困
            try:
                stats = await self.scheduler.client.call("getStats")
            except Exception:  # noqa: BLE001 —— 感知失败继续按（按错无害，漏按致命）
                continue
            if isinstance(stats, dict):
                try:
                    if int(stats.get("air", 0)) >= 300:
                        surfaced = True
                        break
                except (TypeError, ValueError):
                    continue
        if surfaced:
            self.scheduler.log(f"〔本能〕换气完成：水下按 SPACE 上浮 {presses} 次，氧气已恢复")
        else:
            self.scheduler.log(f"〔本能〕换气失败：按住上浮 {presses} 次仍未脱离水面")
            await self.scheduler.broadcast("我好像找不到透气口，还在水里——需要帮忙！")


class UnstuckReflex(ReflexChain):
    """脱困：40tick 窗口有移动输入但位移 <0.75 格 → 137° 扇形爆发 + 周期跳。

    扇形基准取当前朝向（M4.1 起协议 1.3 的 getStats.yaw；旧 bridge 无 yaw 时
    回落 0°），爆发本身仍是探索性的——转错方向也只是多试一段；跳跃是主要
    脱困手段。cooperative：不掀任务，Baritone 的寻路输入与爆发按键在真客户端
    天然共存（Baritone 卡住时它自己也在挣扎，两边的按键都不至于互相抵消）。
    """

    id = "unstuck"
    interrupt = "cooperative"

    def __init__(self, scheduler: ReflexScheduler) -> None:
        super().__init__(scheduler)
        self._cooldown_until = 0.0

    def can_run(self, s: BodyState) -> bool:
        if s.dead:
            return False  # 死亡由 DeathReflex 接管，其余自保无意义
        if time.monotonic() < self._cooldown_until:
            return False
        if not s.movement_active or s.position is None:
            return False
        now = time.monotonic()
        window = [(t, p) for t, p in s.position_window
                  if now - t <= UNSTUCK_WINDOW]
        if len(window) < 3:
            return False  # 样本不足（调度器刚起步）
        oldest_t, oldest_p = window[0]
        if now - oldest_t < UNSTUCK_WINDOW * 0.6:
            return False  # 窗口没铺满，位移判定不可信
        return _dist3(oldest_p, s.position) < UNSTUCK_MIN_DISPLACEMENT

    async def act(self, s: BodyState) -> None:
        self._cooldown_until = time.monotonic() + UNSTUCK_COOLDOWN
        position = s.position or (0.0, 0.0, 0.0)
        base_yaw = s.yaw if s.yaw is not None else 0.0
        for burst in range(1, UNSTUCK_BURSTS + 1):
            yaw = math.radians(base_yaw + UNSTUCK_TURN_DEG * burst)
            dx, dz = -math.sin(yaw), math.cos(yaw)
            await self.scheduler.client.call(
                "lookAt", {"x": position[0] + dx * 4.0,
                           "y": position[1] + 1.0,
                           "z": position[2] + dz * 4.0})
            await self.scheduler.client.call(
                "input.key", {"code": GLFW_KEY_W, "duration_ms": 600})
            await self.scheduler.client.call(
                "input.key", {"code": GLFW_KEY_SPACE, "duration_ms": 300})  # 周期跳
            await asyncio.sleep(0.6)
        self.scheduler.log(
            f"〔本能〕脱困：移动受阻超过 {UNSTUCK_WINDOW:.0f}s，"
            f"{UNSTUCK_BURSTS} 段扇形爆发 + 跳跃尝试挣脱（任务未中断）")


class FireReflex(ReflexChain):
    """撤离：CRITICAL fire → 掀任务 + #stop → 20 格找水优先，否则反向撤 5 格。

    on_fire 信号只有事件（getStats 读不到火）；act 开头清位，持续燃烧等下一条
    事件（bridge 5s 边沿冷却）。反向方向的"反向"取最近 3s 运动速度的反方向；
    没有近期运动时取 +X（简化：火源方向未知，报告注明）。
    """

    id = "fire"
    interrupt = "preempt"

    def can_run(self, s: BodyState) -> bool:
        return s.on_fire

    async def act(self, s: BodyState) -> None:
        scheduler = self.scheduler
        s.on_fire = False  # 本轮消费；再触发靠下一条 CRITICAL fire
        scheduler.preempt("fire")
        await scheduler.safe_command("#stop")
        position = s.position
        if position is None:
            scheduler.log("〔本能〕着火撤离：位置不可用，已停下任务等待火灭")
            return
        water = await self.nearest_water(position)
        if water is not None:
            arrived, final = await scheduler.walk_briefly(
                water[0] + 0.5, water[2] + 0.5, timeout=FIRE_WALK_TIMEOUT)
            note = "已到水边" if arrived else "超时未达（仍在撤离路上）"
            scheduler.log(f"〔本能〕着火撤离：跑向 ({_fmt(water[0])},{_fmt(water[2])}) "
                          f"的水，{note}")
            return
        direction = self.retreat_direction(s)
        target = (position[0] + direction[0] * FIRE_RETREAT_BLOCKS,
                  position[1],
                  position[2] + direction[1] * FIRE_RETREAT_BLOCKS)
        arrived, _ = await scheduler.walk_briefly(
            target[0], target[2], timeout=FIRE_WALK_TIMEOUT)
        scheduler.log(f"〔本能〕着火撤离：{FIRE_WATER_SEARCH_RADIUS:.0f} 格内没水，"
                      f"反向撤 {FIRE_RETREAT_BLOCKS:.0f} 格到 "
                      f"({_fmt(target[0])},{_fmt(target[2])})，"
                      f"{'到位' if arrived else '超时仍在撤离'}")

    async def nearest_water(self, position: tuple[float, float, float]) -> tuple[int, int, int] | None:
        """filtered water 查询按与玩家距离升序（T1 契约）——取最近一格的方块坐标。"""
        try:
            result = await self.scheduler.client.call(
                "world.query", {"type": "blocks",
                                "range": FIRE_WATER_SEARCH_RADIUS,
                                "filter": ["minecraft:water"]})
        except Exception as exc:  # noqa: BLE001 —— 找不到水就走反向撤退
            logger.debug("着火找水查询失败：%s", exc)
            return None
        blocks = result.get("blocks") if isinstance(result, dict) else None
        if not isinstance(blocks, list) or not blocks:
            return None
        best: tuple[float, tuple[int, int, int]] | None = None
        for block in blocks:
            if not isinstance(block, dict):
                continue
            try:
                cell = (int(block["x"]), int(block["y"]), int(block["z"]))
            except (KeyError, TypeError, ValueError):
                continue
            distance = _dist3(position, (cell[0] + 0.5, cell[1] + 0.5, cell[2] + 0.5))
            if best is None or distance < best[0]:
                best = (distance, cell)
        return best[1] if best is not None else None

    @staticmethod
    def retreat_direction(s: BodyState) -> tuple[float, float]:
        """最近 3s 运动速度的反方向（水平）；无近期运动取 +X。"""
        now = time.monotonic()
        window = [p for t, p in s.position_window if now - t <= 3.0]
        if s.position is not None and len(window) >= 2:
            vx = s.position[0] - window[0][0]
            vz = s.position[2] - window[0][2]
            speed = math.hypot(vx, vz)
            if speed > 0.5:
                return -vx / speed, -vz / speed
        return 1.0, 0.0


class HealthLowReflex(ReflexChain):
    """低血：CRITICAL health_low → 掀任务 + 聊天上报 + 〔紧急〕注入认知。

    播报走 chat.send 直发通道（M4.1）：T6 压测实证"低血警报发出前毫秒级死亡、
    死亡屏占 GUI 吞掉 T 键路径"的窗口期竞争——警报这类关键上行不该依赖
    GUI 状态。
    """

    id = "health_low"
    interrupt = "preempt"

    def __init__(self, scheduler: ReflexScheduler) -> None:
        super().__init__(scheduler)
        self._cooldown_until = 0.0

    def can_run(self, s: BodyState) -> bool:
        return s.health_low and time.monotonic() >= self._cooldown_until

    async def act(self, s: BodyState) -> None:
        scheduler = self.scheduler
        s.health_low = False
        self._cooldown_until = time.monotonic() + HEALTH_LOW_COOLDOWN
        scheduler.preempt("health_low")
        await scheduler.safe_command("#stop")
        health = s.health
        await scheduler.broadcast_direct(f"警报：我的血量只剩 {health:g} 了，已停下手上的事")
        scheduler.urgent(
            f"健康告警：生命值 {health:g}（阈值 6）。当前任务已被自保反射中止。"
            f"评估处境（周围有什么威胁、要不要继续撤退、能不能吃东西回血）后再行动。")
        scheduler.log(f"〔本能〕低血停任务：health={health:g}，已聊天上报")


class DeathReflex(ReflexChain):
    """死亡：CRITICAL death → 掀任务 + 聊天上报死亡坐标 + 〔紧急〕注入，等玩家指令。

    不自动重生（裁决）；reported 闩保证每次死亡只报一次，复活（getStats alive
    转真且过宽限期）自动重臂。
    """

    id = "death"
    interrupt = "preempt"

    def __init__(self, scheduler: ReflexScheduler) -> None:
        super().__init__(scheduler)
        self._reported = False

    def can_run(self, s: BodyState) -> bool:
        if s.dead:
            return not self._reported
        self._reported = False  # 活着 → 重臂（下一次死亡的边沿）
        return False

    async def act(self, s: BodyState) -> None:
        self._reported = True
        scheduler = self.scheduler
        position = s.position or (0.0, 0.0, 0.0)
        scheduler.preempt("death")
        x, y, z = position
        # M4.1 T3：死亡屏打开时 T 键唤不起聊天框（M4-rerun §3.3 实证：wire 已发、
        # 游戏聊天无此行）——播报走 chat.send 直发通道（GUI 屏蔽免疫）
        await scheduler.broadcast_direct(
            f"我死了……死亡位置约 ({x:.0f},{y:.0f},{z:.0f})。等你指示，我不会自动重生。")
        scheduler.urgent(
            f"你刚刚死亡，位置约 ({x:.0f},{y:.0f},{z:.0f})。任务已被终止；"
            f"等待玩家指示后再行动，不要自动重生。")
        scheduler.log(f"〔本能〕死亡上报：位置约 ({x:.0f},{y:.0f},{z:.0f})，等待玩家指令")


class FleeReflex(ReflexChain):
    """危怪逃离：12 格内敌对怪进入危险半径（width/2+1.5）→ 掀任务 + 反向走 8 格。

    敌对判定=category=="monster"（注册表数据，模组怪自动归队）；**不还击**
    （与"禁止攻击"安全约束一致）。危险半径是 Numen Menace 的简化：不查怪物
    attribute/实际攻击距离，按碰撞箱宽近似（远程怪会偏保守，报告注明）。
    """

    id = "flee"
    interrupt = "preempt"

    def __init__(self, scheduler: ReflexScheduler) -> None:
        super().__init__(scheduler)
        self._cooldown_until = 0.0

    def can_run(self, s: BodyState) -> bool:
        return (not s.dead and s.threat is not None
                and time.monotonic() >= self._cooldown_until)

    async def act(self, s: BodyState) -> None:
        scheduler = self.scheduler
        threat = s.threat
        s.threat = None
        self._cooldown_until = time.monotonic() + FLEE_COOLDOWN
        position = s.position
        if threat is None or position is None:
            return
        scheduler.preempt("flee")
        await scheduler.safe_command("#stop")
        # 反方向 8 格（水平）
        dx = position[0] - threat.position[0]
        dz = position[2] - threat.position[2]
        horizontal = math.hypot(dx, dz) or 1.0
        tx = position[0] + dx / horizontal * FLEE_ESCAPE_BLOCKS
        tz = position[2] + dz / horizontal * FLEE_ESCAPE_BLOCKS
        arrived, _ = await scheduler.walk_briefly(tx, tz, timeout=FLEE_WALK_TIMEOUT)
        scheduler.log(
            f"〔本能〕逃离 {threat.type}（{threat.distance:.1f} 格进入危险半径 "
            f"{threat.danger_radius:.1f}）：向 ({_fmt(tx)},{_fmt(tz)}) 撤离 "
            f"{'到位' if arrived else '超时仍在撤离'}，未还击")


class SpeakingLookReflex(ReflexChain):
    """注视：正在播报（LoopClient.command 发出聊天后 2.5s 窗口）且 16 格内有玩家
    → lookAt 看向对方头部（none：纯旁路，不碰任务也不碰按键）。

    "主人"近似为 16 格内最近的其他玩家；平滑转头由 bridge 侧 lookAt 自带
    （TurnController 限速）。不写 behavior_log（每次播报都转头是常态，记了全是噪声）。
    """

    id = "speaking_look"
    interrupt = "none"

    def __init__(self, scheduler: ReflexScheduler) -> None:
        super().__init__(scheduler)
        self._last_look_at = 0.0

    def can_run(self, s: BodyState) -> bool:
        return (not s.dead
                and time.monotonic() < self.scheduler._speaking_until
                and s.companion is not None
                and time.monotonic() - self._last_look_at >= SPEAKING_LOOK_MIN_INTERVAL)

    async def act(self, s: BodyState) -> None:
        companion = s.companion
        if companion is None:
            return
        self._last_look_at = time.monotonic()
        await self.scheduler.client.call(
            "lookAt", {"x": companion[0], "y": companion[1] + 1.6, "z": companion[2]})


__all__ = [
    "LEVEL_LABELS",
    "LEVEL_SWITCH_WORDS",
    "REFLEX_LEVEL_PREFIX",
    "BodyState",
    "BreathReflex",
    "DeathReflex",
    "FireReflex",
    "FleeReflex",
    "HealthLowReflex",
    "InterruptKind",
    "ReflexChain",
    "ReflexLevel",
    "ReflexScheduler",
    "SpeakingLookReflex",
    "ThreatFact",
    "UnstuckReflex",
    "instincts_section",
    "match_reflex_level_command",
    "parse_reflex_level",
    # 常量（测试 monkeypatch 用）
    "BREATH_AIR_THRESHOLD",
    "BREATH_MAX_SECONDS",
    "BREATH_PRESS_MS",
    "DEATH_RELATCH_GRACE",
    "EYE_HEIGHT",
    "FIRE_RETREAT_BLOCKS",
    "FIRE_WALK_TIMEOUT",
    "FIRE_WATER_SEARCH_RADIUS",
    "FLEE_COOLDOWN",
    "FLEE_DANGER_BASE",
    "FLEE_ESCAPE_BLOCKS",
    "FLEE_SCAN_RADIUS",
    "FLEE_WALK_TIMEOUT",
    "GLFW_KEY_SPACE",
    "GLFW_KEY_W",
    "HEALTH_LOW_COOLDOWN",
    "SPEAKING_LOOK_MIN_INTERVAL",
    "SPEAKING_LOOK_RADIUS",
    "SPEAKING_WINDOW_SECONDS",
    "UNSTUCK_BURSTS",
    "UNSTUCK_COOLDOWN",
    "UNSTUCK_MIN_DISPLACEMENT",
    "UNSTUCK_TURN_DEG",
    "UNSTUCK_WINDOW",
]
