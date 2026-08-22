"""M3-B 最小大脑循环：玩家在游戏聊天打字 → bot 感知 → VLM 决策 → 工具执行 → 游戏内回话。

把 M3-A 的 QwenVLM 与 M0-M2 的 BridgeClient 接成自主闭环（spec §10.1 M3-B）：

- **指令入口**：``run()`` 订阅 chat 事件；其他玩家的非系统消息进任务队列串行执行。
  自回显双重过滤：(a) ``command()`` 发送时登记 (文本, 时间戳) 抑制窗（默认 5s）内
  相同文本忽略；(b) 启动时 getStats 位置 ↔ world.query 实体位置匹配自识别自身 uuid，
  sender == 自身 uuid 的消息直接忽略
- **急停**：任意非自身 chat 消息为 "停下"/"stop" → 立即置急停标志并回话"好的，
  停下了"；当前任务在下一个检查点（VLM 调用前 / 每个工具执行前）终止——不打断
  在途的 VLM HTTP 调用，也不丢弃已收到的观测
- **任务循环**：系统提示（身份/工具说明/安全约束/当前任务）+ 初始观测
  （getStats+getGuiState）→ 循环 ``asyncio.to_thread(vlm.chat, ...)`` → 逐个执行
  tool_calls → 工具结果（tool role）回填 → 直至 finish 调用 / 纯文本回复 /
  max_steps / token 预算 / 急停
- **结束播报**：finish(result) 与纯文本回复 → ``client.command(result)`` 游戏内播报；
  max_steps/token 预算用尽 → 播报 "这个任务我先到这：（进度摘要）"；急停已由
  chat handler 回话，不再重复
- **上下文管理**：消息历史按任务隔离；单条工具结果文本超 4000 字符截断（保留
  头尾）；截图图像仅保留最近 1 张（新截图到来时旧 user 消息里的 image_url 段
  被裁掉，防上下文爆炸）；M3.5 滚动状态——每步 VLM 调用前替换式注入一条
  〔当前状态〕user 消息（getStats 单行摘要，历史里恒至多一条，Numen 免费搭车做法）
- 全程结构化日志（步号/工具/耗时/tokens），每步一条 INFO
- **M4 反射层**：``scheduler``（ReflexScheduler）随 run() 并行启动——0.5s 轮询
  七条脊髓反射（换气/脱困/撤离/低血/死亡/危怪逃离/注视），preempt 档经
  ``request_preempt(reason)`` 掀翻当前任务（end_reason=preempt，不播报）；
  反射简报/危险感知在每轮 VLM 调用前以〔本能反应〕消息注入，death/低血经
  ``inject_urgent`` 排队成〔紧急〕消息；聊天命令"反射等级 观察/自保"人类-only
  切换等级（instincts 系统提示节随任务重新生成）；等级切换只改内存不落盘
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from sirius_brain.bridge.client import BridgeClient, BridgeError
from sirius_brain.protocol import NotificationFrame

from .config import AgentConfig
from .reflexes import (
    LEVEL_LABELS,
    ReflexLevel,
    ReflexScheduler,
    instincts_section,
    match_reflex_level_command,
    parse_reflex_level,
)
from .tools import (
    FINISH_TOOL,
    COLLECT_BLOCK_TOOL,
    WALK_TO_TOOL,
    UnknownToolError,
    ToolOutcome,
    ToolRegistry,
    compact_json,
    default_registry,
    validation_error_text,
)
from .vlm import QwenVLM, VLMError, VLMResponse, ToolCall, system_message, tool_result_message, user_message

logger = logging.getLogger(__name__)

# 急停词（strip 后比对；ASCII 词大小写不敏感）
STOP_WORDS = ("停下", "stop")
# 急停回话文本（经 command() 发送并登记抑制）
STOP_REPLY_TEXT = "好的，停下了"
# max_steps/token 预算用尽的播报前缀（后接进度摘要）
PARTIAL_DONE_PREFIX = "这个任务我先到这："
# Minecraft 系统消息的 NIL UUID（Util.NIL_UUID；视同"无发送者身份"）
NIL_UUID = "00000000-0000-0000-0000-000000000000"
# 单条工具结果回填的最大字符数（超长截断，保留头尾）
MAX_TOOL_RESULT_CHARS = 4000
# 默认自回显抑制窗（秒）
DEFAULT_ECHO_WINDOW = 5.0
# 自识别时实体位置与 getStats 位置的容差（格）——两次调用间自身可小范围移动
SELF_MATCH_TOLERANCE = 2.0
# 保留的最近任务运行记录数
MAX_RUN_HISTORY = 100
# 滚动状态消息的固定前缀（替换式注入的识别标记；内容形如
# "〔当前状态〕位置(100.5,64.0,-200.5) 生命20 饥饿20 氧气300"）
STATUS_PREFIX = "〔当前状态〕"
# 反射事后知会消息的固定前缀（M4，替换式注入，内容为 behavior_log 尾部）
REFLEX_NOTICE_PREFIX = "〔本能反应〕"
# 紧急消息前缀（death/低血经 urgent 通道注入，drain 点在每轮 VLM 调用前）
URGENT_PREFIX = "〔紧急〕"
# 反射知会 flush 的尾部上限（字符）——纯简报不是记忆，只保最近
REFLEX_NOTICE_TAIL_CHARS = 500

# 任务结束原因（TaskRun.end_reason 取值）
END_FINISH = "finish"            # 模型调用了 finish
END_CONTENT = "content"          # 模型直接给纯文本回复（等价 finish）
END_STOP = "stop"                # 急停
END_PREEMPT = "preempt"          # M4：被反射抢占（fire/flee/health_low/death）
END_MAX_STEPS = "max_steps"      # 步数预算用尽
END_BUDGET = "budget"            # token 预算用尽
END_ERROR = "error"              # VLM 调用失败等不可恢复错误


def bridge_error_hint(code: int) -> str:
    """BridgeError code → 追加给模型的建议动作（读文本可自救，M3.5 T3）。

    语义对照 bridge 侧 Json.java 的错误码：-32602 参数不合法 / -32010 输入限频
    （InputGuard 20/s）/ -32011 输入通道关闭 / -32012 权限分级拒绝。
    """
    if code == -32602:
        return "（注意参数边界：world.query range≤64、filter 1..16 条）"
    if code == -32010:
        return "（输入限频（20/s），稍等再试）"
    if code == -32011:
        return "（输入通道已被关闭（input_enabled=false），需要玩家在 bridge 侧重新开启）"
    if code == -32012:
        return "（该操作超出当前权限分级被拒，换用被允许的操作）"
    return ""


# ---------------------------------------------------------------------- 自回显过滤


class SelfEchoFilter:
    """自回显过滤：抑制窗（同文本近时距）+ 自身 uuid 双重判定。

    - ``sender == self_uuid`` → 一定是自己的话（bridge 把玩家行都带 uuid，M2-B）
    - sender 缺省/未知（NIL/None）且文本与近期 command() 发送的一致（窗口内）→
      视为自己的回显（uuid 识别失败时的兜底）
    - sender 是已知他人 → 不是回显（哪怕文本恰好相同——玩家复读也是新指令）
    时钟可注入（测试确定性用），默认 time.monotonic。
    """

    def __init__(self, window: float = DEFAULT_ECHO_WINDOW,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.window = window
        self.clock = clock
        self._recent: list[tuple[str, float]] = []

    def register(self, text: str) -> None:
        """登记一条自己发出的文本（command() 发送时调用）。"""
        now = self.clock()
        self._recent = [(t, ts) for t, ts in self._recent if now - ts <= self.window]
        self._recent.append((text, now))

    def is_echo(self, text: str, sender: str | None,
                self_uuid: str | None) -> bool:
        """判定一条 chat 消息是否为自己的回显。"""
        if self_uuid is not None and sender == self_uuid:
            return True
        if sender is not None and sender != self_uuid:
            return False
        # sender 未知 → 靠抑制窗
        now = self.clock()
        return any(t == text and now - ts <= self.window for t, ts in self._recent)


def match_self_uuid(stats_result: Any, entities_result: Any,
                    tolerance: float = SELF_MATCH_TOLERANCE) -> str | None:
    """从 getStats + world.query(entities) 结果推断自身 uuid（纯函数，可单测）。

    原理：getStats 无 uuid 但有玩家自身坐标；实体列表里与自己坐标距离最小的
    那个实体（且 < tolerance 格）就是自己。识别不出返回 None（抑制窗兜底）。
    """
    if not isinstance(stats_result, dict) or not stats_result.get("in_game"):
        return None
    stats_pos = stats_result.get("position")
    if not isinstance(stats_pos, dict):
        return None
    if not isinstance(entities_result, dict):
        return None
    entities = entities_result.get("entities")
    if not isinstance(entities, list):
        return None

    def _dist(pos: Any) -> float | None:
        if not isinstance(pos, dict):
            return None
        try:
            return ((float(pos["x"]) - float(stats_pos["x"])) ** 2
                    + (float(pos["y"]) - float(stats_pos["y"])) ** 2
                    + (float(pos["z"]) - float(stats_pos["z"])) ** 2) ** 0.5
        except (KeyError, TypeError, ValueError):
            return None

    best_uuid: str | None = None
    best_dist = float("inf")
    for entity in entities:
        if not isinstance(entity, dict) or not entity.get("uuid"):
            continue
        dist = _dist(entity.get("position"))
        if dist is not None and dist < best_dist:
            best_dist = dist
            best_uuid = str(entity["uuid"])
    return best_uuid if best_dist <= tolerance else None


# ---------------------------------------------------------------------- 结果类型


@dataclass
class ToolExec:
    """一次工具执行的记录（TaskRun.tool_calls 成员）。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    text: str = ""
    elapsed: float = 0.0


@dataclass
class TaskRun:
    """单次任务的执行记录（run_task 的返回值；last_run / runs 里留档）。"""

    instruction: str
    steps: int = 0                      # VLM 调用次数
    tool_calls: list[ToolExec] = field(default_factory=list)
    tokens: int = 0                     # 各步 usage.total_tokens 累计
    end_reason: str = END_ERROR         # finish/content/stop/max_steps/budget/error
    result: str = ""                    # finish/content 的结束语
    error: str | None = None
    elapsed: float = 0.0

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tool_calls]


# ---------------------------------------------------------------------- 循环


class LoopClient:
    """工具 handler / 播报路径上的 bridge 客户端包装。

    - ``command()``：发送前把文本登记进自回显抑制窗（自己的话回来不再当指令）；
      全部 command 调用经循环级锁串行（急停回话 / finish 播报 / command 工具 /
      M4 反射的 #stop、#goto 不并发交错 T→text→ENTER 序列）；**发出一条聊天
      （非 / # 开头）后打开注视窗口**——speaking_look 反射据此转头看主人
    - ``say()``（M4.1 T3）：直发聊天——bridge 的 ``chat.send`` 通道（进程内
      ClientPacketListener.sendChat，绕开 T 键 GUI）。死亡屏打开时 T 键唤不起
      聊天框，死亡播报走这里；旧 bridge 无此工具（-32601）自动回落 command()
      的 GUI 路径。同样登记自回显抑制窗、持同一把循环级锁
    - 其余属性/方法原样委托给真实 BridgeClient（call/subscribe_events/...）
    """

    def __init__(self, client: BridgeClient, loop: "AgentLoop") -> None:
        self._client = client
        self._loop = loop

    async def command(self, text: str, settle: float | None = None,
                      timeout: float | None = None) -> Any:
        async with self._loop._command_lock:
            self._loop.echo.register(text)
            effective_settle = self._loop.command_settle if settle is None else settle
            result = await self._client.command(text, settle=effective_settle,
                                                timeout=timeout)
        if not text.startswith(("/", "#")):
            self._loop.scheduler.note_speaking()
        return result

    async def say(self, text: str, timeout: float | None = None) -> Any:
        """直发一条聊天（chat.send，绕开 T 键 GUI——死亡屏等 GUI 屏蔽场景）。

        与 ``command`` 同样的副作用纪律：登记自回显抑制窗（chat 事件回来
        sender 未知时不当成新指令）、非 / # 开头打开注视窗口、持循环级锁
        （绝不与在途 command 序列交错）。旧 bridge 无 chat.send（-32601）→
        回落 GUI 路径（等价旧行为）。
        """
        async with self._loop._command_lock:
            self._loop.echo.register(text)
            try:
                result = await self._client.call("chat.send",
                                                 {"string": text}, timeout)
            except BridgeError as exc:
                if exc.code != -32601:
                    raise
                logger.info("chat.send 不可用（旧 bridge），回落 GUI 路径：%r",
                            text[:60])
                result = await self._client.command(
                    text, settle=self._loop.command_settle, timeout=timeout)
        if not text.startswith(("/", "#")):
            self._loop.scheduler.note_speaking()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class AgentLoop:
    """最小整机大脑循环。

    用法（CLI 见 ``python -m sirius_brain.agent``）::

        loop = AgentLoop(client, vlm, config, persona="天狼星")
        loop.install()                  # 注册 chat handler（最好在 connect 前）
        await client.connect()
        await loop.run()                # 常驻：订阅 chat → 串行执行任务队列

    也可不经 chat 直接驱动单任务（测试/调试）：``run = await loop.run_task("指令")``。
    """

    def __init__(
        self,
        client: BridgeClient,
        vlm: QwenVLM,
        config: AgentConfig,
        persona: str = "",
        *,
        registry: ToolRegistry | None = None,
        self_echo_window: float = DEFAULT_ECHO_WINDOW,
        command_settle: float = 0.5,
        log_level: int | str | None = None,
    ) -> None:
        self.client = client
        self.vlm = vlm
        self.config = config
        self.persona = persona
        # M3.5：默认注册表带任务级原语（walkTo/digBlock/collectBlock），cancel 闭包把
        # 循环急停态接进原语微步循环（registry.execute 签名无 loop 引用，构造期注入）；
        # 显式传 registry 时不接（调用方自负责原语的取消绑定）
        self.registry = registry if registry is not None else default_registry(
            cancel=lambda: self._stop_requested)
        self.command_settle = command_settle
        # 工具 handler 与播报走的包装客户端（command 拦截 + 串行）
        self.tools_client = LoopClient(client, self)

        self.echo = SelfEchoFilter(window=self_echo_window)
        self.self_uuid: str | None = None
        self._identify_attempted = False

        self.last_run: TaskRun | None = None
        self.runs: list[TaskRun] = []

        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._enqueue_seq = 0
        self._stop_seq = 0            # 每次"停下"自增；早于它的排队任务被丢弃
        self._stop_requested = False
        self._preempt_reason: str | None = None  # M4：非空 = 反射抢占（非玩家急停）
        self._running = False
        self._command_lock = asyncio.Lock()
        self._background: set[asyncio.Task] = set()
        self._chat_unregister: Callable[[], None] | None = None
        self._reflex_unregisters: list[Callable[[], None]] = []
        # M4 紧急消息（death/低血）：排队到下一次 VLM 调用前 drain——不直接改
        # 活动任务的 messages（vlm.chat 在线程里遍历它，并发 append 会竞态）
        self._urgent_pending: list[str] = []

        # M4 反射层：调度器挂在 loop 上（preempt 经 request_preempt 扩展急停
        # 语义；urgent 经 inject_urgent 排队；等级来自 LoopConfig，切换命令只改
        # 内存——重启回默认，不做持久化）
        self.scheduler = ReflexScheduler(
            self.tools_client,
            level=parse_reflex_level(config.loop.reflex_level),
            poll_interval=config.loop.reflex_poll_interval,
            preempt=self.request_preempt,
            urgent=self.inject_urgent,
            self_uuid=lambda: self.self_uuid,
            broadcast=self._broadcast,
            broadcast_direct=self._broadcast_direct,
        )
        self.scheduler.install_default_chains()

        if log_level is not None:
            logging.getLogger("sirius_brain.agent").setLevel(log_level)

    # ------------------------------------------------------------------ 事件装配

    def install(self) -> None:
        """注册 chat + CRITICAL danger 事件 handler（重复调用幂等）。建议在 connect() 之前。"""
        if self._chat_unregister is None:
            self._chat_unregister = self.client.add_event_handler("chat", self._on_chat)
        if not self._reflex_unregisters:
            for event in ReflexScheduler.DANGER_EVENTS:
                self._reflex_unregisters.append(
                    self.client.add_event_handler(
                        event, self.scheduler.danger_handler(event)))

    def _on_chat(self, frame: NotificationFrame) -> None:
        """chat 事件入口（在 bridge 接收循环里同步调用；只做过滤+入队/置标志）。"""
        data = frame.data or {}
        if data.get("system"):
            return  # 系统行（/say、死亡消息等）不当指令
        message = str(data.get("message") or "").strip()
        if not message:
            return
        sender = data.get("sender") or None
        if sender == NIL_UUID:
            sender = None
        if self.echo.is_echo(message, sender, self.self_uuid):
            logger.debug("忽略自身回显：%r", message)
            return
        # M4 反射等级切换（人类-only，绝不进 VLM 工具表）：识别即切换 + 播报确认
        target_level = match_reflex_level_command(message)
        if target_level is not None:
            logger.info("收到反射等级切换指令（sender=%s）：%s → %s",
                        sender, self.scheduler.level.value, target_level.value)
            self._spawn(self._apply_reflex_level(target_level))
            return
        normalized = message.lower()
        if message in STOP_WORDS or normalized in STOP_WORDS:
            logger.info("收到急停指令（sender=%s）", sender)
            self.request_stop()
            self._spawn(self._broadcast(STOP_REPLY_TEXT))
            return
        self._enqueue_seq += 1
        self._queue.put_nowait((message, self._enqueue_seq))
        logger.info("新任务入队 #%d（sender=%s）：%r", self._enqueue_seq, sender, message)

    async def _apply_reflex_level(self, level: ReflexLevel) -> None:
        """反射等级切换：改内存 + 播报确认（instincts 从下个任务起生效；不持久化）。"""
        if level is ReflexLevel.GUARD:
            await self._broadcast(
                f"自卫等级（L2）还是预留位，没有实现——当前仍是 "
                f"{LEVEL_LABELS[self.scheduler.level]}，反射等级维持不变")
            return
        old = self.scheduler.level
        if level is old:
            await self._broadcast(f"反射等级已经是 {LEVEL_LABELS[level]} 了")
            return
        self.scheduler.level = level
        await self._broadcast(
            f"反射等级已切换：{LEVEL_LABELS[old]} → {LEVEL_LABELS[level]}"
            f"（本能说明将随下一个任务生效）")

    def _spawn(self, coro) -> asyncio.Task:
        """后台任务（急停回话等）：登记防 GC，结束自动摘除。"""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    def request_stop(self) -> None:
        """急停：当前任务在下一检查点终止；早于本次停止序号的排队任务将被丢弃。"""
        self._stop_seq += 1
        self._stop_requested = True
        self._preempt_reason = None

    def request_preempt(self, reason: str) -> None:
        """M4 反射抢占：与急停同语义（检查点终止 + 排队任务丢弃），但 end_reason
        记 ``preempt``、不播报"好的，停下了"（反射路径自己上报），也不复用急停的
        STOP_WORDS 回话。"""
        self._stop_seq += 1
        self._stop_requested = True
        self._preempt_reason = reason
        logger.info("反射抢占请求（%s）：当前任务将在下一检查点终止", reason)

    def inject_urgent(self, text: str) -> None:
        """〔紧急〕消息排队（death/低血）：下一次 VLM 调用前 drain 进活动任务的
        历史；任务已终止则留给下一个任务（开头即 drain）。线程安全：只操作
        pending 列表，不碰活动 messages（vlm.chat 在工作线程里遍历它）。"""
        self._urgent_pending.append(text)
        logger.info("紧急消息排队（%d 条待注入）：%s", len(self._urgent_pending), text)

    # ------------------------------------------------------------------ 主入口

    async def run(self) -> None:
        """常驻主入口：识别自身 → 订阅事件 → 串行消费任务队列 + 反射调度协程。"""
        self.install()
        if not self._identify_attempted:
            await self.identify_self()
        # 订阅必须一次带全（bridge 侧单订阅槽，后一次 subscribe 会替换前一次）；
        # 不带 min_level——CRITICAL 过滤会把 INFO 的 chat 滤掉，而 danger 四事件
        # 本身就是 CRITICAL-only，类型即判据
        try:
            await self.client.subscribe_events(
                ["chat", *ReflexScheduler.DANGER_EVENTS])
        except Exception as exc:  # noqa: BLE001 —— 订阅失败要让调用方看见
            logger.error("订阅事件失败（chat + danger）：%s", exc)
            raise
        logger.info("AgentLoop 就绪：自身 uuid=%s，工具表=%s，max_steps=%d，"
                    "token 预算=%d，急停词=%s，反射等级=%s",
                    self.self_uuid or "未知（抑制窗兜底）",
                    ",".join(self.registry.names()),
                    self.config.loop.max_steps,
                    self.config.loop.max_total_tokens, "/".join(STOP_WORDS),
                    self.scheduler.level.value)
        self._running = True
        self._spawn(self.scheduler.run())  # M4：反射调度与任务消费并行
        try:
            while self._running:
                instruction, seq = await self._queue.get()
                if seq < self._stop_seq:
                    logger.info("任务 #%d 已被急停波及，丢弃：%r", seq, instruction)
                    continue
                self._stop_requested = False  # 新任务不继承陈旧急停标志
                self._preempt_reason = None
                while self.scheduler.busy:  # 反射执行中不开新任务（先归位再做事）
                    await asyncio.sleep(0.1)
                try:
                    await self.run_task(instruction, seq=seq)
                except Exception:  # noqa: BLE001 —— 单任务异常不杀常驻循环
                    logger.exception("任务执行异常（instruction=%r）", instruction)
        finally:
            self._running = False

    async def shutdown(self) -> None:
        """停止常驻循环并回收后台任务（不关闭 bridge 连接）。"""
        self._running = False
        self._stop_requested = True
        self.scheduler.stop()
        for task in list(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        for unregister in self._reflex_unregisters:
            unregister()
        self._reflex_unregisters = []
        if self._chat_unregister is not None:
            self._chat_unregister()
            self._chat_unregister = None

    # ------------------------------------------------------------------ 自识别

    async def identify_self(self, range_blocks: float = 16.0) -> str | None:
        """getStats 坐标 ↔ world.query(entities) 最近实体匹配自身 uuid（best-effort）。

        只尝试一次（无论成败）——run() 不会再重复识别。
        """
        self._identify_attempted = True
        try:
            stats = await self.client.call("getStats")
            entities = await self.client.call(
                "world.query", {"type": "entities", "range": range_blocks})
        except Exception as exc:  # noqa: BLE001 —— 识别失败不阻塞启动（抑制窗兜底）
            logger.warning("自识别失败（抑制窗兜底）：%s", exc)
            return None
        self.self_uuid = match_self_uuid(stats, entities)
        logger.info("自身 uuid 识别：%s", self.self_uuid or "未识别（抑制窗兜底）")
        return self.self_uuid

    # ------------------------------------------------------------------ 单任务

    async def run_task(self, instruction: str, *, seq: int | None = None) -> TaskRun:
        """执行一个任务到 finish/预算尽/急停/被反射抢占（chat 队列与直接驱动共用此路径）。"""
        if seq is None:
            self._stop_requested = False  # 直接驱动视为全新任务
            self._preempt_reason = None
        run = TaskRun(instruction=instruction)
        loop_cfg = self.config.loop
        started = time.perf_counter()
        messages: list[dict[str, Any]] = [system_message(self._system_prompt(instruction))]
        observation = await self._initial_observation()
        messages.append(user_message(
            f"初始观测：\n{observation}" if observation else "初始观测不可用，请先用工具观察。"))
        self._drain_urgent(messages)  # 上一任务遗留的〔紧急〕先送达
        last_call_at: float | None = None

        try:
            for step in range(1, loop_cfg.max_steps + 1):
                if self._stop_requested:
                    self._end_for_stop(run)
                    break
                # min_interval：连续 VLM 调用之间的最小间隔
                if last_call_at is not None and loop_cfg.min_interval > 0:
                    await asyncio.sleep(max(0.0, loop_cfg.min_interval
                                            - (time.monotonic() - last_call_at)))
                # M3.5 滚动状态免费搭车（Numen runtime_state 做法）：每步调用前替换式
                # 注入 getStats 摘要——模型白拿最新自身状态，省掉专门的观察调用；
                # M4 反射知会（〔本能反应〕behavior_log 尾部）与〔紧急〕消息同点注入
                await self._inject_reflex_notices(messages)
                self._drain_urgent(messages)
                await self._inject_rolling_status(messages)
                response = await asyncio.to_thread(
                    self.vlm.chat, messages, tools=self.registry.openai_tools())
                last_call_at = time.monotonic()
                run.steps = step
                run.tokens += response.usage.total_tokens
                logger.info("任务 %r 第 %d/%d 步：VLM 返回 %d 个 tool_calls，"
                            "content=%r，累计 tokens=%d",
                            instruction[:32], step, loop_cfg.max_steps,
                            len(response.tool_calls), response.content[:80],
                            run.tokens)
                messages.append(self._assistant_message(response))

                if not response.has_tool_calls:
                    if response.content.strip():
                        # 纯文本回复视作结束（等价 finish，语义与 OpenAI stop 一致）
                        run.end_reason = END_CONTENT
                        run.result = response.content.strip()
                        break
                    messages.append(user_message(
                        "（请继续用工具执行任务，或调用 finish 结束。）"))
                    continue

                finished = False
                for call in response.tool_calls:
                    if self._stop_requested:
                        self._end_for_stop(run)
                        finished = True
                        break
                    outcome = await self._execute_tool(call, run)
                    messages.append(
                        tool_result_message(call.id, self._truncate(outcome.text)))
                    if outcome.image is not None:
                        self._prune_old_images(messages)
                        messages.append(user_message(
                            "（上一条 screenshot 的画面图像见本消息）",
                            images=[outcome.image]))
                    if call.name == FINISH_TOOL:
                        run.end_reason = END_FINISH
                        run.result = outcome.text
                        finished = True
                        break
                if finished:
                    break
                if run.tokens > loop_cfg.max_total_tokens:
                    run.end_reason = END_BUDGET
                    logger.warning("任务 %r token 预算用尽（%d > %d）",
                                   instruction[:32], run.tokens,
                                   loop_cfg.max_total_tokens)
                    break
            else:
                run.end_reason = END_MAX_STEPS
                logger.warning("任务 %r max_steps 用尽（%d 步）",
                               instruction[:32], loop_cfg.max_steps)
        except VLMError as exc:
            run.end_reason = END_ERROR
            run.error = f"VLM 调用失败：{exc}"
            logger.error("任务 %r 终止：%s", instruction[:32], run.error)
        finally:
            run.elapsed = time.perf_counter() - started
            self.last_run = run
            self.runs.append(run)
            if len(self.runs) > MAX_RUN_HISTORY:
                self.runs = self.runs[-MAX_RUN_HISTORY:]
            logger.info("任务结束 %r：原因=%s 步数=%d 工具=%s tokens=%d 耗时=%.2fs",
                        instruction[:32], run.end_reason, run.steps,
                        run.tool_names, run.tokens, run.elapsed)

        await self._finalize(run)
        return run

    def _end_for_stop(self, run: TaskRun) -> None:
        """检查点上的终止定性：玩家急停（stop）还是反射抢占（preempt）。"""
        if self._preempt_reason:
            run.end_reason = END_PREEMPT
            run.result = f"任务被反射 {self._preempt_reason} 抢占"
        else:
            run.end_reason = END_STOP

    async def _finalize(self, run: TaskRun) -> None:
        """任务结束播报（经 command()，自回显登记）。"""
        if run.end_reason in (END_STOP, END_PREEMPT):
            return  # 急停回话已由 chat handler 发出；抢占的上报由反射路径自己发
        if run.end_reason in (END_FINISH, END_CONTENT):
            if run.result.strip():
                await self._broadcast(run.result)
            return
        # max_steps / budget / error：进度摘要播报
        summary = self._progress_summary(run)
        await self._broadcast(f"{PARTIAL_DONE_PREFIX}{summary}")

    async def _broadcast(self, text: str) -> None:
        """游戏内播报一条文本（失败只记日志——播报失败不应杀循环）。"""
        try:
            await self.tools_client.command(text)
            logger.info("已播报：%r", text[:120])
        except Exception as exc:  # noqa: BLE001
            logger.error("播报失败（%r）：%s", text[:60], exc)

    async def _broadcast_direct(self, text: str) -> None:
        """直发播报（M4.1 T3）：chat.send 绕开 T 键 GUI——死亡屏屏蔽 T 键的
        播报通道（M4-rerun §3.3 实证）。失败只记日志（反射 act 不被打断）。"""
        try:
            await self.tools_client.say(text)
            logger.info("已直发播报：%r", text[:120])
        except Exception as exc:  # noqa: BLE001
            logger.error("直发播报失败（%r）：%s", text[:60], exc)

    # ------------------------------------------------------------------ 工具执行

    async def _execute_tool(self, call: ToolCall, run: TaskRun) -> ToolOutcome:
        """执行单个工具调用：一切失败都翻译成文本回填（模型可自救），不杀任务。"""
        started = time.perf_counter()
        ok = True
        # M4：行走类原语执行期间置"有移动输入"（脱困反射的触发前提）——
        # Baritone 在走 = 我们在打 W，卡住 2 秒就该挣扎了
        moving = call.name in (WALK_TO_TOOL, COLLECT_BLOCK_TOOL)
        if moving:
            self.scheduler.note_movement(True)
        try:
            outcome = await self.registry.execute(
                self.tools_client, call.name, call.arguments)
        except UnknownToolError:
            outcome = ToolOutcome(
                f"未知工具 {call.name!r}：不在白名单内。可用工具："
                f"{','.join(self.registry.names())}")
            ok = False
        except ValidationError as exc:
            outcome = ToolOutcome(validation_error_text(call.name, exc))
            ok = False
        except BridgeError as exc:
            outcome = ToolOutcome(
                f"工具 {call.name} 被身体拒绝 [{exc.code}]：{exc.message}"
                f"{bridge_error_hint(exc.code)}")
            ok = False
        except TimeoutError as exc:
            outcome = ToolOutcome(f"工具 {call.name} 超时：{exc}")
            ok = False
        except Exception as exc:  # noqa: BLE001 —— 单工具异常不杀任务
            logger.exception("工具 %s 执行异常", call.name)
            outcome = ToolOutcome(f"工具 {call.name} 执行异常："
                                  f"{type(exc).__name__}: {exc}")
            ok = False
        finally:
            if moving:
                self.scheduler.note_movement(False)
        elapsed = time.perf_counter() - started
        run.tool_calls.append(ToolExec(name=call.name,
                                       arguments=dict(call.arguments),
                                       ok=ok, text=outcome.text, elapsed=elapsed))
        logger.info("任务 %r 步 %d 工具=%s ok=%s 耗时=%.2fs 结果=%r",
                    run.instruction[:24], run.steps, call.name, ok,
                    elapsed, outcome.text[:120])
        return outcome

    # ------------------------------------------------------------------ 消息组装

    def _system_prompt(self, instruction: str) -> str:
        who = self.persona.strip() or "天狼星（Sirius），一位 Minecraft 玩家的 AI 陪玩伙伴"
        return f"""你是{who}。你通过工具感知游戏世界、执行操作、与玩家交流，自主完成玩家在聊天中交给你的任务。

## 工具选用（先读这里）
任务级原语优先——一切"移动 / 挖掘 / 采集"意图都用它们，一次调用自动完成全部寻路与
按键，不需要你操心坐标寻路细节，更不要用 input.key 一步一步走：
- walkTo(x,z[,y])：走到目标坐标。受理即执行、阻塞行走到位后才返回；失败时读返回
  文本里的建议行动照做；行走超时同参数重发即可续走剩余路程
- digBlock(x,y,z)：挖掉指定坐标的方块；目标已空算成功（幂等）；够不着时不会盲挖，
  返回文本会建议先 walkTo 过去；挖掉后返回实际掉落物（drops，经验观测非掉落表）
- collectBlock(block_ids,count[,pickup])：按方块 ID（支持 #tag）收集 N 个——自动"找最近→
  走过去→挖掉"循环到收满或 64 格范围内清空；范围内挖完但不足 count 且有收获 = 成功；
  默认挖后顺路捡起匹配的掉落物，挖通道/清理地形等不要掉落物时传 pickup=false
- pickup([item_ids][,radius])：捡起身边掉落物（走过去让磁吸拾取，缺省 12 格）；
  item_ids 给注册名只捡匹配的，缺省捡范围内全部——多人服礼仪：只捡明确属于
  自己活动的掉落物（如你刚挖出来的），别人的掉落绝对不碰
键鼠原语（input.*）定位为精细操作与 GUI 交互的兜底：开背包/箱子点槽位、拖动物品等
原语覆盖不到的场景才直接用键鼠。

## 其余工具
- 观察：getStats（自身状态）、getGuiState（当前界面与容器槽位）、world.query（附近
  实体/方块）、screenshot（截图，画面图像附在下一条消息里）
- 视角：lookAt（把视线转到世界坐标 x,y,z）
- 交流：command（在游戏聊天框发文本；/ 开头即游戏命令；# 开头是 Baritone 客户端
  命令——walkTo 已封装寻路，一般不需要手发 # 命令）
- 结束：finish（任务完成时调用，result 为游戏内播报的结束语）

## 工具边界契约
- world.query：range ≤64；filter 1..16 条（registry 名或 #tag 写法），命中按与玩家
  距离最近优先返回（最多 32 条）
- input.* 共享限频 20/s：连续输入之间稍等，收到限频错误就等一下再发
- input.click 的 hold_ms 用于长按交互（如按住挖方块）
- getGuiState 的槽位坐标是 gui-scaled，喂给 input.mouseMove 前需按比例换算成窗口像素

## 观察纪律（防幻觉直答）
- 凡答案取决于当前世界状态的问题（周围有什么/距离多远/身上有什么物品/某处方块或
  实体的现状），必须先调感知工具（getStats/getGuiState/world.query/screenshot）再
  回答；禁止凭记忆或想象描述世界现状——上一步的观测此刻可能已经过时
- 可直接回答不必查世界：闲聊打招呼、与游戏世界无关的知识问答、引用自己已有
  工具结果的任务汇报

## 安全约束
- 禁止攻击任何玩家或实体，禁止任何攻击类操作
- 不丢弃重要物品（工具/装备/贵重资源）；操作物品栏前先用 getGuiState 确认内容
- 只做与当前任务相关的事；不确定时先观察（screenshot/getGuiState/world.query）再行动
- 玩家说"停下"或"stop"时任务会立即中止

{instincts_section(self.scheduler.level)}
## 当前任务
{instruction}
"""

    async def _inject_reflex_notices(self, messages: list[dict[str, Any]]) -> None:
        """反射事后知会（M4）：behavior_log 尾部替换式注入成一条〔本能反应〕消息。

        纯简报不是记忆：无持久化、无检索，只保最近 REFLEX_NOTICE_TAIL_CHARS 字符；
        历史里恒至多一条（固定前缀识别，与滚动状态同做法）。危险感知行（〔危险〕）
        也在其中——L0 等级下动作关了、感知照进认知就是靠这条通道。
        """
        log = self.scheduler.behavior_log
        if not log:
            return
        tail = "\n".join(log)[-REFLEX_NOTICE_TAIL_CHARS:]
        messages[:] = [message for message in messages
                       if not (message.get("role") == "user"
                               and isinstance(message.get("content"), str)
                               and message["content"].startswith(REFLEX_NOTICE_PREFIX))]
        messages.append(user_message(
            f"{REFLEX_NOTICE_PREFIX}（本能/危险简报，无需回复，供你了解刚才发生了什么）\n{tail}"))

    def _drain_urgent(self, messages: list[dict[str, Any]]) -> None:
        """把排队的〔紧急〕消息 drain 进当前任务历史（death/低血经此立即送达）。"""
        while self._urgent_pending:
            text = self._urgent_pending.pop(0)
            messages.append(user_message(f"{URGENT_PREFIX}{text}"))

    async def _initial_observation(self) -> str:
        """任务开始的初始观测：getStats + getGuiState 紧凑摘要（失败不阻塞）。"""
        parts: list[str] = []
        for method in ("getStats", "getGuiState"):
            try:
                result = await self.client.call(method)
                parts.append(f"{method}: {compact_json(result)}")
            except Exception as exc:  # noqa: BLE001
                parts.append(f"{method}: 不可用（{exc}）")
        return "\n".join(parts)

    async def _inject_rolling_status(self, messages: list[dict[str, Any]]) -> None:
        """每步 VLM 调用前注入一条〔当前状态〕user 消息（M3.5，替换式不累积）。

        Numen runtime_state 的"免费搭车"做法（EntityAgentLoop）：模型每步白拿一份
        最新自身状态，不必为"我在哪/血量如何"专门花一次 getStats 工具调用。
        getStats 失败则跳过该步注入（上一条旧状态留在原地，不阻塞主循环）。
        """
        try:
            stats = await self.client.call("getStats")
        except Exception as exc:  # noqa: BLE001 —— 搭车功能失败不值得杀任务
            logger.debug("滚动状态 getStats 失败，跳过注入：%s", exc)
            return
        summary = self._status_summary(stats)
        if not summary:
            return
        # 替换式：先移除上一轮的状态消息再追加（固定前缀识别；历史里恒至多一条）
        messages[:] = [message for message in messages
                       if not (message.get("role") == "user"
                               and isinstance(message.get("content"), str)
                               and message["content"].startswith(STATUS_PREFIX))]
        messages.append(user_message(f"{STATUS_PREFIX}{summary}"))

    @staticmethod
    def _status_summary(stats: Any) -> str:
        """getStats → 单行状态摘要：位置 + 生命/饥饿/氧气（字段缺失自动跳过）。

        朝向/主手当前 getStats 不返回（bridge getStats 契约只有位置/生命/饥饿/
        饱和/氧气/经验/维度/模式/效果）；将来 bridge 若扩充字段，在此各加一行即可。
        """
        if not isinstance(stats, dict) or not stats.get("in_game"):
            return ""
        parts: list[str] = []
        position = stats.get("position")
        if isinstance(position, dict):
            try:
                parts.append(f"位置({float(position['x']):.1f},"
                             f"{float(position['y']):.1f},{float(position['z']):.1f})")
            except (KeyError, TypeError, ValueError):
                pass
        for key, label in (("health", "生命"), ("food", "饥饿"), ("air", "氧气")):
            value = stats.get(key)
            if isinstance(value, (int, float)):
                parts.append(f"{label}{value:g}")
        return " ".join(parts)

    @staticmethod
    def _assistant_message(response: VLMResponse) -> dict[str, Any]:
        """VLM 响应 → 回传历史用的 assistant 消息（含 tool_calls 原语形态）。"""
        message: dict[str, Any] = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in response.tool_calls
            ]
        return message

    @staticmethod
    def _truncate(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
        """超长观测截断（保留头尾；结果总长恒 ≤ limit，中间替换为截断记号）。"""
        if len(text) <= limit:
            return text
        marker_budget = 40  # "…[已截断 123456 字符]…" 的上界
        head = int(limit * 0.55)
        tail = max(0, limit - head - marker_budget)
        cut = len(text) - head - tail
        marker = f"…[已截断 {cut} 字符]…"
        return f"{text[:head]}{marker}{text[-tail:]}"

    @staticmethod
    def _prune_old_images(messages: list[dict[str, Any]]) -> None:
        """裁掉历史 user 消息里的全部 image_url 段（只保留即将追加的最近一张截图）。"""
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            kept = [part for part in content
                    if not (isinstance(part, dict) and part.get("type") == "image_url")]
            if len(kept) != len(content):
                if kept:
                    message["content"] = kept
                else:
                    message["content"] = "（旧截图图像已省略）"

    @staticmethod
    def _progress_summary(run: TaskRun) -> str:
        """预算用尽时的进度摘要（步数/工具序列/最近进展）。"""
        tools = "、".join(run.tool_names) or "未执行工具"
        summary = f"已执行 {run.steps} 步（{tools}）"
        if run.tool_calls:
            last = run.tool_calls[-1].text[:120]
            if last:
                summary += f"，最近进展：{last}"
        elif run.error:
            summary += f"，{run.error}"
        return summary


__all__ = [
    "AgentLoop",
    "LoopClient",
    "SelfEchoFilter",
    "TaskRun",
    "ToolExec",
    "END_BUDGET",
    "END_CONTENT",
    "END_ERROR",
    "END_FINISH",
    "END_MAX_STEPS",
    "END_PREEMPT",
    "END_STOP",
    "DEFAULT_ECHO_WINDOW",
    "MAX_TOOL_RESULT_CHARS",
    "NIL_UUID",
    "PARTIAL_DONE_PREFIX",
    "REFLEX_NOTICE_PREFIX",
    "REFLEX_NOTICE_TAIL_CHARS",
    "STATUS_PREFIX",
    "STOP_REPLY_TEXT",
    "STOP_WORDS",
    "URGENT_PREFIX",
    "bridge_error_hint",
    "match_self_uuid",
]
