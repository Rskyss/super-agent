"""协作区：生成 .collab/ 骨架，并根据各卡状态渲染任务板。已存在的文件绝不覆盖。"""
from __future__ import annotations

import json
from pathlib import Path

from collab.state import Card

_SKELETON = {
    "README.md": ("# 协作区\n\n各工具进门先读本文件。规则书在项目里的 collab/规则.md，开工前必须先读。\n"
                  "卡片见 cards/，状态以任务板为准。调度员的操作手册在 collab/README.md。\n"),
    "项目档案.md": "# 项目档案\n\n- 怎么启动：（待填）\n- 怎么测试（测试命令）：（待填）\n- 禁区：（待填）\n- 技术栈：（待填）\n",
    "任务板.md": "# 任务板\n\n（暂无卡片）\n",
    "备份记录.md": "# 备份记录\n\n| 时间 | 版本标记 | 卡片 |\n|---|---|---|\n",
}


PLACEHOLDER = "（待填）"

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


def init_collab(project_dir) -> Path:
    collab = Path(project_dir) / ".collab"
    (collab / "cards").mkdir(parents=True, exist_ok=True)
    for name, text in _SKELETON.items():
        target = collab / name
        if not target.exists():
            target.write_text(text, encoding="utf-8")
    return collab


def render_board(collab_dir) -> None:
    collab = Path(collab_dir)
    rows = []
    for state_file in sorted((collab / "cards").glob("*/状态.json")):
        card = Card.load(state_file)
        rework = f"返工 {card.rework}" if card.rework else "—"
        rows.append(f"| {card.card_id} | {card.state} | {rework} |")
    body = ["# 任务板", "", "| 卡片 | 状态 | 返工 |", "|---|---|---|"]
    body += rows or ["| （暂无卡片） | | |"]
    (collab / "任务板.md").write_text("\n".join(body) + "\n", encoding="utf-8")
