"""M3-B 工具注册表：把 bridge 工具 + 大脑侧自定义工具组装成 VLM function-calling 工具表。

设计要点：
- **参数 schema 从冻结产物读**：``schema/tools/*.json`` 是 ``protocol.TOOL_PARAMS`` 的
  导出物（Java 侧同源），读它保证 VLM 看到的参数契约与 bridge 校验完全一致；
  客户端侧再用同一 ``TOOL_PARAMS`` pydantic 模型预校验——白名单外的/参数错的
  调用在本地就拒绝，不浪费一次 bridge 往返
- **M3 白名单最小集**：观察（getStats/getGuiState/world.query/screenshot）+ 视角
  （lookAt）+ 输入四原语 + command（说话/游戏指令，走 BridgeClient.command 编排）
  + finish（自定义控制工具：结束任务并在游戏内播报 result）；
  **M3.5 追加任务级原语** walkTo/digBlock/collectBlock（handler 包装
  ``agent.primitives``，描述写 Numen 式契约——受理即执行/失败读建议/超时同参重发续做）
- **handler 统一签名**：``async handler(client, args) -> ToolOutcome``。观测结果压成
  紧凑 JSON 文本回填 VLM；screenshot 特殊——文本只回 ``[图像已附]``，JPEG bytes 放
  ``ToolOutcome.image``，由循环附进下一轮 user 消息
- **可扩展**：``ToolRegistry.register(ToolSpec(...))`` 随时挂新工具（M5 分层留口），
  白名单外的名字在 ``execute`` 一律 ``UnknownToolError``（即便 schema 目录里有，
  如 look / events.subscribe 不在 M3 白名单内）
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from sirius_brain.bridge.client import BridgeClient
from sirius_brain.protocol import TOOL_PARAMS

logger = logging.getLogger(__name__)

# 冻结 schema 产物目录：sirius-brain/schema/tools/（本文件在 sirius_brain/agent/ 下）
SCHEMA_TOOLS_DIR = Path(__file__).resolve().parents[2] / "schema" / "tools"

# M3 白名单：bridge 既有工具（schema 产物里读参数）+ 大脑侧自定义工具（command/finish）
BRIDGE_WHITELIST: tuple[str, ...] = (
    "getStats",
    "getGuiState",
    "world.query",
    "screenshot",
    "lookAt",
    "input.mouseMove",
    "input.click",
    "input.key",
    "input.text",
)
# 自定义工具名（不在 bridge schema 里，参数与 handler 都在本文件定义）
COMMAND_TOOL = "command"
FINISH_TOOL = "finish"
# M3.5 任务级原语工具名（handler 包装 agent.primitives，走 Baritone/键鼠组合）
WALK_TO_TOOL = "walkTo"
DIG_BLOCK_TOOL = "digBlock"
COLLECT_BLOCK_TOOL = "collectBlock"
# M3.6 掉落物拾取工具名（Numen collect_items 式契约）
PICKUP_TOOL = "pickup"

# 给 VLM 的工具用途说明（schema 的 description 是"getStats()。spec §8.2。"这类
# 内部记号，对模型无用；这里换成面向模型的操作指引）
TOOL_HINTS: dict[str, str] = {
    "getStats": "查看自身状态：生命/饥饿/氧气/经验/坐标(x,y,z)/维度/游戏模式/状态效果",
    "getGuiState": "查看当前打开的界面（背包/箱子/聊天框等）：widget 树与容器槽位"
                   "（含物品注册名与数量）。注意：槽位坐标是 gui-scaled，"
                   "input.mouseMove 收窗口像素，需按比例换算后使用",
    "world.query": "查询自己附近的实体（type=entities，含 uuid/名字/类型/坐标/生命；"
                   "掉落物实体另带物品注册名 item 与数量 count）"
                   "或非空气方块（type=blocks）；range 为半径（格）",
    "screenshot": "截取当前游戏画面；图像会附加在下一条消息里供你查看",
    "lookAt": "把视线转到世界坐标 (x,y,z)（绝对转视角，用于看向目标/瞄准）",
    "input.mouseMove": "移动鼠标光标到窗口像素坐标 (x,y)",
    "input.click": "鼠标点击（button：0=左键 1=右键 2=中键；count=次数，默认 1）",
    "input.key": "按一个键（code 为 GLFW 键码：E=69 T=84 ENTER=257 W=87 A=65 S=83 D=68；"
                 "duration_ms 为按住时长；modifiers 如 [\"shift\"]）",
    "input.text": "向当前聚焦的文本框输入字符串（先开聊天框/输入框再输入）",
    "command": "在游戏聊天框发送一条文本：以 / 开头即游戏命令（如 /give），"
               "否则是普通聊天发言",
    "finish": "任务完成时调用：result 是要在游戏聊天里播报的结束语。"
              "调用后本任务结束，不再执行任何工具",
}

# M3.5 任务级原语的 Numen 式契约描述（参照 minecraft-numen AutoMineTool.description
# 的写法：意图级语义 + 受理即执行 + 失败读建议 + 超时同参重发续做；模型读描述
# 就知道"该用哪个、失败怎么办"，不需要额外文档）
PRIMITIVE_TOOL_HINTS: dict[str, str] = {
    WALK_TO_TOOL:
        "走到目标坐标 (x,z)（y 可选）。受理即执行：自动寻路并阻塞行走到位后才返回，"
        "不需要你操心路径与按键细节，更不要用 input.key 一步步走。成功返回最终坐标；"
        "失败时读返回文本里的建议行动照做；行走超时时同参数重发即可续走剩余路程",
    DIG_BLOCK_TOOL:
        "挖掉 (x,y,z) 处的方块。受理即执行：自动看向目标并按住左键挖掘直到破坏。"
        "目标已空视为成功（幂等，可放心复查）；距离超出触及范围时不会盲挖，"
        "失败文本会建议先 walkTo 过去；挖不动时文本会说明可能原因并给出下一步建议",
    COLLECT_BLOCK_TOOL:
        "按方块 ID 收集 count 个：自动在 64 格范围内找最近的、走过去、挖掉，循环到收满"
        "或范围内清空——不需要坐标。block_ids 支持 registry 名与 #tag 写法"
        "（如 #minecraft:logs），同一物品的全部变体都要列上（如 iron_ore 和 "
        "deepslate_iron_ore）。挖掉后会顺路捡起匹配的掉落物并附在结果里"
        "（pickup，默认 true）；挖通道/清理地形等不要掉落物时传 pickup=false。"
        "契约：范围内已挖完但不足 count 且有收获 = 成功"
        "（文本会说明挖到几个）；范围内一个都没有 = 失败（建议确认 ID 或走近些）；"
        "超时时同参数重发可续做",
    PICKUP_TOOL:
        "捡起身边的掉落物（Numen collect_items 式契约）：自动走到掉落物旁让游戏"
        "磁吸拾取，实体消失 = 已捡起，不匹配的绝对不碰。item_ids 给物品注册名"
        "（最多 8 个）只捡匹配的；缺省捡 radius 范围内全部掉落。多人服礼仪："
        "只捡明确属于自己活动的掉落物（如你刚挖出来的），别人的掉落不要碰。"
        "radius 默认 12 格；0 件也是成功（范围内没有可捡的）",
}

# 两张表合并给 VLM（分开放是原语描述长且自成体系，与 bridge 工具的一句话提示分层）
TOOL_HINTS.update(PRIMITIVE_TOOL_HINTS)


class UnknownToolError(Exception):
    """工具名不在注册表（白名单）内。args[0] 为工具名。"""


# ---------------------------------------------------------------------- 参数模型（自定义工具）


class CommandToolParams(BaseModel):
    """command({text})：在游戏聊天框发送文本（/ 开头即游戏命令）。"""

    text: str


class FinishToolParams(BaseModel):
    """finish({result})：结束当前任务并在游戏内播报 result。"""

    result: str


class WalkToParams(BaseModel):
    """walkTo({x, z, y?})：任务级行走原语（Baritone #goto 封装）。"""

    x: float
    z: float
    y: float | None = None


class DigBlockParams(BaseModel):
    """digBlock({x, y, z})：任务级挖掘原语（对准 + 按住左键到破坏）。"""

    x: int
    y: int
    z: int


class CollectBlockParams(BaseModel):
    """collectBlock({block_ids, count, pickup?})：任务级采集原语（找最近→走位→挖→拾取，循环）。"""

    # 1..16 条与 bridge world.query filter 的契约对齐（条数超限本地即拒绝）
    block_ids: list[str] = Field(min_length=1, max_length=16)
    # 1..64：单次任务的合理上限（防一次调用挖穿整个矿脉预算）
    count: int = Field(ge=1, le=64)
    # T7：挖掉后是否顺路捡起匹配的掉落物（要获得目标物品 = True 默认；
    # 挖通道/清理地形等不要掉落物 = False，如圆石一路捡会拖慢挖掘）
    pickup: bool = True


class PickupParams(BaseModel):
    """pickup({item_ids?, radius?})：捡起身边掉落物（走位磁吸；M3.6 注册为 VLM 工具）。

    item_ids 缺省 = 捡 radius 内全部掉落（多人服礼仪由工具描述约束 VLM：只对
    明确属于自己活动的掉落使用缺省形式）；radius 与 brain 侧 DROP_QUERY_RANGE
    默认对齐 12 格。
    """

    item_ids: list[str] | None = Field(default=None, min_length=1, max_length=8)
    radius: int = Field(default=12, ge=1, le=32)


# ---------------------------------------------------------------------- 注册表


@dataclass(frozen=True)
class ToolOutcome:
    """工具执行结果：text 回填 VLM（由循环做长度截断），image 为 screenshot 的图像 bytes。"""

    text: str
    image: bytes | None = None


ToolHandler = Callable[[Any, dict[str, Any]], Awaitable[ToolOutcome]]


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的完整定义：VLM 可见的元数据 + 执行体 + 参数校验模型。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    # 客户端侧参数校验模型（None = 不校验，直接透传）
    params_model: type[BaseModel] | None = None


def compact_json(obj: Any) -> str:
    """观测结果 → 紧凑 JSON 文本（回填 VLM 用；不可序列化的对象退化为 repr）。"""
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(obj)


class ToolRegistry:
    """工具注册表：name → ToolSpec；产出 OpenAI function-calling 工具表并统一执行。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------------ 装配

    def register(self, spec: ToolSpec) -> None:
        """注册一个工具（重名覆盖并记 warning——扩展点，M5 分层用）。"""
        if spec.name in self._tools:
            logger.warning("工具 %s 重复注册，后者覆盖前者", spec.name)
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def openai_tools(self) -> list[dict[str, Any]]:
        """产出 OpenAI function-calling 工具表（QwenVLM.chat 的 tools 参数）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tools.values()
        ]

    # ------------------------------------------------------------------ 执行

    async def execute(self, client: Any, name: str,
                      arguments: Mapping[str, Any] | None) -> ToolOutcome:
        """执行一次工具调用：白名单检查 → 参数校验 → handler。

        - 未注册（白名单外）→ :class:`UnknownToolError`
        - 参数不过 schema → :class:`pydantic.ValidationError`
        - handler 内的 bridge 错误（BridgeError/Timeout）由调用方（循环）翻译成文本
        """
        spec = self._tools.get(name)
        if spec is None:
            raise UnknownToolError(name)
        raw = dict(arguments or {})
        if spec.params_model is not None:
            validated = spec.params_model.model_validate(raw)
            args = validated.model_dump(mode="json", exclude_none=True)
        else:
            args = raw
        return await spec.handler(client, args)


# ---------------------------------------------------------------------- schema 装载


def load_schema_parameters(method: str) -> dict[str, Any]:
    """从冻结产物 ``schema/tools/<method>.json`` 读参数 JSON Schema（剥掉 $schema 记号键）。

    文件不存在 → ``FileNotFoundError``（白名单里的方法必须有冻结 schema，缺了是装配错误）。
    """
    path = SCHEMA_TOOLS_DIR / f"{method}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: value for key, value in data.items() if key != "$schema"}


# ---------------------------------------------------------------------- handlers


def _json_tool(method: str) -> ToolHandler:
    """通用观察/动作工具工厂：bridge 调用结果整体压成紧凑 JSON 文本。"""

    async def handler(client: BridgeClient, args: dict[str, Any]) -> ToolOutcome:
        result = await client.call(method, args)
        return ToolOutcome(compact_json(result))

    return handler


async def _handle_screenshot(client: BridgeClient, args: dict[str, Any]) -> ToolOutcome:
    """screenshot：图像 bytes 放 ToolOutcome.image（循环附进下一轮 user 消息）。"""
    result = await client.call("screenshot", args)
    image_b64 = None
    if isinstance(result, Mapping):
        image_b64 = result.get("image_b64") or result.get("jpeg_b64")
    if not image_b64:
        # 不出图像的异常形态如实回给模型，让它决定重试还是换路
        return ToolOutcome(f"screenshot 未返回图像：{compact_json(result)}")
    try:
        image = base64.b64decode(image_b64)
    except (ValueError, TypeError) as exc:
        return ToolOutcome(f"screenshot 图像解码失败：{exc}；{compact_json(result)}")
    meta = ""
    if isinstance(result, Mapping):
        meta = (f" {result.get('width', '?')}x{result.get('height', '?')}"
                f" {result.get('format', 'jpeg')}")
    return ToolOutcome(f"[图像已附]{meta}", image=image)


async def _handle_command(client: BridgeClient, args: dict[str, Any]) -> ToolOutcome:
    """command：在游戏聊天框发送文本（client 通常是循环的包装客户端，自回显在那里登记）。"""
    await client.command(args["text"])
    return ToolOutcome(f"已发送：{args['text']}")


async def _handle_finish(client: BridgeClient, args: dict[str, Any]) -> ToolOutcome:
    """finish：不做任何事，result 由循环负责在游戏内播报。"""
    return ToolOutcome(args["result"])


def _primitive_factories(cancel: Callable[[], bool] | None) -> tuple[ToolHandler, ...]:
    """任务级原语的 handler 工厂（M3.5 T3 三个 + M3.6 pickup）。

    - ``cancel``：绑定循环急停态的检查回调（AgentLoop 传 ``lambda: self._stop_requested``；
      None = 原语不可取消——注册表独立使用/测试时）。registry.execute 的签名是
      (client, name, args) 没有 loop 引用，所以取消态经此闭包在构造期接进去
    - Primitives 在函数体内延迟导入：primitives.py 顶部 import 本模块的 ToolOutcome，
      模块级反向 import 会成环
    - 每次 handler 调用实例化一个 Primitives（无状态包装，开销可忽略；client 由
      registry.execute 按次传入——循环里传的是 LoopClient，command 走自回显登记）
    """
    from .primitives import Primitives

    async def walk_to(client: Any, args: dict[str, Any]) -> ToolOutcome:
        return await Primitives(client).walk_to(
            args["x"], args["z"], y=args.get("y"), cancel=cancel)

    async def dig_block(client: Any, args: dict[str, Any]) -> ToolOutcome:
        return await Primitives(client).dig_block(
            args["x"], args["y"], args["z"], cancel=cancel)

    async def collect_block(client: Any, args: dict[str, Any]) -> ToolOutcome:
        return await Primitives(client).collect_block(
            args["block_ids"], args["count"],
            pickup=args.get("pickup", True), cancel=cancel)

    async def pickup(client: Any, args: dict[str, Any]) -> ToolOutcome:
        # item_ids 被 pydantic exclude_none 排除 = 缺省"捡全部"语义
        return await Primitives(client).pickup(
            args.get("item_ids"), radius=args.get("radius", 12), cancel=cancel)

    return walk_to, dig_block, collect_block, pickup


# ---------------------------------------------------------------------- 默认注册表


def default_registry(cancel: Callable[[], bool] | None = None) -> ToolRegistry:
    """M3.6 默认注册表（9 bridge 工具 + 4 任务级原语 + command + finish = 15 个）。

    ``cancel``：原语的急停检查回调，AgentLoop 构造时传 ``lambda: self._stop_requested``
    把循环急停态接进原语微步循环；None = 不可取消（独立使用/测试，向后兼容旧签名）。
    """
    registry = ToolRegistry()
    for method in BRIDGE_WHITELIST:
        parameters = load_schema_parameters(method)
        handler: ToolHandler = (_handle_screenshot if method == "screenshot"
                                else _json_tool(method))
        registry.register(ToolSpec(
            name=method,
            description=TOOL_HINTS.get(method)
            or str(parameters.get("description") or method),
            parameters=parameters,
            handler=handler,
            params_model=TOOL_PARAMS.get(method),
        ))
    # 任务级原语：排在键鼠原语之后、command/finish 之前——系统提示里的分层引导
    # （原语优先、键鼠兜底）才是选用顺序的权威，这里只保证都在表里
    walk_to, dig_block, collect_block, pickup = _primitive_factories(cancel)
    registry.register(ToolSpec(
        name=WALK_TO_TOOL,
        description=TOOL_HINTS[WALK_TO_TOOL],
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "目标 X（世界坐标）"},
                "z": {"type": "number", "description": "目标 Z（世界坐标）"},
                "y": {"type": "number",
                      "description": "目标 Y（可选；不给则由寻路器落到目标处的地面）"},
            },
            "required": ["x", "z"],
        },
        handler=walk_to,
        params_model=WalkToParams,
    ))
    registry.register(ToolSpec(
        name=DIG_BLOCK_TOOL,
        description=TOOL_HINTS[DIG_BLOCK_TOOL],
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "方块 X 坐标（世界坐标）"},
                "y": {"type": "integer", "description": "方块 Y 坐标（世界坐标）"},
                "z": {"type": "integer", "description": "方块 Z 坐标（世界坐标）"},
            },
            "required": ["x", "y", "z"],
        },
        handler=dig_block,
        params_model=DigBlockParams,
    ))
    registry.register(ToolSpec(
        name=COLLECT_BLOCK_TOOL,
        description=TOOL_HINTS[COLLECT_BLOCK_TOOL],
        parameters={
            "type": "object",
            "properties": {
                "block_ids": {
                    "type": "array", "items": {"type": "string"},
                    "minItems": 1, "maxItems": 16,
                    "description": "方块 registry 名或 #tag（如 #minecraft:logs）；"
                                   "同一物品的全部变体都列上",
                },
                "count": {"type": "integer", "minimum": 1, "maximum": 64,
                          "description": "要挖除的目标方块数"},
                "pickup": {
                    "type": "boolean",
                    "description": "挖掉方块后是否走过去捡起匹配的掉落物"
                                   "（默认 true）；要获得目标物品时用默认，"
                                   "挖通道/清理地形等不要掉落物时传 false",
                },
            },
            "required": ["block_ids", "count"],
        },
        handler=collect_block,
        params_model=CollectBlockParams,
    ))
    registry.register(ToolSpec(
        name=PICKUP_TOOL,
        description=TOOL_HINTS[PICKUP_TOOL],
        parameters={
            "type": "object",
            "properties": {
                "item_ids": {
                    "type": "array", "items": {"type": "string"},
                    "minItems": 1, "maxItems": 8,
                    "description": "要捡的物品注册名（最多 8 个，如 minecraft:oak_log）；"
                                   "缺省 = 捡 radius 范围内全部掉落"
                                   "（多人服只对自己活动的掉落这么用）",
                },
                "radius": {
                    "type": "integer", "minimum": 1, "maximum": 32,
                    "description": "搜索半径（格），默认 12",
                },
            },
            "required": [],
        },
        handler=pickup,
        params_model=PickupParams,
    ))
    registry.register(ToolSpec(
        name=COMMAND_TOOL,
        description=TOOL_HINTS[COMMAND_TOOL],
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要发送的聊天文本（/ 开头即游戏命令）"},
            },
            "required": ["text"],
        },
        handler=_handle_command,
        params_model=CommandToolParams,
    ))
    registry.register(ToolSpec(
        name=FINISH_TOOL,
        description=TOOL_HINTS[FINISH_TOOL],
        parameters={
            "type": "object",
            "properties": {
                "result": {"type": "string", "description": "任务结束语（在游戏聊天里播报）"},
            },
            "required": ["result"],
        },
        handler=_handle_finish,
        params_model=FinishToolParams,
    ))
    return registry


# 参数校验异常的紧凑文本（循环把 ValidationError 翻译成模型可读的回填）
def validation_error_text(name: str, exc: ValidationError) -> str:
    errors = "; ".join(
        f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
        for err in exc.errors(include_url=False)
    )
    return f"工具 {name} 参数校验失败：{errors}"


__all__ = [
    "BRIDGE_WHITELIST",
    "COLLECT_BLOCK_TOOL",
    "COMMAND_TOOL",
    "DIG_BLOCK_TOOL",
    "FINISH_TOOL",
    "PICKUP_TOOL",
    "PRIMITIVE_TOOL_HINTS",
    "SCHEMA_TOOLS_DIR",
    "TOOL_HINTS",
    "WALK_TO_TOOL",
    "CollectBlockParams",
    "DigBlockParams",
    "PickupParams",
    "ToolHandler",
    "ToolOutcome",
    "ToolRegistry",
    "ToolSpec",
    "UnknownToolError",
    "WalkToParams",
    "compact_json",
    "default_registry",
    "load_schema_parameters",
    "validation_error_text",
]
