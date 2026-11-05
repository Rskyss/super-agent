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
CODEX_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")

_EFFORT_LABELS = {
    "low": "低（low · 速度最快）",
    "medium": "中（medium · 日常均衡）",
    "high": "高（high · 深度思考）",
    "xhigh": "极高（xhigh · 复杂问题）",
    "max": "最大（max · 高难问题）",
    "ultra": "超强（ultra · 最高推理）",
}

MODEL_OPTIONS = {
    "codex": [
        {"value": "gpt-6.1-sol", "label": "GPT-6.1-Sol", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"]},
        {"value": "gpt-6-astra", "label": "GPT-6-Astra", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"]},
        {"value": "gpt-6.1-terra", "label": "GPT-6.1 Terra", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"]},
        {"value": "gpt-6-sol", "label": "GPT-6-Sol", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"]},
        {"value": "gpt-6-luna", "label": "GPT-6-Luna", "efforts": ["low", "medium", "high", "xhigh", "max"]},
        {"value": "gpt-5.6-sol", "label": "GPT-5.6-Sol", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"]},
        {"value": "gpt-5.6-terra", "label": "GPT-5.6-Terra", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"]},
        {"value": "gpt-5.6-luna", "label": "GPT-5.6-Luna", "efforts": ["low", "medium", "high", "xhigh", "max"]},
    ],
    "codex_effort": [{"value": k, "label": _EFFORT_LABELS[k]} for k in CODEX_EFFORTS],
    "cursor": [
        {"value": "auto", "label": "Auto（自动推荐 · 默认）"},
        {"value": "gpt-5.6-sol-xhigh", "label": "GPT-5.6 Sol 1M Extra High"},
        {"value": "gpt-5.6-sol-xhigh-fast", "label": "GPT-5.6 Sol 1M Extra High Fast"},
        {"value": "gpt-5.6-sol-high", "label": "GPT-5.6 Sol 1M High"},
        {"value": "gpt-5.6-sol-high-fast", "label": "GPT-5.6 Sol 1M High Fast"},
        {"value": "gpt-5.6-luna-high", "label": "GPT-5.6 Luna 1M High"},
        {"value": "gpt-5.3-codex-xhigh", "label": "Codex 5.3 Extra High"},
        {"value": "gpt-5.3-codex-xhigh-fast", "label": "Codex 5.3 Extra High Fast"},
        {"value": "gpt-5.3-codex-high", "label": "Codex 5.3 High"},
        {"value": "gpt-5.3-codex-high-fast", "label": "Codex 5.3 High Fast"},
        {"value": "gpt-5.3-codex", "label": "Codex 5.3"},
        {"value": "gpt-5.3-codex-fast", "label": "Codex 5.3 Fast"},
        {"value": "gpt-5.3-codex-low", "label": "Codex 5.3 Low"},
        {"value": "gpt-5.3-codex-low-fast", "label": "Codex 5.3 Low Fast"},
        {"value": "claude-opus-5-thinking-high", "label": "Claude Opus 5 1M Thinking"},
        {"value": "claude-opus-5-thinking-high-fast", "label": "Claude Opus 5 1M Thinking Fast"},
        {"value": "claude-fable-5-thinking-xhigh", "label": "Claude Fable 5 1M Extra High Thinking"},
        {"value": "claude-fable-5-thinking-high", "label": "Claude Fable 5 1M Thinking"},
        {"value": "claude-sonnet-5-thinking-xhigh", "label": "Claude Sonnet 5 1M Extra High Thinking"},
        {"value": "claude-sonnet-5-thinking-high", "label": "Claude Sonnet 5 1M Thinking"},
        {"value": "composer-2.5", "label": "Composer 2.5"},
        {"value": "gpt-5.2", "label": "GPT-5.2"},
        {"value": "gemini-3.7-flash-high", "label": "Gemini 3.7 Flash"},
        {"value": "cursor-grok-4.5-high", "label": "Grok 4.5"},
        {"value": "cursor-grok-4.5-high-fast", "label": "Grok 4.5 Fast"},
        {"value": "grok-4.7-medium", "label": "Grok 4.7 Medium"},
        {"value": "grok-4.7-low", "label": "Grok 4.7 Low"},
        {"value": "grok-4.7-low-fast", "label": "Grok 4.7 Low Fast"},
    ],
    "agy": [
        {"value": "gemini-4-argon-high", "label": "Gemini 4 Argon (High · 默认)"},
        {"value": "gemini-4-argon-medium", "label": "Gemini 4 Argon (Medium)"},
        {"value": "gemini-4-argon-low", "label": "Gemini 4 Argon (Low)"},
        {"value": "gemini-3.8-flash-high", "label": "Gemini 3.8 Flash (High)"},
        {"value": "gemini-3.8-flash-medium", "label": "Gemini 3.8 Flash (Medium)"},
        {"value": "gemini-3.8-flash-low", "label": "Gemini 3.8 Flash (Low)"},
        {"value": "claude-opus-5-5-high", "label": "Claude Opus 5.5 (High)"},
        {"value": "claude-opus-5-5-medium", "label": "Claude Opus 5.5 (Medium)"},
        {"value": "claude-opus-5-5-low", "label": "Claude Opus 5.5 (Low)"},
        {"value": "claude-sonnet-5-5-high", "label": "Claude Sonnet 5.5 (High)"},
        {"value": "claude-sonnet-5-5-medium", "label": "Claude Sonnet 5.5 (Medium)"},
        {"value": "claude-sonnet-5-5-low", "label": "Claude Sonnet 5.5 (Low)"},
        {"value": "gpt-oss-120b-medium", "label": "GPT-OSS 120B (Medium)"},
    ],
}


def load_model_options(home_dir=None) -> dict:
    """从本机各家 CLI 同步好的本地目录文件里读取最新模型与思考档位（0 秒读取，未登录或文件不存在时用内置清单兜底）。"""
    home = Path(home_dir) if home_dir else Path.home()
    opts = {k: [dict(item) for item in v] for k, v in MODEL_OPTIONS.items()}

    # 1. 读取 Codex 本机模型目录 (~/.codex/models_cache.json)
    codex_cache = home / ".codex" / "models_cache.json"
    if codex_cache.is_file():
        try:
            raw = json.loads(codex_cache.read_text(encoding="utf-8"))
            codex_list = []
            seen_efforts = []
            for m in raw.get("models") or []:
                if not isinstance(m, dict) or m.get("visibility") != "list":
                    continue
                slug = (m.get("slug") or "").strip()
                if not slug:
                    continue
                disp = (m.get("display_name") or slug).strip()
                efforts = [
                    e.get("effort")
                    for e in (m.get("supported_reasoning_levels") or [])
                    if isinstance(e, dict) and e.get("effort")
                ]
                for ef in efforts:
                    if ef not in seen_efforts:
                        seen_efforts.append(ef)
                codex_list.append({"value": slug, "label": disp, "efforts": efforts or list(CODEX_EFFORTS)})
            if codex_list:
                opts["codex"] = codex_list
            if seen_efforts:
                opts["codex_effort"] = [
                    {"value": ef, "label": _EFFORT_LABELS.get(ef, ef)} for ef in seen_efforts
                ]
        except (OSError, ValueError):
            pass

    # 2. 读取 Cursor 本机配置 (~/.cursor/cli-config.json) 中用户使用过的自定义/新模型并补充
    cursor_cfg = home / ".cursor" / "cli-config.json"
    if cursor_cfg.is_file():
        try:
            raw = json.loads(cursor_cfg.read_text(encoding="utf-8"))
            known = {x["value"] for x in opts["cursor"]}
            for cand in [
                (raw.get("model") or {}).get("displayModelId"),
                (raw.get("selectedModel") or {}).get("modelId"),
                *(raw.get("modelSelectionHistory") or []),
            ]:
                if isinstance(cand, str) and cand.strip() and cand not in ("default", "") and cand not in known:
                    opts["cursor"].insert(1, {"value": cand, "label": cand})
                    known.add(cand)
        except (OSError, ValueError):
            pass

    return opts

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


def save_models(project_dir, models: dict) -> None:
    """把各家工具选定的模型与思考档位存进 .collab/配置.json，其他设置原样保留。"""
    if not isinstance(models, dict):
        raise ConfigError("模型配置格式不对")
    clean = {}
    for key in DEFAULT_MODELS:
        if key not in models or models[key] is None:
            continue
        val = str(models[key]).strip()
        if not val:
            raise ConfigError(f"{key} 的模型名不能为空")
        if key == "codex_effort" and val not in CODEX_EFFORTS:
            raise ConfigError(f"Codex 思考档位不能是 {val!r}：可选 {'、'.join(CODEX_EFFORTS)}")
        clean[key] = val
    data = _read(project_dir)
    data.update(clean)
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_tools(project_dir, dev=None, test=None, review=None) -> dict:
    """开工用谁：命令里写了就用命令里的，没写就用团队配置，再没有就用默认。复查为 none 时返回 None。"""
    team = load_team(project_dir)
    chosen_review = review or team["reviewer"]
    return {"test": test or team["tester"], "dev": dev or team["developer"],
            "review": None if chosen_review == "none" else chosen_review}
