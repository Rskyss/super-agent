"""实时进度：让人能看出施工有没有在动。只读，不改任何文件。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from collab.state import Card

QUIET_WARN_SECONDS = 10 * 60  # 超过 10 分钟没有任何文件变化，提示可能卡住
TERMINAL_STATES = {"待验收", "已合并", "需Claude介入", "已放弃"}


def _fmt(delta) -> str:
    total = max(0, int(delta.total_seconds()))
    return f"{total // 60} 分 {total % 60} 秒"


def _start_time(collab: Path, card_id: str):
    ledger = collab / "备份记录.md"
    if not ledger.exists():
        return None
    start = None
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if card_id in line:
            first = line.split("|")[1].strip()
            try:
                start = datetime.strptime(first, "%Y-%m-%d %H:%M")
            except ValueError:
                pass
    return start


def _last_activity(worktree: Path):
    latest = None
    for root, dirs, files in os.walk(worktree):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
        for name in files:
            mtime = os.path.getmtime(os.path.join(root, name))
            if latest is None or mtime > latest:
                latest = mtime
    return datetime.fromtimestamp(latest) if latest else None


def snapshot(project_dir, card_id: str, now=None) -> str:
    now = now or datetime.now()
    project = Path(project_dir)
    collab = project / ".collab"
    cdir = collab / "cards" / card_id
    card = Card.load(cdir / "状态.json")

    lines = [f"{card_id}：当前状态「{card.state}」，返工 {card.rework} 次"]
    start = _start_time(collab, card_id)
    if start:
        lines.append(f"已用时 {_fmt(now - start)}")

    worktree = project.parent / f"{project.name}-worktrees" / card_id
    last = _last_activity(worktree) if worktree.exists() else None
    if last:
        quiet = now - last
        lines.append(f"最近动静：{_fmt(quiet)}前")
        if card.state not in TERMINAL_STATES and quiet.total_seconds() > QUIET_WARN_SECONDS:
            lines.append("⚠ 超过 10 分钟没有任何文件变化，可能卡住，请留意")
    else:
        lines.append("最近动静：还没有任何文件变化")

    log = cdir / "执行记录.md"
    if log.exists():
        recent = log.read_text(encoding="utf-8").splitlines()[-3:]
        lines.append("最近步骤：")
        lines.extend(recent)
    return "\n".join(lines)
