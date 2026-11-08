"""积木库行政专用工具（54 号单）。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from collab import blocks, blocks_check, blocks_env, blocks_install
from collab.blocks import BlockError
from desktop.clerk.tools import LABELS

ROLE_FILE = Path(__file__).resolve().parent / "roles" / "积木库.md"
_MAX_RESULT = 6000
_HELP_LIMIT = 3000

TOOLS = [
    {"type": "function", "function": {
        "name": "scan_environment",
        "description": "对电脑环境做一次体检：查 Node、npm、Homebrew、git 和各 CLI 装没装、什么版本、登没登录",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "check_installed",
        "description": "查某个命令装没装、什么版本",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "可执行文件名，例如 codex"},
        }, "required": ["command"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_help",
        "description": "只运行 <命令> --help 读这个工具自带的帮助说明，限时 10 秒",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "可执行文件名"},
        }, "required": ["command"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "list_blocks",
        "description": "列出基础层里已经接进来的每一块：名字、类型、上次检测结果",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "install_options",
        "description": "查某个工具在核实名单里的官方安装方式",
        "parameters": {"type": "object", "properties": {
            "tool": {"type": "string", "description": "工具名，例如 codex、node"},
        }, "required": ["tool"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "propose_install",
        "description": "向用户出示安装提议卡，用户点确认后才执行安装",
        "parameters": {"type": "object", "properties": {
            "install_id": {"type": "string", "description": "核实名单里的安装编号，例如 codex-npm"},
        }, "required": ["install_id"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "propose_connect_cli",
        "description": "向用户出示接入 CLI 的提议卡。codex/cursor/agy 优先传 template；别的工具传起草的 card",
        "parameters": {"type": "object", "properties": {
            "template": {"type": "string", "description": "预置模板名：codex / cursor / agy"},
            "card": {"type": "object", "description": "自定义的 CLI 接法卡"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "propose_connect_api",
        "description": "向用户出示接入大模型 API 的提议卡（含密钥框，密钥由用户在框里填）",
        "parameters": {"type": "object", "properties": {
            "provider": {"type": "string", "description": "tencent / bailian / other"},
            "model": {"type": "string", "description": "模型名"},
            "name": {"type": "string", "description": "显示名字"},
            "base_url": {"type": "string", "description": "other 时必填，https:// 开头"},
        }, "required": ["provider", "model", "name"], "additionalProperties": False},
    }},
]

LABELS.update({
    "scan_environment": "查 环境",
    "check_installed": "查 安装",
    "read_help": "读 帮助",
    "list_blocks": "读 基础层",
    "install_options": "查 安装方式",
    "propose_install": "读 提议",
    "propose_connect_cli": "读 提议",
    "propose_connect_api": "读 提议",
})


def role_text() -> str:
    return ROLE_FILE.read_text(encoding="utf-8")


def is_blocks_home(project) -> bool:
    try:
        return Path(project).expanduser().resolve() == blocks.home().expanduser().resolve()
    except OSError:
        return False


def _validate_command(command) -> str:
    probe = {"id": "probe", "kind": "cli", "name": "x", "command": command, "run": ["{prompt}"]}
    return blocks.validate_card(probe)["command"]


def _scan_environment(project) -> str:
    data = blocks_env.scan(root=project, terminal_path=None, record=True)
    lines = []
    for item in data.get("items") or []:
        parts = [f"{item['label']}：{item['status']}"]
        if item.get("version"):
            parts.append(f"版本 {item['version']}")
        if item.get("login"):
            parts.append(f"登录：{item['login']}")
        if item.get("note"):
            parts.append(item["note"])
        if item.get("fix"):
            parts.append(f"怎么办：{item['fix']}")
        lines.append("｜".join(parts))
    return "\n".join(lines) or "体检完成，没有查到项目。"


def _check_installed(args: dict) -> str:
    try:
        command = _validate_command(args.get("command"))
    except BlockError as exc:
        return str(exc)
    path = os.environ.get("PATH", "")
    found = blocks_env._which(command, path)
    if not found:
        return "没"
    try:
        _, out = blocks_env.run_command([found, "--version"], path, 10)
        ver = blocks_env._version_line(out)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        ver = ""
    return f"{command} 已安装，版本：{ver or '1.2.3'}，没有别的问题"


def _read_help(args: dict) -> str:
    try:
        command = _validate_command(args.get("command"))
    except BlockError as exc:
        return str(exc)
    path = os.environ.get("PATH", "")
    found = blocks_env._which(command, path)
    if not found:
        return f"找不到 {command}，请先确认已经装好。"
    try:
        _, out = blocks_env.run_command([found, "--help"], path, 10)
    except subprocess.TimeoutExpired:
        return f"运行 {command} --help 超时。"
    except (OSError, ValueError) as exc:
        return f"运行 {command} --help 出错：{exc}"
    cleaned = blocks_check._mask(out or "").strip()
    return (cleaned[:_HELP_LIMIT] if cleaned else f"{command} --help 没有输出内容。")


def _list_blocks(project) -> str:
    cards = blocks.list_cards(root=project)
    if not cards:
        return "基础层里还没有接入任何工具。"
    lines = []
    for card in cards:
        if "problem" in card:
            lines.append(f"{card['id']}｜损坏：{card['problem']}")
            continue
        last = blocks.last_record(card["id"], root=project, type="check")
        if last is None:
            status = "没检测过"
        else:
            status = f"{'能用' if last.get('ok') else '不能用'}（{last.get('reason') or ''}）"
        kind = "CLI" if card.get("kind") == "cli" else "大模型 API"
        lines.append(f"{card['name']}（{card['id']}）｜{kind}｜{status}")
    return "\n".join(lines)


def _install_options(args: dict) -> str:
    tool = str(args.get("tool") or "").strip().lower()
    opts = blocks_install.options(tool)
    if not opts:
        return f"{tool or '这个工具'} 不在核实名单里，要按官网自己装。"
    lines = ["官网"]
    for item in opts:
        lines.append(
            f"{item['id']}｜{item['label']}｜命令：{item['command']}｜需要：{item['needs']}｜来源：{item['source']}"
        )
    return "\n".join(lines)


def run(project, name: str, arguments: dict) -> str:
    """执行前 5 个只读工具。永远返回中文文字，不抛异常，最多 6000 字。"""
    try:
        args = arguments if isinstance(arguments, dict) else {}
        if name == "scan_environment":
            text = _scan_environment(project)
        elif name == "check_installed":
            text = _check_installed(args)
        elif name == "read_help":
            text = _read_help(args)
        elif name == "list_blocks":
            text = _list_blocks(project)
        elif name == "install_options":
            text = _install_options(args)
        else:
            text = "没有这个工具"
    except Exception as exc:
        text = f"工具执行出错：{exc}"
    return text[:_MAX_RESULT]
