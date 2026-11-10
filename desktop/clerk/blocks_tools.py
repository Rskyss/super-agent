"""积木库行政专用工具（54 号单）。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from collab import blocks, blocks_check, blocks_env, blocks_install, blocks_web
from collab.blocks import BlockError
from desktop.clerk.tools import LABELS

ROLE_FILE = Path(__file__).resolve().parent / "roles" / "积木库.md"
_MAX_RESULT = 6000
_PAGE_RESULT = 9000
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
        "description": ("向用户出示接入 CLI 的提议卡。codex/cursor/agy 优先传 template；别的工具传起草的 card。"
                        "AI 助手（交代一句话它自己干活）的 card 写 run（含 {prompt}）；普通工具（给命令返回结果）写 "
                        "role=tool 和 probe（一条只查不改的检测命令的参数列表），可选 ok_words；要密钥的写 secrets："
                        "[{env: 交给工具时的环境变量名, label: 显示名}]，只写名字，密钥由用户在卡上填"),
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

TOOLS.extend([
    {"type": "function", "function": {
        "name": "web_search",
        "description": "用基础层里的联网搜索查资料（例如某个工具的官网和安装说明），最多 5 条结果",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索词"},
        }, "required": ["query"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_page",
        "description": "由程序打开一个 https 网页，把正文交给你看（只读，最多 8000 字）",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "完整的 https:// 网址"},
        }, "required": ["url"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "propose_web_install",
        "description": ("核实名单里没有的工具：把官网上那一页的网址和页面上原样的安装命令交给程序，程序自己读页面核对、"
                        "查安装包底细，都过了才向用户出示安装卡。只支持 npm install -g、brew install、"
                        "curl … | sh/bash 三种写法"),
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "写着安装命令的官网页面网址"},
            "install_command": {"type": "string", "description": "页面上原样的那一行安装命令"},
            "command": {"type": "string", "description": "装好后的命令名（脚本安装必填，例如 agent）"},
        }, "required": ["url", "install_command"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "propose_connect_search",
        "description": "基础层里还没有联网搜索时，向用户出示接入联网搜索（Tavily）的卡，用户在卡上填 Tavily 密钥",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
])

LABELS.update({
    "web_search": "查 网上",
    "read_page": "读 网页",
    "propose_web_install": "读 提议",
    "propose_connect_search": "读 提议",
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
    if not project or not isinstance(project, (str, Path)):
        return False  # 空路径会被当成当前文件夹
    try:
        return Path(project).expanduser().resolve() == blocks.home().expanduser().resolve()
    except (OSError, ValueError):  # 带 \0 这类畸形路径
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
        return f"找不到 {command}：这台电脑上没装，或者装在超级助手找不到的地方。"
    try:
        _, out = blocks_env.run_command([found, "--version"], path, 10)
        ver = blocks_env._version_line(out)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        ver = ""
    if not ver:
        return f"{command} 已经装了（{blocks_check._mask(found)}），但查不出版本号。"
    return f"{command} 已经装了，版本：{ver}"


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
    cleaned = blocks_check._mask_cut(out or "", _HELP_LIMIT).strip()  # 先打码再截断，超长输出也不卡
    return cleaned or f"{command} --help 没有输出内容。"


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
            word = "能用" if last.get("ok") else ("额度不足（能连上）" if last.get("status") == "quota" else "不能用")
            status = f"{word}（{last.get('reason') or ''}）"
        kind = blocks.CATEGORY_LABELS[blocks.category(card)]
        lines.append(f"{card['name']}（{card['id']}）｜{kind}｜{status}")
    return "\n".join(lines)


def _install_options(args: dict) -> str:
    tool = str(args.get("tool") or "").strip().lower()
    opts = blocks_install.options(tool)
    if not opts:
        return f"{tool or '这个工具'} 不在核实名单里，要按官网自己装。"
    lines = []
    for item in opts:
        lines.append(
            f"{item['id']}｜{item['label']}｜命令：{item['command']}｜需要：{item['needs']}｜来源：{item['source']}"
        )
    return "\n".join(lines)


_NO_SEARCH = ("基础层里没有联网搜索，或者还没填它的密钥：用 propose_connect_search 出卡，请用户填一把 Tavily 密钥；"
              "也可以请用户把官网链接贴给你，用 read_page 读。")


def _web_search(args: dict) -> str:
    from desktop.clerk import tasks as clerk_tasks
    key = clerk_tasks.search_key()
    if not key:
        return _NO_SEARCH
    try:
        items = blocks_web.search(str(args.get("query") or ""), key)
    except BlockError as exc:
        return f"搜索没成功：{exc}"
    if not items:
        return "没搜到结果，换个说法再搜一次。"
    return "\n".join(f"{i + 1}. {it['title']}｜{it['url']}｜{it['snippet']}" for i, it in enumerate(items))


def _read_page(args: dict) -> str:
    from desktop.clerk import tasks as clerk_tasks
    url = str(args.get("url") or "").strip()
    try:
        page = blocks_web.read_page(url)
    except BlockError as exc:
        return f"读不了：{exc}"
    if len(page["text"]) < 200:  # 网页打开后才画出来的，直接读只有空架子：有搜索密钥就请 Tavily 读一次
        key = clerk_tasks.search_key()
        if key:
            try:
                extra = blocks_web.tavily_extract(page["url"], key)
            except BlockError:
                extra = ""
            if extra:
                page = dict(page, text=blocks_web._norm(extra))
    return blocks_web.clerk_text(page)


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
        elif name == "web_search":
            text = _web_search(args)
        elif name == "read_page":
            text = _read_page(args)
        else:
            text = "没有这个工具"
    except Exception as exc:
        text = f"工具执行出错：{exc}"
    limit = _PAGE_RESULT if name == "read_page" else _MAX_RESULT  # 网页正文按方案给到 8000 字（复查第 9 条）
    return text[:limit]
