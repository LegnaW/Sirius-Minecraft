"""协议信封帧模型：工具调用（请求-响应）、事件推送、版本协商、NEKO 兼容帧。spec §8.2。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .enums import TaskFinishedStatus


class ToolCallRequest(BaseModel):
    """后端 → Mod 工具调用请求帧。spec §8.2：请求-响应，JSON Schema 参数校验。"""

    type: Literal["request"] = "request"
    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class ToolCallError(BaseModel):
    """工具调用错误对象。spec §8.2。"""

    code: int
    message: str
    data: Any | None = None


class ToolCallResponse(BaseModel):
    """Mod → 后端工具调用响应帧（与请求 id 配对）。spec §8.2。"""

    type: Literal["response"] = "response"
    id: str
    result: Any | None = None
    error: ToolCallError | None = None


class NotificationFrame(BaseModel):
    """Mod → 后端事件推送帧（一等公民，主动唤醒 agent）。spec §8.2。"""

    type: Literal["notification"] = "notification"
    event: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: float
    seq: int = Field(ge=0)


class Capability(BaseModel):
    """单项能力描述（capabilities/list 响应成员）。spec §8.2。"""

    name: str
    version: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesListRequest(BaseModel):
    """后端 → Mod capabilities/list 请求（发现 + 版本协商）。spec §8.2。"""

    type: Literal["request"] = "request"
    id: str
    method: Literal["capabilities/list"] = "capabilities/list"
    params: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesListResponse(BaseModel):
    """capabilities/list 响应：能力清单 + 协议版本。spec §8.2。"""

    type: Literal["response"] = "response"
    id: str
    result: list[Capability]
    protocol_version: str
    error: ToolCallError | None = None


class TaskFrame(BaseModel):
    """NEKO 兼容：后端 → Mod 任务帧 {type:"task", task, task_id}。spec §8.2。"""

    type: Literal["task"] = "task"
    task: str
    task_id: str


class TaskFinishedFrame(BaseModel):
    """NEKO 兼容：Mod → 后端任务完成帧。task_id 必须原样回传（否则 out-of-order 完成会错误归属）。spec §8.2。"""

    type: Literal["task_finished"] = "task_finished"
    status: TaskFinishedStatus
    task_id: str
    text: str


class HelloAckFrame(BaseModel):
    """握手回应帧：Mod → 后端 ``{"type":"hello_ack","ok":true,"protocol_version":...}``。

    真机服务端 M1 起就回（Json.helloAck 实况），客户端此前当未知帧忽略并告警——
    M3.6 补进协议建模（T2）。注意配对的 hello 请求帧仍定义在 bridge/client.py
    （握手不属于工具调用协议，schema 导出不覆盖握手帧）。
    """

    type: Literal["hello_ack"] = "hello_ack"
    ok: bool
    protocol_version: str
