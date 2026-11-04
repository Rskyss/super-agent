"""模型配置：包里给默认值，目标项目的 .collab/配置.json 可以逐项覆盖。

换模型只改这里，不改代码。
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MODELS = {
    "cursor": "auto",              # Cursor CLI（agent）
    "codex": "gpt-6.1-sol",        # Codex 测试员
    "codex_effort": "xhigh",       # Codex 推理强度
    "agy": "gemini-4-argon-high",  # agy 备选开发
}

CONFIG_FILE = "配置.json"


class ConfigError(Exception):
    pass


def load_models(project_dir) -> dict:
    """返回生效的模型配置：默认值 + 项目 .collab/配置.json 的覆盖。"""
    models = dict(DEFAULT_MODELS)
    path = Path(project_dir) / ".collab" / CONFIG_FILE
    if not path.exists():
        return models
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(overrides, dict):
        raise ConfigError(f"{path} 顶层必须是对象")
    models.update({k: v for k, v in overrides.items() if k in DEFAULT_MODELS})
    return models
