"""信封帧与工具参数测试。spec §8.2。"""

import pytest
from pydantic import ValidationError

from sirius_brain.protocol import (
    CapabilitiesListRequest,
    CapabilitiesListResponse,
    Capability,
    HelloAckFrame,
    NotificationFrame,
    ScreenshotParams,
    TaskFinishedFrame,
    TaskFinishedStatus,
    TaskFrame,
    TextParams,
    ToolCallError,
    ToolCallRequest,
    ToolCallResponse,
    WorldQueryParams,
)


class TestRoundTrip:
    """model_dump → model_validate 往返相等。"""

    @pytest.mark.parametrize(
        "model,instance",
        [
            (ToolCallRequest, ToolCallRequest(id="1", method="screenshot",
                                              params={"tier": "full"})),
            (ToolCallResponse, ToolCallResponse(id="1", result={"ok": True})),
            (ToolCallResponse, ToolCallResponse(
                id="1", error=ToolCallError(code=-32602, message="bad params"))),
            (NotificationFrame, NotificationFrame(
                event="health", data={"health": 6}, timestamp=1e9, seq=42)),
            (CapabilitiesListRequest, CapabilitiesListRequest(id="2")),
            (CapabilitiesListResponse, CapabilitiesListResponse(
                id="2", result=[Capability(name="screenshot", version="1.0",
                                           input_schema={"type": "object"})],
                protocol_version="1.0")),
            (TaskFrame, TaskFrame(task="挖一组铁矿石", task_id="T-42")),
            (TaskFinishedFrame, TaskFinishedFrame(
                status=TaskFinishedStatus.SUPERSEDED, task_id="T-42", text="被顶替")),
            # M3.6 T2：hello_ack 握手回应（真机 Mod 连接后首条入站帧的实况形态）
            (HelloAckFrame, HelloAckFrame(ok=True, protocol_version="1.2")),
        ],
    )
    def test_round_trip(self, model, instance):
        assert model.model_validate(instance.model_dump()) == instance

    def test_hello_ack_wire_format(self):
        """M3.6 T2 防漂移：字段名/取值对照真机 Json.helloAck 的线上 JSON——
        {"type":"hello_ack","ok":true,"protocol_version":"1.2"}，绝不多一个字段。"""
        d = HelloAckFrame(ok=True, protocol_version="1.2").model_dump()
        assert set(d) == {"type", "ok", "protocol_version"}
        assert d == {"type": "hello_ack", "ok": True, "protocol_version": "1.2"}
        # 线上实况原文直接过模（防字段改名/收紧）
        wire = ('{"type":"hello_ack","ok":true,"protocol_version":"1.0"}')
        frame = HelloAckFrame.model_validate_json(wire)
        assert frame.ok is True and frame.protocol_version == "1.0"
        with pytest.raises(ValidationError):
            HelloAckFrame.model_validate({"type": "hello_ack"})  # 缺 ok/protocol_version

    def test_notification_wire_format(self):
        """字段名与线上 JSON 一致：{type,event,data,timestamp,seq}。"""
        d = NotificationFrame(event="chat", data={}, timestamp=1.0, seq=0).model_dump()
        assert set(d) == {"type", "event", "data", "timestamp", "seq"}
        assert d["type"] == "notification"

    def test_json_round_trip(self):
        req = ToolCallRequest(id="9", method="input.text", params={"string": "hi"})
        assert ToolCallRequest.model_validate_json(req.model_dump_json()) == req


class TestValidation:
    def test_request_requires_id_and_method(self):
        with pytest.raises(ValidationError):
            ToolCallRequest(id="1")  # type: ignore[call-arg]

    def test_notification_requires_event_timestamp_seq(self):
        with pytest.raises(ValidationError):
            NotificationFrame(event="chat")  # type: ignore[call-arg]

    def test_capabilities_method_frozen(self):
        with pytest.raises(ValidationError):
            CapabilitiesListRequest(id="1", method="other/list")

    def test_screenshot_tier_invalid(self):
        with pytest.raises(ValidationError):
            ScreenshotParams(tier="huge")

    def test_screenshot_valid(self):
        p = ScreenshotParams(tier="crop", bbox=(0, 0, 100, 100), quality=80)
        assert ScreenshotParams.model_validate(p.model_dump()) == p

    def test_world_query_type_invalid(self):
        with pytest.raises(ValidationError):
            WorldQueryParams(type="chunks", range=32)

    def test_text_missing_string(self):
        with pytest.raises(ValidationError):
            TextParams()  # type: ignore[call-arg]
