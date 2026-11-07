"""协作区：接入一个项目时生成 .collab/ 骨架，并根据各卡状态渲染任务板。已存在的文件绝不覆盖。

协作包本身不再复制进项目（只在超级助手里放一份）。项目里只多一个 .collab/，并加进不提交名单：
- 结果（台账、项目说明、归档）放项目 docs/，跟着代码进 git；
- 过程（卡片、交接全文、实时输出）放 .collab/，只留在本机。
"""
from __future__ import annotations

import json
from pathlib import Path

from collab.state import Card

PACKAGE_DIR = Path(__file__).resolve().parent
PROFILE_FILE = Path("docs") / "项目说明.md"        # 项目说明：结果，进 git
LEGACY_PROFILE = Path(".collab") / "项目档案.md"   # 旧位置，仍然认
IGNORE_RULE = ".collab/"

RECORDS_README = (
    "# 协作区（只留在本机，不进 git）\n\n"
    f"各工具进门先读本文件。规则书：{PACKAGE_DIR / '规则.md'}，开工前必须先读。\n"
    f"调度员的操作手册：{PACKAGE_DIR / 'README.md'}。\n"
    "卡片见 cards/，状态以任务板为准。项目说明在项目的 docs/项目说明.md。\n"
)

_SKELETON = {
    "README.md": RECORDS_README,
    "任务板.md": "# 任务板\n\n（暂无卡片）\n",
    "备份记录.md": "# 备份记录\n\n| 时间 | 版本标记 | 卡片 |\n|---|---|---|\n",
}

PLACEHOLDER = "（待填）"

PROFILE_TEMPLATE = """# 项目说明

- 这个项目是做什么的：{placeholder}
- 怎么启动：{placeholder}
- 怎么测试（测试命令）：{placeholder}
- 禁区（哪些不能动）：{placeholder}
- 技术栈：{placeholder}
""".format(placeholder=PLACEHOLDER)

PLAN_TEMPLATE = """# 拆解方案

## 要做什么（一句话）
{placeholder}

## 拆成几步，每步做什么、产出什么
1. {placeholder}

## 你会看到什么结果（验收标准）
{placeholder}

## 明确不做什么
{placeholder}

## 需要你确认的问题
{placeholder}
""".format(placeholder=PLACEHOLDER)


def has_blanks(text: str) -> bool:
    """文档里还留着空白标记，说明还没填完，不能拿来确认。"""
    return PLACEHOLDER in text


def profile_path(project_dir) -> Path:
    """项目说明在哪：新位置 docs/项目说明.md 优先；只有旧位置有时用旧的；都没有时返回新位置（待起草）。"""
    project = Path(project_dir)
    new, old = project / PROFILE_FILE, project / LEGACY_PROFILE
    if new.exists() or not old.exists():
        return new
    return old


def _already_ignored(lines) -> bool:
    return any(line.strip() in (".collab", ".collab/", "/.collab", "/.collab/") for line in lines)


def ensure_ignored(project_dir) -> bool:
    """把 .collab/ 加进项目的不提交名单。只对 git 项目做；已经有了不重复；原有内容一字不动。
    返回 True 表示这次新加了。"""
    project = Path(project_dir)
    if not (project / ".git").exists():
        return False
    ignore = project / ".gitignore"
    text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    if _already_ignored(text.splitlines()):
        return False
    prefix = "" if not text or text.endswith("\n") else "\n"
    with ignore.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}# 超级助手的施工过程记录，只留在本机\n{IGNORE_RULE}\n")
    return True


def init_collab(project_dir) -> Path:
    """接入一个项目：建 .collab/ 骨架、加进不提交名单。不复制协作包。可以重复执行。"""
    collab = Path(project_dir) / ".collab"
    (collab / "cards").mkdir(parents=True, exist_ok=True)
    for name, text in _SKELETON.items():
        target = collab / name
        if not target.exists():
            target.write_text(text, encoding="utf-8")
    ensure_ignored(project_dir)
    return collab


def render_board(collab_dir) -> None:
    collab = Path(collab_dir)
    rows = []
    for state_file in sorted((collab / "cards").glob("*/状态.json")):
        try:
            card = Card.load(state_file)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        rework = f"返工 {card.rework}" if card.rework else "—"
        rows.append(f"| {card.card_id} | {card.state} | {rework} |")
    body = ["# 任务板", "", "| 卡片 | 状态 | 返工 |", "|---|---|---|"]
    body += rows or ["| （暂无卡片） | | |"]
    (collab / "任务板.md").write_text("\n".join(body) + "\n", encoding="utf-8")
