"""建任务卡：行政人员确认提议后调用。编号加锁，不能重号。"""
from __future__ import annotations

import fcntl
import json
import re
from pathlib import Path

from collab.flow import record as flow_record
from collab.state import Card
from collab.workspace import PLAN_TEMPLATE, init_collab, render_board

_CARD_SHAPE = re.compile(r"^CARD-(\d+)$")


class TaskError(Exception):
    """用户能看懂的建卡失败原因。"""


def new_card(project, title, do, not_do, done_when, conversation="") -> str:
    project = Path(project)
    if not (project / ".git").exists():
        raise TaskError("这个文件夹还不是 git 项目，暂时不能建任务。")
    collab = init_collab(project)
    cards = collab / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    lock_path = collab / ".编号锁"
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        next_no = 1
        for path in cards.iterdir():
            match = _CARD_SHAPE.match(path.name)
            # 空目录也算占用编号，避免建卡撞上
            if match and path.is_dir():
                next_no = max(next_no, int(match.group(1)) + 1)
        card_id = f"CARD-{next_no:03d}"
        cdir = cards / card_id
        cdir.mkdir(parents=True, exist_ok=False)
        text = (f"# {title}\n\n"
                f"## 要做什么\n{do}\n\n"
                f"## 不做什么\n{not_do}\n\n"
                f"## 怎么算做完\n{done_when}\n")
        (cdir / "需求卡.md").write_text(text, encoding="utf-8")
        Card(card_id, state="待对齐").save(cdir / "状态.json")
        (cdir / "方案.md").write_text(PLAN_TEMPLATE, encoding="utf-8")
        (cdir / "来源.json").write_text(
            json.dumps({"conversation": conversation or ""}, ensure_ascii=False), encoding="utf-8")
        (cdir / "已汇报.json").write_text(
            json.dumps({"code": "new"}, ensure_ascii=False), encoding="utf-8")
        flow_record(cdir, "需求", src="你", dst="调度员", summary=title, text=text)
        render_board(collab)
        return card_id


def revise_card(project, card_id: str, text: str) -> None:
    """把新要求追加到需求卡。验收中或卡住时回到开发，写方案时保持待对齐。"""
    project = Path(project)
    cdir = project / ".collab" / "cards" / card_id
    state_path = cdir / "状态.json"
    if not state_path.is_file():
        raise TaskError("找不到这张卡片")
    words = (text or "").strip()
    if not words:
        raise TaskError("请写上要改什么")
    req = cdir / "需求卡.md"
    body = req.read_text(encoding="utf-8") if req.is_file() else ""
    if body and not body.endswith("\n"):
        body += "\n"
    req.write_text(body + f"\n## 追加要求\n{words}\n", encoding="utf-8")
    before = cdir / "测试前改动.json"
    if before.exists():
        before.unlink()
    card = Card.load(state_path)
    if card.state in ("待验收", "需Claude介入"):
        card.rework = 0
        card.transition("开发中")
        card.save(state_path)
    render_board(project / ".collab")
    flow_record(cdir, "追加要求", src="你", dst="调度员", summary=words[:80], text=words)
