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
import re
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
    # 取序号到写完一整条要锁住（跨进程）：施工进程起草时，本机服务同时记“接受”“结束”，不能撞同一个序号（第四轮复查）
    import fcntl
    with open(cdir / ".流转.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _record_locked(cdir, kind, src, dst, summary, text, live, extra)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _record_locked(cdir: Path, kind, src, dst, summary, text, live, extra) -> dict:
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


# ---- 复查意见说给人听（流程修理第二轮 ㉞ ㊲；派活和卡片右上角、确认框共用）----
_VERDICT_LINE = re.compile(r"^[ \t*#]*结论")
_ITEM = re.compile(r"^\s*(\d+)[.、)）]\s*(.+)$")
TEST_PROBLEM = "【测试的问题】"


def _numbered_items(text: str, whole: bool = False) -> list:
    """复查意见里编号的那几条。whole=True 时连同下面接着写的原因和位置（交给写测试的要用全文，复查 B 第 4 条）。"""
    items, state = [], ""  # state：open（这一条刚开始，还没空行）/ blank（空过一行）/ ""（已结束）
    label = re.compile(r"^\s*(?:原因|位置|影响|建议|复现|修法|为什么|现象|依据|改法|出处)\s*[：:]")
    summary = re.compile(r"指定测试|测试命令|共\s*\d+\s*项|全部通过|未修改|未跑全量|核对表|^\s*验证\s*[：:]|"
                         r"^\s*\*\*[^*]+\*\*\s*$")
    for line in (text or "").splitlines():
        m = _ITEM.match(line)
        if m:
            items.append(m.group(2).strip())
            state = "open"
            continue
        if not line.strip():
            state = "blank" if state else ""
            continue
        if not (items and whole and state) or _VERDICT_LINE.match(line):
            continue
        indented = line[:1] in (" ", "\t", "　") or line.startswith(("- ", "* ", "• "))
        if label.match(line) or indented:
            items[-1] += "\n" + line.rstrip()   # 缩进的、原因位置这类标签行一律算这一条（第 3 轮第 3 条：不按关键词误删）
            continue
        if summary.search(line):
            state = ""        # 顶格的总结（测了多少项、有没有改文件、粗体小标题）不算这一条（第 2 轮第 3 条）
            continue
        if state == "open":
            items[-1] += "\n" + line.rstrip()   # 原因、位置这类标签行不管顶格还是隔了空行都算这一条
        else:
            state = ""        # 空行后顶格写的别的话：这一条结束
    return items


# 中文和英文字母之间 \b 不算边界（第 3 轮 B 第 3 条），用“前后不是英文数字”来认
_A = r"(?<![A-Za-z0-9_])"
_Z = r"(?![A-Za-z0-9_])"
_CODE = re.compile(r"`|(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_-]+\.[A-Za-z]{1,5}(?::\d+)?" + _Z +
                   r"|" + _A + r"[A-Za-z_][A-Za-z0-9_.]*\([^()]*\)"
                   r"|" + _A + r"[A-Za-z0-9]*_[A-Za-z0-9_]+" + _Z +
                   r"|" + _A + r"[a-z]+[A-Z][A-Za-z0-9]*" + _Z)


def _plain(item: str, limit: int = 60) -> str:
    """一条复查意见的第一句。去掉文件链接、加粗、级别标记；第一句里还带文件名、函数名、代码名的不算大白话，返回空
    （硬删会剩下半句话，第 2 轮 B 第 4 条）。"""
    item = re.sub(r"\[([^\]]*)\]\([^)]*\)", "", item)          # [tasks.py:587](/路径) 这类链接整个去掉
    item = re.sub(r"\*\*|【测试的问题】", "", item)
    item = re.sub(r"^\s*\[?P\d(?:[ ／/·|｜]*[^\]：:｜|\s]{0,4})?\]?\s*[：:｜|]?\s*", "", item.strip())  # [P1]、P1／高：
    item = re.sub(r"^[\[（(【]?(?:P\d|严重|中等|轻微|高|中|低)[^\]）)】]{0,8}[\]）)】]\s*", "", item)
    item = re.split(r"[。！？!?]", item, maxsplit=1)[0].strip(" ：:；;，,、")
    if _CODE.search(item):
        return ""
    return item if len(item) <= limit else item[:limit - 1] + "…"


def _plain_enough(text: str) -> bool:
    """去掉代码名后还剩一句像样的中文才算（第 2 轮 B 第 4 条）；剩不下就不拿它当大白话。"""
    return len(re.findall(r"[\u4e00-\u9fff]", text)) >= 4


def plain_issues(text: str) -> list:
    """复查挑出的事，每条一句大白话：只取第一句，去掉文件链接、加粗、级别标记（流程修理第二轮 ㉞ ㊲）。"""
    return [p for p in (_plain(i) for i in _numbered_items(text)) if p and _plain_enough(p)]


def rework_words(rework: int, card_dir) -> str:
    """返工次数的说法（第四轮 58）：测试没过的返工、复查自动退回、用户继续改分开数。
    69b 只写“返工 0 次”，其实复查退回了 3 次、继续改 1 次。"""
    events = read_flow(card_dir)
    returns = sum(1 for e in events if e.get("kind") == "退回")
    revises = sum(1 for e in events if e.get("kind") == "追加要求")
    # 方案定的口径：复查退回 = 自动退回 + 你点“继续改”重开（69b 算 4 次）
    words = f"测试没过返工 {rework} 次、复查退回 {returns + revises} 次"
    return words + (f"（其中你继续改 {revises} 次）" if revises else "")
