"""行政人员的只读工具：查项目说明、台账、卡片和团队设置。不接受文件路径，不读密钥。"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from collab.config import ConfigError, load_team
from collab.flow import read_flow
from collab.state import Card

_MAX_RESULT = 6000
_CARD_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_ROLE_NAMES = {"dispatcher": "调度员", "tester": "写测试", "developer": "写代码", "reviewer": "复查"}
_TOOL_NAMES = {"codex": "Codex", "cursor": "Cursor", "agy": "agy", "none": "不复查"}
_TEAM_ROLES = ("dispatcher", "tester", "developer", "reviewer")
_TEAM_WORKER_TOOLS = ("codex", "cursor", "agy")
_TEAM_REVIEWER_TOOLS = _TEAM_WORKER_TOOLS + ("none",)

TOOLS = [
    {"type": "function", "function": {
        "name": "read_project_profile",
        "description": "读项目说明",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_ledger",
        "description": "读开发进度台账的最后几行",
        "parameters": {"type": "object", "properties": {
            "lines": {"type": "integer", "description": "读最后几行，默认 40，最多 80"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "list_cards",
        "description": "列出所有任务卡片",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_card",
        "description": "读一张卡片的详情",
        "parameters": {"type": "object", "properties": {
            "card_id": {"type": "string", "description": "卡片编号，例如 CARD-001"},
        }, "required": ["card_id"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_team",
        "description": "读团队配置",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "list_installed_tools",
        "description": "查看本机 PATH 里是否装了 codex、agent、agy（只检查，不运行）",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "propose_team_change",
        "description": "向用户出示“改团队设置？”确认框。用户点确认后才写入配置。",
        "parameters": {"type": "object", "properties": {
            "role": {"type": "string", "description": "dispatcher / tester / developer / reviewer"},
            "tool": {"type": "string", "description": "codex / cursor / agy；复查可选 none"},
        }, "required": ["role", "tool"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "propose_task",
        "description": "向用户出示“变成任务？”确认框。用户点确认才建卡。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "任务标题"},
            "do": {"type": "string", "description": "要做什么"},
            "not_do": {"type": "string", "description": "不做什么"},
            "done_when": {"type": "string", "description": "怎么算做完"},
        }, "required": ["title", "do", "not_do", "done_when"], "additionalProperties": False},
    }},
]

LABELS = {
    "read_project_profile": "读 项目说明",
    "read_ledger": "读 台账",
    "list_cards": "读 卡片列表",
    "read_card": "读 卡片",
    "read_team": "读 团队设置",
    "list_installed_tools": "查 本机工具",
    "propose_team_change": "读 提议",
    "propose_task": "读 提议",
}


def run_tool(project: str, name: str, arguments: dict) -> str:
    """永远返回文字，不抛异常。单个结果最多 6000 字。"""
    try:
        args = arguments if isinstance(arguments, dict) else {}
        if name == "read_project_profile":
            text = _read_profile(project)
        elif name == "read_ledger":
            text = _read_ledger(project, args)
        elif name == "list_cards":
            text = _list_cards(project)
        elif name == "read_card":
            text = _read_card(project, args)
        elif name == "read_team":
            text = _read_team(project)
        elif name == "list_installed_tools":
            text = _list_installed_tools()
        else:
            text = "没有这个工具"
    except Exception:
        text = "这次没读到，请稍后再试。"
    if len(text) > _MAX_RESULT:
        text = text[:_MAX_RESULT]
    return text


def _clip(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_profile(project: str) -> str:
    path = Path(project) / "docs" / "项目说明.md"
    if not path.is_file():
        return "这个项目还没有项目说明。"
    text = _clip(path).strip()
    return text or "项目说明是空的。"


def _read_ledger(project: str, args: dict) -> str:
    path = Path(project) / "docs" / "开发进度跟踪.md"
    if not path.is_file():
        return "还没有开发进度台账。"
    try:
        n = int(args.get("lines", 40))
    except (TypeError, ValueError):
        n = 40
    n = max(1, min(n, 80))
    lines = _clip(path).splitlines()
    if not lines:
        return "台账还是空的。"
    return "\n".join(lines[-n:])


def _card_title(cdir: Path, card_id: str) -> str:
    for ev in read_flow(cdir):
        if ev.get("kind") == "需求" and (ev.get("summary") or "").strip():
            return ev["summary"].strip()
    path = cdir / "需求卡.md"
    if path.is_file():
        for line in _clip(path).splitlines():
            s = line.strip()
            if s.startswith("#"):
                title = s.lstrip("#").strip()
                if title:
                    return title
    return card_id


def _list_cards(project: str) -> str:
    base = Path(project) / ".collab" / "cards"
    if not base.is_dir():
        return "还没有任务卡片。"
    rows = []
    for state_file in sorted(base.glob("*/状态.json")):
        try:
            card = Card.load(state_file)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        title = _card_title(state_file.parent, card.card_id)
        rows.append(f"{card.card_id}｜{title}｜{card.state}｜返工 {card.rework} 次")
    return "\n".join(rows) if rows else "还没有任务卡片。"


def _read_card(project: str, args: dict) -> str:
    card_id = str(args.get("card_id") or "")
    if not _CARD_ID.fullmatch(card_id):
        return "卡片编号不对。"
    cdir = Path(project) / ".collab" / "cards" / card_id
    state_file = cdir / "状态.json"
    if not state_file.is_file():
        return "没有这张卡片。"
    try:
        card = Card.load(state_file)
    except (OSError, ValueError, KeyError, TypeError):
        return "这张卡片读不出来。"
    title = _card_title(cdir, card.card_id)
    lines = [f"编号：{card.card_id}", f"标题：{title}", f"状态：{card.state}", f"返工次数：{card.rework}"]
    flow = read_flow(cdir)
    recent = flow[-12:]
    if recent:
        lines.append("最近流转：")
        for ev in recent:
            kind = ev.get("kind") or ""
            summary = ev.get("summary") or ""
            lines.append(f"- {kind}：{summary}" if summary else f"- {kind}")
    for ev in reversed(flow):
        if ev.get("kind") == "转人工":
            reason = (ev.get("summary") or "").strip()
            if reason:
                lines.append(f"最近一次转人工原因：{reason}")
            break
    return "\n".join(lines)


def _list_installed_tools() -> str:
    """用 shutil.which 只检查是否存在可执行文件，不运行。"""
    lines = []
    for cmd, label in (("codex", "Codex"), ("agent", "Cursor（agent 命令）"), ("agy", "agy")):
        path = shutil.which(cmd)
        if path:
            lines.append(f"{label}：已安装（{path}）")
        else:
            lines.append(f"{label}：未在 PATH 中找到")
    return "\n".join(lines)


def team_incomplete_hint(project: str) -> str:
    """团队未配齐时给系统提示里加一句引导（一次一项）。"""
    try:
        team = load_team(project)
    except ConfigError:
        return "团队配置读不出来：请在设置里先修好配置，或一项一项说清要改谁。"
    if not team.get("configured"):
        return "团队没配好：先请用户去「设置」里选调度员；选好后如需再调别的角色，再一步一步来。"
    missing = []
    for role in _TEAM_ROLES:
        v = team.get(role)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(_ROLE_NAMES.get(role, role))
    if missing:
        first = missing[0]
        return f"团队没配好：现在先只帮用户配好「{first}」这一项，再问别的。"
    return ""


def _read_team(project: str) -> str:
    try:
        team = load_team(project)
    except ConfigError as exc:
        return str(exc)
    except Exception:
        return "还没有团队配置。"
    lines = []
    for key, label in _ROLE_NAMES.items():
        value = team.get(key)
        if value is None or value == "":
            shown = "还没选"
        elif isinstance(value, str) and value.startswith("custom:"):
            shown = "自定义命令"
        else:
            shown = _TOOL_NAMES.get(str(value), str(value))
        lines.append(f"{label}：{shown}")
    return "\n".join(lines)
