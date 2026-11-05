"""项目配置：模型与团队。都存在目标项目的 .collab/配置.json（只在本机）。

- 模型：包里给默认值，项目可以逐项覆盖。换模型只改这里，不改代码。
- 团队：谁当调度员、谁写测试/写代码/复查。由用户在窗口里亲手配置；
  协作包开工、桌面窗口、右边面板都读这一份。调度员没有默认值（必须用户自己选）。
"""
from __future__ import annotations

import json
from pathlib import Path

WORKERS = ("codex", "cursor", "agy")          # 能被协作包自动派活的工具
DISPATCHERS = ("codex", "cursor", "agy")      # 可当调度员的（另可“custom:命令”）；Claude 暂不进入
DEFAULT_TEAM = {"tester": "codex", "developer": "cursor", "reviewer": "agy"}
_ROLES = {"tester": "写测试", "developer": "写代码", "reviewer": "复查", "dispatcher": "调度员"}

DEFAULT_MODELS = {
    "cursor": "auto",              # Cursor CLI（agent）
    "codex": "gpt-6.1-sol",        # Codex 测试员
    "codex_effort": "xhigh",       # Codex 推理强度
    "agy": "gemini-4-argon-high",  # agy 备选开发
}

CONFIG_FILE = "配置.json"


class ConfigError(Exception):
    pass


def _config_path(project_dir) -> Path:
    return Path(project_dir) / ".collab" / CONFIG_FILE


def _read(project_dir) -> dict:
    path = _config_path(project_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 顶层必须是对象")
    return data


def load_models(project_dir) -> dict:
    """返回生效的模型配置：默认值 + 项目 .collab/配置.json 的覆盖。"""
    models = dict(DEFAULT_MODELS)
    models.update({k: v for k, v in _read(project_dir).items() if k in DEFAULT_MODELS})
    return models


def _check_member(role: str, value) -> None:
    allowed = WORKERS + (("none",) if role == "reviewer" else ())
    if role == "dispatcher":
        if value in DISPATCHERS or (isinstance(value, str) and value.startswith("custom:") and value[7:].strip()):
            return
        raise ConfigError(f"调度员不能是 {value!r}：可选 Codex、Cursor、agy，或自定义命令")
    if value not in allowed:
        raise ConfigError(f"{_ROLES[role]}不能是 {value!r}：可选 {'、'.join(allowed)}（执行员必须是协作包能自动派活的工具）")


def load_team(project_dir) -> dict:
    """团队：{dispatcher, tester, developer, reviewer, configured}。没配的执行员用默认，调度员没配就是 None。"""
    saved = _read(project_dir).get("team") or {}
    if not isinstance(saved, dict):
        raise ConfigError("团队配置格式不对")
    team = dict(DEFAULT_TEAM, dispatcher=None)
    for role in ("dispatcher", "tester", "developer", "reviewer"):
        if saved.get(role) is not None:
            _check_member(role, saved[role])
            team[role] = saved[role]
    team["configured"] = bool(saved.get("dispatcher"))
    return team


def save_team(project_dir, team: dict) -> None:
    """检查后存进 .collab/配置.json，其他设置原样保留。"""
    clean = {}
    for role in ("dispatcher", "tester", "developer", "reviewer"):
        value = team.get(role)
        _check_member(role, value)
        clean[role] = value
    data = _read(project_dir)
    data["team"] = clean
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_tools(project_dir, dev=None, test=None, review=None) -> dict:
    """开工用谁：命令里写了就用命令里的，没写就用团队配置，再没有就用默认。复查为 none 时返回 None。"""
    team = load_team(project_dir)
    chosen_review = review or team["reviewer"]
    return {"test": test or team["tester"], "dev": dev or team["developer"],
            "review": None if chosen_review == "none" else chosen_review}
