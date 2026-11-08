"""流转记录：边干边记，给人（和桌面窗口）看“谁在干、到哪一步、彼此说了什么”。

每张卡一份 流转.jsonl，一行一条，只追加：
  {"seq": 3, "time": "2026-11-04T20:41:00", "kind": "派活", "src": "调度员", "dst": "测试员",
   "tool": "codex", "step": "写测试", "round": 1, "summary": "按验收标准写测试",
   "text": "流转/003-派活.md", "live": "流转/003-实时输出.log"}

kind 的取值：需求、批准、开跑、派活、回复、范围、判定、页面检查、转人工、结束、归档。
- 派活：开始干活时记（所以跑到一半就能读出谁在干）；回复：干完时记，reply_to 指向对应的派活。
- 全文（派活原文、回复全文、测试输出）存在 流转/ 目录，记录里只放一句话摘要和全文位置。
- 实时输出：派活时预留 流转/NNN-实时输出.log，执行者边干边往里写。
只记录，不参与任何判断：派活、返工、判定的规则都不在这里。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FLOW_FILE = "流转.jsonl"
TEXT_DIR = "流转"
SUMMARY_LIMIT = 80


def read_flow(card_dir) -> list:
    """按顺序读出全部记录；坏掉的行跳过，不让一行坏数据挡住整张卡。"""
    path = Path(card_dir) / FLOW_FILE
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            events.append(data)
    return events


def _next_seq(card_dir: Path) -> int:
    events = read_flow(card_dir)
    return max((e.get("seq", 0) for e in events), default=0) + 1


def record(card_dir, kind: str, src: str = "", dst: str = "", summary: str = "",
           text=None, live: bool = False, **extra) -> dict:
    """追加一条记录并返回它。text 给了就把全文另存；live=True 时预留一份实时输出文件。"""
    cdir = Path(card_dir)
    (cdir / TEXT_DIR).mkdir(parents=True, exist_ok=True)
    seq = _next_seq(cdir)
    event = {"seq": seq, "time": datetime.now().isoformat(timespec="seconds"), "kind": kind,
             "src": src, "dst": dst, "summary": summarize(summary)}
    event.update({k: v for k, v in extra.items() if v is not None})
    if text is not None:
        rel = f"{TEXT_DIR}/{seq:03d}-{kind}.md"
        (cdir / rel).write_text(str(text), encoding="utf-8")
        event["text"] = rel
    if live:
        rel = f"{TEXT_DIR}/{seq:03d}-实时输出.log"
        (cdir / rel).touch()
        event["live"] = rel
    with (cdir / FLOW_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def text_of(card_dir, event: dict) -> str:
    """取一条记录的全文；没有全文时返回摘要。"""
    rel = event.get("text")
    if rel:
        path = Path(card_dir) / rel
        if path.exists():
            return path.read_text(encoding="utf-8")
    return event.get("summary", "")


def working_now(events: list):
    """最近一次还没收到回复的派活，就是“正在干”的那一个；没有则返回 None。"""
    answered = {e.get("reply_to") for e in events if e.get("kind") == "回复"}
    for event in reversed(events):
        if event.get("kind") == "派活":
            return None if event.get("seq") in answered else event
    return None


def summarize(text, limit: int = SUMMARY_LIMIT) -> str:
    """一句话摘要：第一行有内容的文字，去掉标题符号，最长 80 字。"""
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line if len(line) <= limit else line[:limit - 1] + "…"
    return ""


_MARKERS = {"开发": "汇报：", "返工修改": "汇报：", "复查": "结论："}  # 只有被要求写这一行的岗位才找它


def reply_summary(step: str, text: str, ok: bool) -> str:
    """回复的一句话：开发员取“汇报：”，复查员取“结论：”，其他取最后一句有内容的话。
    （测试员读规则时可能引用“汇报：”那句话，不能被当成它的汇报——2026-11-04 实跑时出现过。）"""
    body = str(text or "")
    if not ok:
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        return "没有完成：" + (lines[-1] if lines else "没有返回任何内容")
    marker = _MARKERS.get(step)
    if marker and marker in body:
        tail = body.rsplit(marker, 1)[1].strip().splitlines()
        return marker + (tail[0].strip() if tail else "")
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    return lines[-1] if lines else "（没有回复文字）"


_INTERNAL_HEADINGS = ("务必先看", "执行员", "不要调用", "注意事项", "重要提示")


def card_title(cdir, card_id: str) -> str:
    """卡片标题只有这一种取法：左栏、聊天、汇报都用它。跳过写给执行员看的内部叮嘱。"""
    cdir = Path(cdir)
    for ev in read_flow(cdir):
        if ev.get("kind") == "需求" and (ev.get("summary") or "").strip():
            cand = ev["summary"].strip()
            if not any(k in cand for k in _INTERNAL_HEADINGS):
                return cand
    path = cdir / "需求卡.md"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                cand = s.lstrip("#").strip()
                if cand and not any(k in cand for k in _INTERNAL_HEADINGS):
                    return summarize(cand)
        title = summarize(text)
        if title:
            return title
    return card_id
