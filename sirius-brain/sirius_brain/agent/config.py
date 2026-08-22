"""Agent 配置：VLM 客户端 + bridge + 循环预留。spec §10.1 M3-A。

M3 三裁决（2026-08-19）：智能全在 brain——本包是大脑侧"眼-思维"接口的配置层。

三层结构（全部 dataclass，风格对齐 ``bridge/config.py``）：
- ``VLMConfig``：qwen3.7-plus 经 DashScope OpenAI 兼容模式的连接/采样/重试参数。
  key 只从 local.md 的 ```env 围栏块或环境变量来（gitignored），任何入库文件严禁出现
- ``BridgeConfig``：直接复用 ``sirius_brain.bridge`` 的既有配置类（本文件不重复定义）
- ``LoopConfig``：M3-B 工具循环的预留参数（max_steps 等，本任务只占位）

装载来源（优先级：显式传参 > local.md env 块 / 环境变量）：
- ``AgentConfig.from_local_md(path)``：解析 local.md **首个** ```env 围栏块
  （``SIRIUS_VLM_*`` 键进 VLMConfig；块内若出现 ``SIRIUS_BRIDGE_URL``/``_TOKEN``
  顺带进 BridgeConfig，其余键忽略——前向兼容）
- ``AgentConfig.from_env()``：同名环境变量回退（``os.environ`` 里的 ``SIRIUS_VLM_*``
  + ``BridgeConfig.from_env()`` 的 ``SIRIUS_BRIDGE_*``）
- 两者都接受显式 ``vlm=``/``bridge=``/``loop=`` 整体覆盖显式传入的配置对象
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from sirius_brain.bridge.config import BridgeConfig

# local.md env 围栏块的 VLM 键前缀（机器可读配置块约定，见 local.template.md）
VLM_ENV_PREFIX = "SIRIUS_VLM_"

# ```env 围栏块：信息串标签（```env 后可跟空白），正文非贪婪到闭合围栏；
# re.search 天然"多块取首个"
_ENV_FENCE_RE = re.compile(r"```env[^\S\n]*\r?\n(.*?)```", re.DOTALL)

# 块内/环境变量名 → VLMConfig 字段（前缀剥掉后）
_VLM_ENV_FIELDS = {
    "BASE_URL": "base_url",
    "API_KEY": "api_key",
    "MODEL": "model",
    "ENABLE_THINKING": "enable_thinking",
    "REASONING_EFFORT": "reasoning_effort",
    "PROXY": "proxy",
    "TEMPERATURE": "temperature",
    "MAX_TOKENS": "max_tokens",
    "RETRIES": "retries",
    "TIMEOUT": "timeout",
}

# 块内 bridge 键（顺带支持；缺省全走 BridgeConfig 默认值）
_BRIDGE_ENV_FIELDS = {
    "SIRIUS_BRIDGE_URL": "url",
    "SIRIUS_BRIDGE_TOKEN": "token",
}


def parse_env_fenced_block(text: str) -> dict[str, str]:
    """解析文本中**首个** ```env 围栏块为 ``{KEY: VALUE}``。

    - 无 env 围栏块返回 ``{}``（由调用方决定算不算错误）
    - 注释行（``#`` 开头）与空行跳过；不含 ``=`` 的行跳过
    - 值按首个 ``=`` 切分（值本身可含 ``=``）；首尾空白剥掉
    - 文本其余部分（含中文正文、其他围栏块）不影响解析
    """
    match = _ENV_FENCE_RE.search(text)
    if match is None:
        return {}
    pairs: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def _parse_bool(raw: str, name: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{name}={raw!r} 不是合法布尔值（true/false/1/0/yes/no）")


@dataclass
class VLMConfig:
    """VLM 客户端配置（DashScope OpenAI 兼容模式 / qwen3.7-plus 配方）。

    - ``enable_thinking``：请求体**根级**参数（非 message 内），False 关闭思考——
      local.md 实测配方（DashScope qwen3 系；LM Studio 本地模型会静默忽略此参数）
    - ``reasoning_effort``：OpenAI 风格根级参数（"none" 关思考）。本地 LM Studio
      实测唯一有效的思考开关（enable_thinking/chat_template_kwargs//no_think 均无效，
      见 local.md「本地 LLM」节）；None = 不下发
    - ``proxy``：None/空串 = 国内直连（调用窗口内清空代理环境变量 + NO_PROXY=*）；
      填了代理 URL 则走它
    - ``temperature`` / ``max_tokens``：None = 不下发（用服务端默认值）
    - ``retries``：初试失败后的最多重试次数（429/5xx/网络错误；401/400 不重试）
    """

    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = ""
    model: str = "qwen3.7-plus"
    enable_thinking: bool = False
    reasoning_effort: str | None = None
    proxy: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    retries: int = 3
    # 单次 HTTP 请求超时（秒）——图片请求体大、VLM 生成慢，默认放宽
    timeout: float = 120.0

    def __post_init__(self) -> None:
        if not (self.base_url.startswith("http://") or self.base_url.startswith("https://")):
            raise ValueError(f"base_url 必须以 http:// 或 https:// 开头，got {self.base_url!r}")
        if self.proxy == "":
            self.proxy = None  # 空串视同直连（env 块 SIRIUS_VLM_PROXY= 留空的惯例）
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError(f"temperature 须在 [0, 2]，got {self.temperature}")
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError(f"max_tokens 须为正整数，got {self.max_tokens}")
        if self.retries < 0:
            raise ValueError(f"retries 须 >= 0，got {self.retries}")
        if self.timeout <= 0:
            raise ValueError(f"timeout 须为正数，got {self.timeout}")

    @property
    def chat_completions_url(self) -> str:
        """POST 目标：``{base_url}/chat/completions``。"""
        return self.base_url.rstrip("/") + "/chat/completions"

    @classmethod
    def from_mapping(cls, pairs: Mapping[str, str],
                     prefix: str = VLM_ENV_PREFIX) -> "VLMConfig":
        """从 ``{SIRIUS_VLM_*: 值}`` 映射构造（env 围栏块与 os.environ 同一入口）。

        - 只认 ``_VLM_ENV_FIELDS`` 列出的键，其余同前缀键忽略（前向兼容）
        - 解析失败抛 ValueError（带键名），绝不静默吞错
        """
        overrides: dict = {}
        for suffix, field_name in _VLM_ENV_FIELDS.items():
            raw = pairs.get(f"{prefix}{suffix}")
            if raw is None:
                continue
            try:
                if field_name == "enable_thinking":
                    overrides[field_name] = _parse_bool(raw, f"{prefix}{suffix}")
                elif field_name == "temperature":
                    overrides[field_name] = float(raw)
                elif field_name == "max_tokens":
                    overrides[field_name] = int(raw)
                elif field_name == "retries":
                    overrides[field_name] = int(raw)
                elif field_name == "timeout":
                    overrides[field_name] = float(raw)
                else:
                    overrides[field_name] = raw
            except ValueError as exc:
                raise ValueError(f"{prefix}{suffix}={raw!r} 解析失败：{exc}") from exc
        return cls(**overrides)

    def with_overrides(self, **changes) -> "VLMConfig":
        """返回局部覆盖后的新配置（不改自身；值为 None 的参数不覆盖）。"""
        applied = {k: v for k, v in changes.items() if v is not None}
        unknown = set(applied) - {f.name for f in fields(self)}
        if unknown:
            raise ValueError(f"未知 VLM 配置字段 {sorted(unknown)}")
        return replace(self, **applied)


@dataclass
class LoopConfig:
    """M3-B 工具循环的预留参数（本任务只占位，循环本身见 M3-B）。"""

    # 单轮任务里 VLM 调用 + 工具执行的最大步数（防失控烧 token）
    max_steps: int = 25
    # 连续两次 VLM 调用之间的最小间隔（秒）；0 = 不限
    min_interval: float = 0.0
    # 单任务 token 预算（按各步 VLM usage.total_tokens 累计）。
    # M3.5（2026-08-20）200k→500k：任务级原语下沉后单任务的 VLM 调用数骤降
    # （M3 砍树 22 步 → 目标 ≤4 步），200k 会在复杂探索任务上误伤；500k 作为
    # 复杂探索任务的硬上限，仍能兜住失控循环
    max_total_tokens: int = 500_000
    # M4 反射等级默认值：observer（L0 关动作不关感知）/ self_preserve（L1 默认，
    # 七条反射全开）/ guard（L2 预留枚举位，配置了也只在切换时被拒）。
    # 聊天切换命令只改内存不落盘——重启回这里的默认值（刻意：简单优先）
    reflex_level: str = "self_preserve"
    # M4 反射调度器轮询间隔（秒）：Numen CompanionBrain 的 0.5s 移植值
    reflex_poll_interval: float = 0.5

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError(f"max_steps 须 >= 1，got {self.max_steps}")
        if self.min_interval < 0:
            raise ValueError(f"min_interval 须 >= 0，got {self.min_interval}")
        if self.max_total_tokens <= 0:
            raise ValueError(f"max_total_tokens 须 > 0，got {self.max_total_tokens}")
        if self.reflex_level not in ("observer", "self_preserve", "guard"):
            raise ValueError(
                f"reflex_level 须为 observer/self_preserve/guard，got {self.reflex_level!r}")
        if self.reflex_poll_interval <= 0:
            raise ValueError(
                f"reflex_poll_interval 须为正数，got {self.reflex_poll_interval}")


@dataclass
class AgentConfig:
    """M3 整机大脑的聚合配置：VLM + bridge + 循环。"""

    vlm: VLMConfig = field(default_factory=VLMConfig)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)

    @classmethod
    def from_local_md(cls, path: str | Path, *,
                      vlm: VLMConfig | None = None,
                      bridge: BridgeConfig | None = None,
                      loop: LoopConfig | None = None) -> "AgentConfig":
        """从 local.md（或同构文件）装载：首个 ```env 围栏块。

        - 文件不存在 → ``FileNotFoundError``（路径写错要立刻暴露）
        - 无 env 围栏块 / 块内无任何 ``SIRIUS_VLM_*`` 键 → ``ValueError``
          （VLM 未配置宁可报错，不静默用空 key 撞 401）
        - ``vlm`` / ``bridge`` / ``loop`` 显式传入时整体替代装载结果
          （显式传参优先于文件；测试与 M3-B 局部换档用）
        """
        file_path = Path(path)
        pairs = parse_env_fenced_block(file_path.read_text(encoding="utf-8"))
        if not pairs:
            raise ValueError(
                f"{file_path} 中未找到 ```env 围栏块（VLM 配置来源，格式见 local.template.md）")
        if not any(key.startswith(VLM_ENV_PREFIX) for key in pairs):
            raise ValueError(f"{file_path} 的 env 围栏块中没有 {VLM_ENV_PREFIX}* 配置")
        vlm_config = vlm if vlm is not None else VLMConfig.from_mapping(pairs)
        if bridge is not None:
            bridge_config = bridge
        else:
            overrides = {field_name: pairs[env_name]
                         for env_name, field_name in _BRIDGE_ENV_FIELDS.items()
                         if pairs.get(env_name)}
            bridge_config = BridgeConfig(**overrides)
        return cls(vlm=vlm_config, bridge=bridge_config,
                   loop=loop if loop is not None else LoopConfig())

    @classmethod
    def from_env(cls, *,
                 vlm: VLMConfig | None = None,
                 bridge: BridgeConfig | None = None,
                 loop: LoopConfig | None = None) -> "AgentConfig":
        """从环境变量装载（local.md 不可用时的同名回退）。

        ``SIRIUS_VLM_*`` → VLMConfig（未设的键用默认值）；bridge 走
        ``BridgeConfig.from_env()``（``SIRIUS_BRIDGE_*``）。显式传参优先。
        """
        vlm_config = vlm if vlm is not None else VLMConfig.from_mapping(os.environ)
        bridge_config = bridge if bridge is not None else BridgeConfig.from_env()
        return cls(vlm=vlm_config, bridge=bridge_config,
                   loop=loop if loop is not None else LoopConfig())
