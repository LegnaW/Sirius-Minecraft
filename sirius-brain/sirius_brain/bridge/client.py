"""Bridge 客户端：大脑连接身体的统一入口（asyncio + websockets）。spec §8.2 / §10.1 M1-D。

对两种身体同样工作——mock（``sirius_brain.mock``）与真 Bridge Mod（NeoForge，M1-B/C）：
协议一致，这正是"大脑不绑死身体"的第一次实战。

职责（全部复用 ``protocol/`` 的 pydantic 模型收发帧，不重复定义协议类型）：
- 连接管理：``connect()``/``close()``、异步上下文管理器、断线自动重连（次数/指数退避可配）、
  状态回调 ``on_state_change(state, detail)``
- token 握手：连接建立后首条消息发送 ``{"type":"hello","token":...,"protocol_version":...}``
  （spec §8.2 安全模型，真 Mod 要求）。握手为 best-effort：mock 身体不校验也不回应 hello，
  客户端等待 hello 回应有超时上限、不阻塞任何后续调用
- 工具调用 RPC：``call(method, params, timeout)``——ToolCallRequest 发、ToolCallResponse 收，
  uuid id 配对，超时抛 ``TimeoutError``，错误帧（-32601/-32602 等）抛 ``BridgeError``
- 命令编排：``command(text)``——按人类时序串联 input.key T / input.text / input.key ENTER，
  供大脑发送聊天与斜杠命令（M2-D）
- 能力协商：``capabilities()`` → ``CapabilitiesInfo``（能力清单 + 协议版本）
- NEKO 帧收发：``send_task(task, task_id)``、``on_task_finished`` 回调注册（五态枚举）
- 事件订阅：后台接收循环把 notification 帧按 event 分发给已注册 handler，
  seq 单调性校验（乱序只告警不致命）；``subscribe_events()`` 封装 events.subscribe 工具调用
- 前向兼容：收到无法识别的帧类型忽略并记录日志
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import ClientConnection, connect

from sirius_brain.protocol import (
    CapabilitiesListRequest,
    CapabilitiesListResponse,
    Capability,
    EventLevel,
    EventsSubscribeParams,
    NotificationFrame,
    TaskFinishedFrame,
    TaskFrame,
    ToolCallRequest,
    ToolCallResponse,
)

from .config import BridgeConfig

logger = logging.getLogger(__name__)

# 客户端合成的错误码（区别于线上协议错误码 -32700/-32600/-32601/-32602；
# 取 JSON-RPC 保留给实现方自定义的 -32000 段）
CODE_CONNECTION_LOST = -32000
CODE_NOT_CONNECTED = -32001
CODE_INVALID_RESPONSE = -32002


class BridgeError(Exception):
    """身体回的错误帧（code 为线上协议错误码）或客户端侧连接错误（code 为合成码）。"""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class BridgeState(StrEnum):
    """连接状态机：connecting → connected →（断线）reconnecting → … → disconnected。"""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


StateCallback = Callable[[BridgeState, str], None]
EventHandler = Callable[[NotificationFrame], Any]
TaskFinishedHandler = Callable[[TaskFinishedFrame], Any]

HelloStatus = Literal["no-token", "acked", "ignored", "timeout"]

# command() 用的 GLFW 键码（冻结 schema input.key.code 声明的是整数；键名是
# Mod 侧的扩展。GLFW: 字母=大写 ASCII，ENTER=257——与 KeyCodes.java 一致）
GLFW_KEY_T = 84
GLFW_KEY_ENTER = 257


class HelloFrame(BaseModel):
    """连接后首条消息的 token 握手帧。spec §8.2 安全模型。

    真 Mod（M1-B/C）要求此帧；mock 不校验也不要求（会回一条 -32600 的未知帧错误，
    客户端按 best-effort 忽略）。protocol/ 未定义 hello（它不是工具调用协议的一部分），
    故在此定义——不与既有协议模型重复。
    """

    type: Literal["hello"] = "hello"
    token: str
    protocol_version: str


@dataclass(frozen=True)
class HelloResult:
    """hello 握手结果（best-effort，任何状态都不影响后续调用）。

    status：no-token=未配置 token；acked=收到非错误回应；ignored=身体回错误帧表示
    不认识 hello（mock 行为）；timeout=hello_timeout 内无回应。
    """

    status: HelloStatus
    detail: str = ""


@dataclass(frozen=True)
class CapabilitiesInfo:
    """capabilities() 协商结果：能力清单 + 协议版本。spec §8.2。"""

    capabilities: list[Capability]
    protocol_version: str


@dataclass
class _PendingCall:
    """在途请求：kind 决定响应按哪个协议模型解析（tool / capabilities）。"""

    kind: Literal["tool", "capabilities"]
    future: asyncio.Future


class BridgeClient:
    """大脑 ↔ 身体的 WebSocket 客户端。

    用法::

        client = BridgeClient("ws://127.0.0.1:8765", token="s3cret")
        async with client:
            info = await client.capabilities()
            stats = await client.call("getStats")
            await client.send_task("挖一组铁矿")

    重连语义：``connect()`` 首连失败立即报错（不自动重试，调用方能拿到清晰错误）；
    已建立的连接断开后按 ``BridgeConfig`` 的次数/指数退避自动重连，
    在途请求以 ``BridgeError(CODE_CONNECTION_LOST)`` 失败。

    注意：事件 / task_finished handler 建议在 ``connect()`` 之前注册——
    身体可能在连接建立的一瞬间就推送帧，晚注册会错过这批最早的事件。
    """

    def __init__(
        self,
        config: BridgeConfig | str | None = None,
        *,
        url: str | None = None,
        token: str | None = None,
        on_state_change: StateCallback | None = None,
    ) -> None:
        if isinstance(config, str):
            config = BridgeConfig(url=config)
        elif config is None:
            config = BridgeConfig()
        if url is not None or token is not None:
            config = config.with_overrides(url=url, token=token)
        self.config = config
        self.on_state_change = on_state_change
        self.hello_result: HelloResult | None = None
        self._state = BridgeState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._send_lock = asyncio.Lock()
        self._supervisor: asyncio.Task | None = None
        self._closing = False
        self._ready: asyncio.Future[None] | None = None
        self._hello_waiter: asyncio.Future | None = None
        self._hello_done: asyncio.Future[None] | None = None
        self._hello_task: asyncio.Task | None = None
        self._pending: dict[str, _PendingCall] = {}
        self._last_seq: int | None = None
        self._event_handlers: dict[str, list[EventHandler]] = {}
        self._task_finished_handlers: list[TaskFinishedHandler] = []

    # ------------------------------------------------------------------ 生命周期

    @property
    def state(self) -> BridgeState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state is BridgeState.CONNECTED and self._ws is not None

    async def connect(self) -> "BridgeClient":
        """建立连接（等待首连结果）。首连失败抛 ``BridgeError``，不自动重试。"""
        if self._supervisor is not None and not self._supervisor.done():
            return self  # 已在连接/已连接
        self._closing = False
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._hello_done = loop.create_future()
        self._pending = {}
        self._supervisor = asyncio.create_task(
            self._supervise(), name="bridge-client-supervisor")
        await self._ready
        return self

    async def close(self) -> None:
        """关闭连接并停止重连（幂等）。在途请求以 CODE_CONNECTION_LOST 失败。"""
        self._closing = True
        for task in (self._hello_task, self._supervisor):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._hello_task, self._supervisor):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 —— 关闭时吞掉后台任务异常并记录
                    logger.exception("关闭 bridge 客户端后台任务时出错")
        self._hello_task = None
        self._supervisor = None
        self._ws = None
        self._abort_pending("客户端已关闭")
        if self._state is not BridgeState.DISCONNECTED:
            self._set_state(BridgeState.DISCONNECTED, "客户端主动关闭")

    async def __aenter__(self) -> "BridgeClient":
        return await self.connect()

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def wait_hello(self, timeout: float | None = None) -> HelloResult | None:
        """等待 best-effort hello 握手出结果（未连接/超时返回 None，绝不抛错）。"""
        fut = self._hello_done
        if fut is None:
            return None
        try:
            await asyncio.wait_for(asyncio.shield(fut), timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return None
        return self.hello_result

    # ------------------------------------------------------------------ RPC

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """工具调用 RPC：发 ToolCallRequest，按 id 配对等 ToolCallResponse。

        - 正常返回 ``response.result``
        - 身体回错误帧（-32601/-32602 等）→ ``BridgeError(code, message, data)``
        - 超时 / 连接断开 → ``TimeoutError`` / ``BridgeError(CODE_CONNECTION_LOST)``
        """
        if timeout is None:
            timeout = self.config.request_timeout
        req_id, pending = self._register_pending("tool")
        request = ToolCallRequest(id=req_id, method=method, params=dict(params or {}))
        try:
            await self._send(request)
        except BridgeError:
            self._pending.pop(req_id, None)
            raise
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise BridgeError(CODE_CONNECTION_LOST, f"发送请求失败（{method}）：{exc}") from exc

        try:
            response = await asyncio.wait_for(pending.future, timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)  # 迟到的回包会被接收循环按未知 id 忽略
            raise TimeoutError(f"工具调用 {method!r} 超时（>{timeout:.1f}s）") from None
        if response.error is not None:
            raise BridgeError(response.error.code, response.error.message, response.error.data)
        return response.result

    async def capabilities(self, timeout: float | None = None) -> CapabilitiesInfo:
        """能力协商：capabilities/list 往返，返回能力清单 + 协议版本。spec §8.2。"""
        if timeout is None:
            timeout = self.config.request_timeout
        req_id, pending = self._register_pending("capabilities")
        request = CapabilitiesListRequest(id=req_id)
        try:
            await self._send(request)
        except BridgeError:
            self._pending.pop(req_id, None)
            raise
        except Exception as exc:
            self._pending.pop(req_id, None)
            raise BridgeError(CODE_CONNECTION_LOST, f"发送请求失败（capabilities/list）：{exc}") from exc

        try:
            response = await asyncio.wait_for(pending.future, timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"capabilities/list 超时（>{timeout:.1f}s）") from None
        if response.error is not None:
            raise BridgeError(response.error.code, response.error.message, response.error.data)
        return CapabilitiesInfo(
            capabilities=list(response.result),
            protocol_version=response.protocol_version,
        )

    async def subscribe_events(
        self,
        types: Iterable[str],
        min_level: EventLevel | None = None,
        timeout: float | None = None,
    ) -> Any:
        """``events.subscribe`` 工具调用的便捷封装（参数经 EventsSubscribeParams 客户端侧校验）。"""
        params = EventsSubscribeParams(types=list(types), min_level=min_level)
        return await self.call(
            "events.subscribe",
            params.model_dump(mode="json", exclude_none=True),
            timeout,
        )

    async def command(self, text: str, settle: float = 0.5,
                      timeout: float | None = None) -> Any:
        """聊天/命令编排：T 开聊天框 → 输入文本 → ENTER 发送 → 等待生效。

        按"人类打命令"的时序串联三个 M2-A 原语（缺一步游戏都可能丢字）：
        ``input.key {"code":84}``（T，默认 50ms tap）→ 0.4s 等聊天框真正获得焦点 →
        ``input.text {"string": text}`` → 0.3s 等全部码点入框 →
        ``input.key {"code":257}``（ENTER）发送 → ``settle`` 秒收尾。
        code 用整数键码是冻结 schema 的形态（键名 "T"/"ENTER" 只是 Mod 侧扩展）。

        - ``text`` 以 ``/`` 开头即命令（``/give @s diamond 1``），否则按普通聊天
          消息发送——两者对客户端输入路径完全一致，本方法不做区分
        - 返回最后一步（ENTER 的 input.key）的 result；任何一步被身体拒绝
          （-32010 限频 / -32011 输入关闭 / -32012 权限分级 / -32602 参数）都抛
          ``BridgeError``
        - ``settle`` 默认 0.5s：命令生效后的世界变化（如 /give 的物品进背包）
          需要服务器往返 + 客户端容器同步，立刻截图/查背包会读到旧状态——
          Mindcraft CE 的 /give 即此模式。查结果建议再等 getStats/inventory 就绪
        """
        await self.call("input.key", {"code": GLFW_KEY_T}, timeout)
        await asyncio.sleep(0.4)  # 等聊天框打开并获得焦点（E/T 开屏是下一帧的事）
        await self.call("input.text", {"string": text}, timeout)
        await asyncio.sleep(0.3)  # 等全部码点经 charTyped 入框
        result = await self.call("input.key", {"code": GLFW_KEY_ENTER}, timeout)
        await asyncio.sleep(settle)  # 等命令在服务器生效并同步回来
        return result

    # ------------------------------------------------------------------ NEKO 帧

    async def send_task(self, task: str, task_id: str | None = None) -> str:
        """发 NEKO task 帧（fire-and-forget）。task_id 缺省生成 uuid；返回实际使用的 id。

        完成事件通过 ``on_task_finished`` 注册的回调送达（task_finished.task_id 原样回传）。
        """
        if task_id is None:
            task_id = uuid.uuid4().hex
        await self._send(TaskFrame(task=task, task_id=task_id))
        return task_id

    def add_task_finished_handler(self, handler: TaskFinishedHandler) -> Callable[[], None]:
        """注册 task_finished 回调（可多次注册；返回注销函数）。"""
        self._task_finished_handlers.append(handler)
        return lambda: self._task_finished_handlers.remove(handler)

    def on_task_finished(self, handler: TaskFinishedHandler) -> TaskFinishedHandler:
        """``on_task_finished`` 回调注册的装饰器风格。"""
        self.add_task_finished_handler(handler)
        return handler

    # ------------------------------------------------------------------ 事件

    def add_event_handler(self, event: str, handler: EventHandler) -> Callable[[], None]:
        """注册某事件名的 handler（"*" = 通配所有事件；返回注销函数）。"""
        self._event_handlers.setdefault(event, []).append(handler)
        return lambda: self._event_handlers.get(event, []).remove(handler)

    def on_event(self, event: str) -> Callable[[EventHandler], EventHandler]:
        """事件 handler 注册的装饰器风格：``@client.on_event("fire")``。"""
        def decorator(handler: EventHandler) -> EventHandler:
            self.add_event_handler(event, handler)
            return handler
        return decorator

    # ------------------------------------------------------------------ 内部：连接监督

    async def _supervise(self) -> None:
        """连接生命周期监督：建连 → hello → 接收循环 → 断线善后 →（可选）重连。"""
        ever_connected = False
        attempt = 0
        try:
            while True:
                self._set_state(BridgeState.CONNECTING, self.config.url)
                drop_detail = "对端关闭了连接"
                try:
                    # reconnect_delays=None：关闭库内建重连，断线策略统一由本类监督循环掌管
                    async with connect(self.config.url,
                                       open_timeout=self.config.connect_timeout,
                                       reconnect_delays=None) as ws:
                        self._ws = ws
                        self._last_seq = None  # 新连接 seq 空间重置（每连接从 0 单调）
                        ever_connected = True
                        attempt = 0
                        hello_waiter = await self._send_hello()
                        self._set_state(BridgeState.CONNECTED, "")
                        if not self._ready.done():
                            self._ready.set_result(None)
                        self._hello_task = asyncio.create_task(
                            self._wait_hello_ack(hello_waiter),
                            name="bridge-client-hello")
                        await self._recv_loop(ws)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 —— 统一按断线处理
                    drop_detail = f"{type(exc).__name__}: {exc}"

                # ---- 断线善后（正常关闭与异常关闭同路）
                self._ws = None
                self._hello_waiter = None
                self._abort_pending(drop_detail)
                if self._closing:
                    break
                if not ever_connected:
                    # 首连失败：立即报错退出（不自动重试），connect() 拿到清晰异常
                    if not self._ready.done():
                        self._ready.set_exception(BridgeError(
                            CODE_CONNECTION_LOST,
                            f"无法连接 {self.config.url}：{drop_detail}",
                        ))
                    break
                attempt += 1
                if self.config.max_reconnects is not None and attempt > self.config.max_reconnects:
                    logger.warning("重连次数耗尽（max_reconnects=%s），放弃",
                                   self.config.max_reconnects)
                    break
                delay = min(self.config.reconnect_base_delay * 2 ** (attempt - 1),
                            self.config.reconnect_max_delay)
                self._set_state(BridgeState.RECONNECTING,
                                f"第 {attempt} 次重连（{delay:.1f}s 后重试；原因：{drop_detail}）")
                await asyncio.sleep(delay)
        finally:
            self._ws = None
            self._set_state(BridgeState.DISCONNECTED, "")
            if not self._ready.done():
                self._ready.set_exception(BridgeError(
                    CODE_CONNECTION_LOST, f"连接监督任务退出：{self.state.value}"))

    async def simulate_disconnect(self) -> None:
        """主动断开当前 WebSocket 以触发自动重连路径（测试/诊断用）。"""
        if self._ws is not None:
            await self._ws.close()

    async def _send_hello(self) -> asyncio.Future | None:
        """发送 hello 帧——同步执行，保证它是本连接首条出站消息（真 Mod 要求）。

        返回 hello 回应观察 future（接收循环收到任何帧都会 poke 它）；
        未配置 token 时不发 hello，直接记 no-token 并返回 None。
        """
        if not self.config.token:
            self.hello_result = HelloResult("no-token", "未配置 token，跳过握手")
            return None
        waiter = asyncio.get_running_loop().create_future()
        self._hello_waiter = waiter
        await self._send(HelloFrame(token=self.config.token,
                                    protocol_version=self.config.protocol_version))
        return waiter

    async def _wait_hello_ack(self, waiter: asyncio.Future | None) -> None:
        """后台等待 hello 回应（best-effort）：不阻塞连接就绪与任何 RPC。"""
        try:
            if waiter is None:
                return  # 未配置 token，_send_hello 已记 no-token
            try:
                ack = await asyncio.wait_for(waiter, self.config.hello_timeout)
            except TimeoutError:
                self.hello_result = HelloResult(
                    "timeout",
                    f"{self.config.hello_timeout:.1f}s 内无 hello 回应（best-effort，不影响使用）")
                return
            if isinstance(ack, dict) and ack.get("type") == "response" \
                    and isinstance(ack.get("error"), dict):
                err = ack["error"]
                self.hello_result = HelloResult(
                    "ignored",
                    f"身体回错误帧（code={err.get('code')}, "
                    f"message={err.get('message')!r}）——按不支持 hello 处理")
            else:
                ack_type = ack.get("type") if isinstance(ack, dict) else type(ack).__name__
                self.hello_result = HelloResult("acked", f"收到回应 type={ack_type!r}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 握手绝不拖垮连接
            self.hello_result = HelloResult("ignored", f"hello 等待出错：{exc}")
        finally:
            self._hello_waiter = None
            if self._hello_done is not None and not self._hello_done.done():
                self._hello_done.set_result(None)

    # ------------------------------------------------------------------ 内部：收发

    async def _recv_loop(self, ws: ClientConnection) -> None:
        """后台接收循环：单帧处理异常不致命（记录后继续收）。"""
        async for raw in ws:
            try:
                await self._handle_raw(raw)
            except Exception:  # noqa: BLE001
                logger.exception("处理入站帧出错（忽略该帧，继续接收）")

    async def _handle_raw(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            msg: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("收到非 JSON 消息，忽略：%s", exc)
            self._poke_hello(None)
            return
        if not isinstance(msg, dict):
            logger.warning("帧必须是 JSON 对象，忽略：%r", raw)
            self._poke_hello(msg)
            return
        self._poke_hello(msg)
        frame_type = msg.get("type")
        if frame_type == "response":
            await self._handle_response(msg)
        elif frame_type == "notification":
            await self._handle_notification(msg)
        elif frame_type == "task_finished":
            await self._handle_task_finished(msg)
        else:
            # 前向兼容：身体新增帧类型时旧客户端不崩。spec §8.2。
            logger.info("忽略无法识别的帧 type=%r（前向兼容）", frame_type)

    async def _handle_response(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        pending = self._pending.pop(req_id, None) if isinstance(req_id, str) else None
        if pending is None:
            # 典型场景：mock 对 hello 回的 id="" 错误帧、超时后迟到的回包
            logger.debug("收到无法配对的响应（id=%r），忽略", req_id)
            return
        model = CapabilitiesListResponse if pending.kind == "capabilities" else ToolCallResponse
        try:
            frame = model.model_validate(msg)
        except ValidationError as exc:
            pending.future.set_exception(BridgeError(
                CODE_INVALID_RESPONSE, f"响应帧校验失败（id={req_id!r}）",
                data=[{"loc": list(e["loc"]), "msg": str(e["msg"]), "type": e["type"]}
                      for e in exc.errors(include_url=False)]))
            return
        if not pending.future.done():
            pending.future.set_result(frame)

    async def _handle_notification(self, msg: dict[str, Any]) -> None:
        try:
            frame = NotificationFrame.model_validate(msg)
        except ValidationError as exc:
            logger.warning("notification 帧校验失败，忽略：%s", exc)
            return
        if self._last_seq is not None and frame.seq <= self._last_seq:
            # 乱序告警不致命：仍分发，last_seq 不回退（后续恢复单调判断基线）
            logger.warning("事件 seq 乱序：last=%d got=%d（event=%s，仍分发）",
                           self._last_seq, frame.seq, frame.event)
        self._last_seq = frame.seq if self._last_seq is None else max(self._last_seq, frame.seq)
        handlers = [*self._event_handlers.get(frame.event, []),
                    *self._event_handlers.get("*", [])]
        for handler in handlers:
            try:
                outcome = handler(frame)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:  # noqa: BLE001
                logger.exception("事件 handler 抛错（event=%s）", frame.event)

    async def _handle_task_finished(self, msg: dict[str, Any]) -> None:
        try:
            frame = TaskFinishedFrame.model_validate(msg)
        except ValidationError as exc:
            logger.warning("task_finished 帧校验失败，忽略：%s", exc)
            return
        for handler in list(self._task_finished_handlers):
            try:
                outcome = handler(frame)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:  # noqa: BLE001
                logger.exception("task_finished handler 抛错（task_id=%s）", frame.task_id)

    def _poke_hello(self, payload: Any) -> None:
        """hello 等待者观察第一帧（不消费帧，正常分发照旧）。"""
        waiter = self._hello_waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(payload)

    async def _send(self, frame: BaseModel) -> None:
        ws = self._ws
        if ws is None:
            raise BridgeError(CODE_NOT_CONNECTED, "未连接（先 await client.connect()）")
        async with self._send_lock:
            await ws.send(frame.model_dump_json())

    def _register_pending(self, kind: Literal["tool", "capabilities"]) -> tuple[str, _PendingCall]:
        req_id = uuid.uuid4().hex
        pending = _PendingCall(kind=kind, future=asyncio.get_running_loop().create_future())
        self._pending[req_id] = pending
        return req_id, pending

    def _abort_pending(self, reason: str | BaseException) -> None:
        """断线/关闭时让全部在途请求立刻失败（避免调用方挂到自己的超时）。"""
        for req_id, pending in list(self._pending.items()):
            self._pending.pop(req_id, None)
            if not pending.future.done():
                pending.future.set_exception(BridgeError(
                    CODE_CONNECTION_LOST, f"连接断开，请求 {req_id[:8]}… 未完成（{reason}）"))

    def _set_state(self, state: BridgeState, detail: str = "") -> None:
        if state is self._state and not detail:
            return
        self._state = state
        logger.debug("bridge 状态 → %s %s", state.value, detail)
        if self.on_state_change is not None:
            try:
                self.on_state_change(state, detail)
            except Exception:  # noqa: BLE001
                logger.exception("状态回调抛错（on_state_change）")
