"""Bridge 客户端连接配置。spec §8.2（token 安全模型）、§10.1 M1-D。

一个 dataclass 覆盖三类配置：
- 连接目标：url / token / protocol_version（token 供 hello 握手，真 Mod 要求，mock 忽略）
- 超时：connect_timeout（TCP+WebSocket 建连）/ request_timeout（单次 RPC 默认值）/
  hello_timeout（等待 hello 回应的 best-effort 上限）
- 重连策略：max_reconnects（None = 无限）/ reconnect_base_delay（指数退避基数）/
  reconnect_max_delay（退避上限）

支持两种外部装载方式（可组合，调用方自行决定覆盖顺序）：
- ``BridgeConfig.from_json_file(path)``：JSON 文件，键 = 字段名
- ``BridgeConfig.from_env(prefix)``：环境变量 ``SIRIUS_BRIDGE_URL`` 等
"""

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path

# 环境变量名 → 字段名（前缀拼接后全大写）
_ENV_FIELDS = {
    "URL": "url",
    "TOKEN": "token",
    "PROTOCOL_VERSION": "protocol_version",
    "CONNECT_TIMEOUT": "connect_timeout",
    "REQUEST_TIMEOUT": "request_timeout",
    "HELLO_TIMEOUT": "hello_timeout",
    "MAX_RECONNECTS": "max_reconnects",
    "RECONNECT_BASE_DELAY": "reconnect_base_delay",
    "RECONNECT_MAX_DELAY": "reconnect_max_delay",
}


@dataclass
class BridgeConfig:
    """Bridge 客户端连接配置（值均为纯数据，可 dataclasses.replace 局部覆盖）。"""

    url: str = "ws://127.0.0.1:8765"
    # token 握手：spec §8.2 安全模型。真 Mod 要求首条消息 hello；mock 不校验。
    # None = 不发送 hello。
    token: str | None = None
    # 与 Mod 侧 Capabilities.PROTOCOL_VERSION 保持同步（M4.1 起为 1.3：
    # chat.send 直发聊天 + getStats 增报 yaw/pitch；1.2 = dig 原语 + lookAt
    # turn_speed_deg_s）
    protocol_version: str = "1.3"
    connect_timeout: float = 10.0
    request_timeout: float = 30.0
    hello_timeout: float = 2.0
    # 重连：None = 无限重试；退避 = base * 2^(n-1)，封顶 max_delay
    max_reconnects: int | None = 3
    reconnect_base_delay: float = 0.5
    reconnect_max_delay: float = 30.0

    def __post_init__(self) -> None:
        if not (self.url.startswith("ws://") or self.url.startswith("wss://")):
            raise ValueError(f"url 必须以 ws:// 或 wss:// 开头，got {self.url!r}")
        if self.token == "":
            self.token = None  # 空串视同未配置（环境变量常见）
        for name in ("connect_timeout", "request_timeout", "hello_timeout",
                     "reconnect_base_delay", "reconnect_max_delay"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须为正数，got {getattr(self, name)}")
        if self.max_reconnects is not None and self.max_reconnects < 0:
            raise ValueError(f"max_reconnects 须 >= 0 或 None，got {self.max_reconnects}")

    # ------------------------------------------------------------------ 装载

    @classmethod
    def from_json_file(cls, path: str | Path) -> "BridgeConfig":
        """从 JSON 文件加载（UTF-8，键 = 字段名，未知键报错防拼写错）。

        文件示例::

            {"url": "ws://127.0.0.1:8765", "token": "s3cret",
             "request_timeout": 10, "max_reconnects": 5}
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"配置文件必须是 JSON 对象：{path}")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"配置文件含未知字段 {sorted(unknown)}（可用字段：{sorted(known)}）")
        return cls(**data)

    @classmethod
    def from_env(cls, prefix: str = "SIRIUS_BRIDGE_") -> "BridgeConfig":
        """从环境变量加载：``<prefix>URL`` / ``<prefix>TOKEN`` / ``<prefix>REQUEST_TIMEOUT`` …

        - 只认 _ENV_FIELDS 列出的变量，其余同前缀变量忽略（前向兼容）
        - TOKEN 为空串视同未配置；MAX_RECONNECTS 支持 "none"（无限重连）
        - 解析失败抛 ValueError（带变量名）
        """
        overrides: dict = {}
        for suffix, field_name in _ENV_FIELDS.items():
            raw = _read_env(f"{prefix}{suffix}")
            if raw is None:
                continue
            current_type = type(getattr(cls(), field_name))
            try:
                if field_name == "token":
                    overrides[field_name] = raw or None
                elif field_name == "max_reconnects":
                    overrides[field_name] = None if raw.lower() in ("none", "inf") \
                        else int(raw)
                elif current_type is float:
                    overrides[field_name] = float(raw)
                else:
                    overrides[field_name] = raw
            except ValueError as exc:
                raise ValueError(f"环境变量 {prefix}{suffix}={raw!r} 解析失败：{exc}") from exc
        return cls(**overrides)

    # ------------------------------------------------------------------ 工具

    def with_overrides(self, **changes) -> "BridgeConfig":
        """返回局部覆盖后的新配置（不改自身；值为 None 的参数不覆盖）。"""
        applied = {k: v for k, v in changes.items() if v is not None}
        unknown = set(applied) - {f.name for f in fields(self)}
        if unknown:
            raise ValueError(f"未知配置字段 {sorted(unknown)}")
        return replace(self, **applied)


def _read_env(name: str) -> str | None:
    """os.environ.get 包装（隔离 import 便于测试 monkeypatch）。"""
    import os

    return os.environ.get(name)
