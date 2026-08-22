"""Schema 导出产物测试。spec §8.2 / §10.1 M0：pydantic 模型 + JSON Schema 双产出。

验证三件事：
1. 产物完整且合法：每个文件是合法 JSON、是合法 draft 2020-12 schema、自包含（$ref 全为 #/ 片段）
2. 语义等价：示例帧（取自 pydantic 模型实例的 JSON 序列化）逐个对 schema 校验通过；非法样例被拒
3. 仓库内 schema/ 与代码再导出保持同步（防"改了模型忘重导出"）
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for

from sirius_brain.protocol import (
    CapabilitiesListRequest,
    CapabilitiesListResponse,
    Capability,
    ClickParams,
    EventsSubscribeParams,
    EventsWatchParams,
    GetGuiStateParams,
    GetStatsParams,
    KeyParams,
    LookAtParams,
    LookParams,
    MouseMoveParams,
    NotificationFrame,
    ReportBlocked,
    ReportDone,
    ReportProgress,
    RequestDecision,
    ScreenshotParams,
    TaskCard,
    TaskFinishedFrame,
    TaskFrame,
    TextParams,
    ToolCallError,
    ToolCallRequest,
    ToolCallResponse,
    WorldQueryParams,
)
from sirius_brain.protocol.export_schema import (
    CATEGORIES,
    DEFAULT_OUTPUT_DIR,
    PROTOCOL_VERSION,
    SCHEMA_DIALECT,
    export_all,
)


def dump(model_instance):
    """pydantic 实例 → 线上 JSON 形态（tuple 转 list 等）。"""
    return json.loads(model_instance.model_dump_json())


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """全量导出到临时目录，本模块所有断言基于这份新导出。"""
    out = tmp_path_factory.mktemp("schema")
    export_all(out)
    return out


def load_schema(exported: Path, rel: str) -> dict:
    return json.loads((exported / rel).read_text(encoding="utf-8"))


def schema_files(exported: Path) -> list[Path]:
    return sorted(p for p in exported.rglob("*.json") if p.name != "index.json")


def expected_rel_files() -> list[str]:
    """注册表推导出的相对路径清单（收集期可用，不依赖 fixture）。"""
    return [f"{cat}/{name}.json" for cat, models in CATEGORIES.items() for name in models]


# ---------------------------------------------------------------- 产物完整性

def test_expected_file_layout(exported):
    """文件集 = 注册表推导集 + index.json；工具文件名 = 方法名。"""
    expected = {"index.json"} | set(expected_rel_files())
    actual = {
        str(p.relative_to(exported)).replace("\\", "/") for p in exported.rglob("*.json")
    }
    assert actual == expected
    # 工具方法名含 '.' 也原样作为文件名（目录即查找规则）
    for method in CATEGORIES["tools"]:
        assert (exported / "tools" / f"{method}.json").is_file()


@pytest.mark.parametrize("rel", expected_rel_files())
class TestEachSchemaFile:
    def test_valid_json_valid_schema_declares_dialect(self, exported, rel):
        schema = load_schema(exported, rel)
        Draft202012Validator.check_schema(schema)  # 是合法 2020-12 schema
        assert schema["$schema"] == SCHEMA_DIALECT

    def test_self_contained_refs(self, exported, rel):
        schema = load_schema(exported, rel)

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref":
                        assert value.startswith("#"), f"{rel}: 跨文件 $ref {value}"
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(schema)


def test_index_json(exported):
    index = json.loads((exported / "index.json").read_text(encoding="utf-8"))
    assert index["protocol_version"] == PROTOCOL_VERSION == "1.3"  # M4.1 v1.3：chat.send + getStats yaw/pitch
    assert index["schema_dialect"] == SCHEMA_DIALECT
    from datetime import datetime

    datetime.fromisoformat(index["exported_at"].replace("Z", "+00:00"))  # 导出时间可解析
    assert set(index["files"]) == {
        str(p.relative_to(exported)).replace("\\", "/") for p in schema_files(exported)
    }
    for category, models in CATEGORIES.items():
        assert set(index["categories"][category]) == set(models)


# ---------------------------------------------------------------- 语义等价

# 示例帧：优先复用 tests/test_frames.py 的实例语义，再补齐全部工具/任务模型
FRAME_EXAMPLES = {
    "ToolCallRequest": [
        dump(ToolCallRequest(id="1", method="screenshot", params={"tier": "full"})),
        dump(ToolCallRequest(id="9", method="input.text", params={"string": "hi"})),
    ],
    "ToolCallResponse": [
        dump(ToolCallResponse(id="1", result={"ok": True})),
        dump(ToolCallResponse(id="1", error=ToolCallError(code=-32602, message="bad params"))),
    ],
    "ToolCallError": [dump(ToolCallError(code=-32700, message="parse error", data={"raw": "x"}))],
    "NotificationFrame": [
        dump(NotificationFrame(event="fire", data={"source": "creeper"}, timestamp=1e9, seq=0)),
    ],
    "Capability": [
        dump(Capability(name="screenshot", version="1.0", input_schema={"type": "object"})),
    ],
    "CapabilitiesListRequest": [dump(CapabilitiesListRequest(id="2"))],
    "CapabilitiesListResponse": [
        dump(CapabilitiesListResponse(
            id="2",
            result=[Capability(name="screenshot", version="1.0",
                               input_schema={"type": "object"})],
            protocol_version="1.0")),
    ],
    "TaskFrame": [dump(TaskFrame(task="挖一组铁矿石", task_id="T-42"))],
    "TaskFinishedFrame": [
        dump(TaskFinishedFrame(status=status, task_id="T-42", text="done"))
        for status in ("ok", "failed", "interrupted", "superseded", "timeout")
    ],
}

TOOL_EXAMPLES = {
    "screenshot": dump(ScreenshotParams(tier="crop", bbox=(0, 0, 100, 100), quality=80)),
    "look": dump(LookParams(yaw=90.0, pitch=-10.0)),
    "lookAt": dump(LookAtParams(x=1.5, y=64.0, z=-3.5)),
    "getGuiState": dump(GetGuiStateParams()),
    "world.query": dump(WorldQueryParams(type="entities", range=32.0)),
    "getStats": dump(GetStatsParams()),
    "input.mouseMove": dump(MouseMoveParams(x=400.0, y=300.0)),
    "input.click": dump(ClickParams(button=0, count=2)),
    "input.key": dump(KeyParams(code=87, duration_ms=50, modifiers=["shift"])),
    "input.text": dump(TextParams(string="你好")),
    "events.subscribe": dump(EventsSubscribeParams(types=["chat"], min_level="WARNING")),
    "events.watch": dump(EventsWatchParams(
        stat="health", condition="health < 8", hysteresis=1.0, cooldown_ms=500)),
}

TASK_EXAMPLES = {
    "TaskCard": dump(TaskCard(
        task_id="T-42",
        goal="清理矿井入口的骷髅群",
        success_criteria="nearest skeleton within 32 blocks == null && health > 10",
        constraints=["不许破坏方块", "血量<8立即撤退"],
        tools_allowlist=["!attack", "!goToCoordinates", "!stats", "!inventory"],
        interrupt_policy="deflect",
        timeout_mins=10,
        context=["<记忆检索 top-1>"],
    )),
    "ReportDone": dump(ReportDone(task_id="T-42", result="骷髅已清理", evidence="!stats 输出")),
    "ReportBlocked": dump(ReportBlocked(
        task_id="T-42", reason="骷髅在岩浆后，无法近战", observation="!nearbyBlocks 输出")),
    "RequestDecision": dump(RequestDecision(
        task_id="T-42", question="绕路还是撤退？", options=["绕路", "撤退"], default="撤退")),
    "ReportProgress": dump(ReportProgress(task_id="T-42", step="合成中", done=1, total=3)),
}


def _parametrized_examples():
    cases = []
    for rel_prefix, group in (("frames", FRAME_EXAMPLES), ("tasks", TASK_EXAMPLES)):
        for name, examples in group.items():
            if not isinstance(examples, list):
                examples = [examples]
            for i, example in enumerate(examples):
                cases.append((pytest.param(f"{rel_prefix}/{name}.json", example,
                                           id=f"{rel_prefix}.{name}[{i}]")))
    for method, example in TOOL_EXAMPLES.items():
        cases.append(pytest.param(f"tools/{method}.json", example, id=f"tools.{method}"))
    return cases


@pytest.mark.parametrize("rel,example", _parametrized_examples())
def test_example_validates_against_schema(exported, rel, example):
    schema = load_schema(exported, rel)
    validator = validator_for(schema)  # 依 $schema 自动选择校验器
    assert issubclass(validator, Draft202012Validator)  # 方言声明生效
    validator(schema).validate(example)


@pytest.mark.parametrize(
    "rel,bad",
    [
        # 枚举拒绝
        ("frames/TaskFinishedFrame.json",
         {"type": "task_finished", "status": "cancelled", "task_id": "T-1", "text": "x"}),
        ("tools/screenshot.json", {"tier": "huge"}),
        ("tools/world.query.json", {"type": "chunks", "range": 32}),
        ("tools/events.subscribe.json", {"types": ["chat"], "min_level": "DEBUG"}),
        # 数值边界拒绝
        ("tools/look.json", {"yaw": 200.0, "pitch": 0.0}),
        ("frames/NotificationFrame.json",
         {"type": "notification", "event": "chat", "data": {}, "timestamp": 1.0, "seq": -1}),
        ("tools/screenshot.json", {"tier": "full", "quality": 101}),
        # 必填缺失拒绝
        ("frames/TaskFrame.json", {"type": "task", "task": "挖矿"}),
        ("tools/input.text.json", {}),
        ("frames/TaskFinishedFrame.json",
         {"type": "task_finished", "status": "ok", "text": "没有 task_id"}),
    ],
)
def test_invalid_examples_rejected(exported, rel, bad):
    validator = Draft202012Validator(load_schema(exported, rel))
    with pytest.raises(Exception):
        validator.validate(bad)


# ---------------------------------------------------------------- 枚举完整性

def _enum_of(exported, rel, def_name):
    return set(load_schema(exported, rel)["$defs"][def_name]["enum"])


def test_task_finished_five_states(exported):
    assert _enum_of(exported, "frames/TaskFinishedFrame.json", "TaskFinishedStatus") == {
        "ok", "failed", "interrupted", "superseded", "timeout",
    }


def test_event_three_levels(exported):
    assert _enum_of(exported, "tools/events.subscribe.json", "EventLevel") == {
        "CRITICAL", "WARNING", "INFO",
    }


def test_interrupt_two_modes(exported):
    assert _enum_of(exported, "tasks/TaskCard.json", "InterruptPolicy") == {
        "cancel", "deflect",
    }


def test_screenshot_tiers(exported):
    assert _enum_of(exported, "tools/screenshot.json", "ScreenshotTier") == {"full", "crop"}


# ---------------------------------------------------------------- 与仓库产物同步

def test_committed_schema_in_sync(tmp_path):
    """仓库内 schema/ 必须与代码重导出一致（index 的 exported_at 除外）。"""
    assert DEFAULT_OUTPUT_DIR.is_dir(), f"仓库内 schema/ 不存在：{DEFAULT_OUTPUT_DIR}"
    export_all(tmp_path)
    committed = {str(p.relative_to(DEFAULT_OUTPUT_DIR)).replace("\\", "/")
                 for p in DEFAULT_OUTPUT_DIR.rglob("*.json")}
    fresh = {str(p.relative_to(tmp_path)).replace("\\", "/") for p in tmp_path.rglob("*.json")}
    assert committed == fresh, f"文件清单不同步：仅仓库有 {committed - fresh}，仅导出有 {fresh - committed}"
    for rel in sorted(fresh):
        a = json.loads((DEFAULT_OUTPUT_DIR / rel).read_text(encoding="utf-8"))
        b = json.loads((tmp_path / rel).read_text(encoding="utf-8"))
        if rel == "index.json":
            a.pop("exported_at")
            b.pop("exported_at")
        assert a == b, f"{rel} 与代码不同步：请重跑 python -m sirius_brain.protocol.export_schema"


# ---------------------------------------------------------------- CLI

def test_cli_export(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "sirius_brain.protocol.export_schema", "--output", str(tmp_path)],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent,
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "index.json").is_file()
    assert (tmp_path / "frames" / "TaskFinishedFrame.json").is_file()
    assert (tmp_path / "tools" / "world.query.json").is_file()
