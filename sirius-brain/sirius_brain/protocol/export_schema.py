"""协议 JSON Schema 导出：pydantic 模型 → schema/ 产物，供 Java 侧（sirius-bridge）直接消费。

产物布局（默认输出到仓库内 ``sirius-brain/schema/``，需提交进版本库）::

    schema/
      index.json                      # 汇总索引：全部文件清单 + 协议版本 + 导出时间
      frames/<FrameName>.json         # 信封帧 / NEKO 兼容帧（每个模型一个文件）
      tools/<method>.json             # 工具调用 params 契约（方法名即文件名，含 '.'）
      tasks/<ModelName>.json          # 大脑内部任务卡 / 执行器报告（spec §5）

自包含保证：每个文件都是一份独立完整的 draft 2020-12 文档——嵌套模型/枚举全部内联进
该文件的 ``$defs``，``$ref`` 一律是同文件片段（``#/$defs/...``），无跨文件引用，
everit-org/json-schema 与 networknt 均可单文件加载校验。

CLI::

    .venv\\Scripts\\python.exe -m sirius_brain.protocol.export_schema [--output DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from . import frames, tasks
from .tools import TOOL_PARAMS

#: 协议版本（capabilities/list 协商用，见 mock 与 index.json）
#: v1.1（M3.5）：world.query 加可选 filter、input.click 加可选 hold_ms（均向后兼容）
#: v1.2（M3.5 T6）：新增 dig 智能挖掘原语、lookAt 加可选 turn_speed_deg_s 平滑转头
#: v1.3（M4.1）：新增 chat.send 直发聊天（绕开 T 键 GUI）、getStats 增报 yaw/pitch
PROTOCOL_VERSION = "1.3"

#: JSON Schema 方言（pydantic v2 默认输出 draft 2020-12）
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

#: 信封帧模型（后端 ↔ Mod 线上协议，spec §8.2）
FRAME_MODELS: dict[str, type[BaseModel]] = {
    "ToolCallRequest": frames.ToolCallRequest,
    "ToolCallResponse": frames.ToolCallResponse,
    "ToolCallError": frames.ToolCallError,
    "NotificationFrame": frames.NotificationFrame,
    "Capability": frames.Capability,
    "CapabilitiesListRequest": frames.CapabilitiesListRequest,
    "CapabilitiesListResponse": frames.CapabilitiesListResponse,
    "TaskFrame": frames.TaskFrame,
    "TaskFinishedFrame": frames.TaskFinishedFrame,
}

#: 大脑内部协议模型（任务卡 / 执行器报告，spec §5、§4.2）
TASK_MODELS: dict[str, type[BaseModel]] = {
    "TaskCard": tasks.TaskCard,
    "ReportDone": tasks.ReportDone,
    "ReportBlocked": tasks.ReportBlocked,
    "RequestDecision": tasks.RequestDecision,
    "ReportProgress": tasks.ReportProgress,
}

#: 工具方法 → 参数模型（来自 tools.TOOL_PARAMS 注册表；文件名 = 方法名 + .json）
TOOL_METHODS: dict[str, type[BaseModel]] = dict(TOOL_PARAMS)

#: 分类 → 模型注册表（决定子目录与文件命名）
CATEGORIES: dict[str, dict[str, type[BaseModel]]] = {
    "frames": FRAME_MODELS,
    "tools": TOOL_METHODS,
    "tasks": TASK_MODELS,
}

#: 默认输出目录：sirius-brain/schema/（仓库内，Java 侧直接从仓库读）
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "schema"


def _assert_self_contained(schema: dict[str, Any], where: str) -> None:
    """断言 schema 文档内所有 $ref 都是同文件片段引用（# 开头），无外部依赖。"""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                raise ValueError(f"{where}: 非自包含 $ref {ref!r}")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def _with_dialect(schema: dict[str, Any]) -> dict[str, Any]:
    """置入首位的 $schema 方言声明（pydantic 2.13 的 model_json_schema 不输出该键）。

    显式声明后：Python jsonschema.validator_for 自动选 Draft202012Validator；
    networknt 可用 SpecVersionDetector 自动识别；everit-org 不认识的方言 URI 仅被
    忽略（prefixItems 按未知关键字处理，校验偏宽但不会报错）。
    """
    out = {"$schema": SCHEMA_DIALECT}
    out.update(schema)
    return out


def _dump(schema: dict[str, Any]) -> str:
    """确定性序列化：缩进 2、保留非 ASCII、结尾换行。"""
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def export_all(out_dir: Path) -> list[Path]:
    """导出全部 schema 文件到 out_dir，返回写入文件列表（含 index.json）。"""
    out_dir = Path(out_dir)
    written: list[Path] = []
    category_files: dict[str, dict[str, str]] = {}

    for category, models in CATEGORIES.items():
        category_files[category] = {}
        cat_dir = out_dir / category
        for name, model in models.items():
            schema = _with_dialect(model.model_json_schema())
            _assert_self_contained(schema, f"{category}/{name}")
            rel = f"{category}/{name}.json"
            path = out_dir / f"{rel}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_dump(schema), encoding="utf-8")
            written.append(path)
            category_files[category][name] = rel

    index = {
        "protocol_version": PROTOCOL_VERSION,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "schema_dialect": SCHEMA_DIALECT,
        "generator": "sirius_brain.protocol.export_schema",
        "categories": category_files,
        "files": sorted(str(p.relative_to(out_dir)).replace("\\", "/") for p in written),
        "notes": (
            "每个文件自包含（$defs 内联、$ref 均为 #/ 片段），Java 侧可单文件加载；"
            "tools/<method>.json 描述该工具 ToolCallRequest.params 的契约；"
            "NEKO 兼容帧见 frames/TaskFrame.json 与 frames/TaskFinishedFrame.json"
        ),
    }
    index_path = out_dir / "index.json"
    index_path.write_text(_dump(index), encoding="utf-8")
    written.append(index_path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sirius_brain.protocol.export_schema",
        description="把 protocol/ 的 pydantic 模型冻结为 JSON Schema（Java 侧直接消费）",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录（默认：{DEFAULT_OUTPUT_DIR}）",
    )
    args = parser.parse_args(argv)

    written = export_all(args.output)
    print(f"协议版本 {PROTOCOL_VERSION} · 方言 {SCHEMA_DIALECT}")
    print(f"已写入 {len(written)} 个文件到 {Path(args.output).resolve()}")
    for path in written:
        print(f"  {path.relative_to(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
