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


def _clean_scope_files(files) -> list:
    """“继续改”框里写的文件（第三批 ㉟）：只认项目里的相对路径，不许绝对路径、..、.collab。"""
    out = []
    for raw in files or []:
        rel = str(raw or "").strip().replace("\\", "/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not rel or rel.startswith("/") or ".." in parts or not parts or parts[0] == ".collab":
            raise TaskError(f"“{raw}”不是项目里的文件路径：写成相对项目的路径，比如 collab/abilities_run.py")
        rel = "/".join(parts)
        if rel not in out:
            out.append(rel)
    return out


def _add_scope(body: str, files: list) -> str:
    """把文件加进“允许改动的范围”那一节的末尾；没有这一节就新建。已有的不重复加。"""
    from collab.scope import SCOPE_HEADING, read_allowed
    have = set(read_allowed(body) or [])
    new = [f for f in files if f not in have]
    if not new:
        return body
    lines = "".join(f"- {f}\n" for f in new)
    if SCOPE_HEADING not in body:
        return body.rstrip("\n") + f"\n\n{SCOPE_HEADING}\n{lines}"
    head, rest = body.split(SCOPE_HEADING, 1)
    nxt = rest.find("\n## ")
    section, tail = (rest, "") if nxt < 0 else (rest[:nxt + 1], rest[nxt + 1:])
    section = section.rstrip("\n") + "\n" + lines + ("\n" if tail else "")
    return head + SCOPE_HEADING + section + tail


def revise_card(project, card_id: str, text: str, files=None) -> None:
    """把新要求追加到任务卡片。验收中或卡住时回到开发，写方案时保持待对齐。
    files：还要改的文件，加进允许改动的范围（写了就算用户点头，记“追加范围”，第三批 ㉟）。"""
    project = Path(project)
    cdir = project / ".collab" / "cards" / card_id
    state_path = cdir / "状态.json"
    if not state_path.is_file():
        raise TaskError("找不到这张卡片")
    words = (text or "").strip()
    if not words:
        raise TaskError("请写上要改什么")
    scope = _clean_scope_files(files)  # 不合规矩的在动任何东西之前就退回
    req = cdir / "需求卡.md"
    body = req.read_text(encoding="utf-8") if req.is_file() else ""
    if scope:
        body = _add_scope(body, scope)
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
    if scope:
        flow_record(cdir, "追加范围", src="你", dst="调度员", files=scope,
                    summary=f"加进允许改动的范围：{'、'.join(scope)}")
    flow_record(cdir, "追加要求", src="你", dst="调度员", summary=words[:80], text=words)
