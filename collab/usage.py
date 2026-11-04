"""额度记录：从 Claude 会话记录（JSONL）汇总 token 用量，按卡片记账。

四项都要计入：输入、缓存写入、缓存读取、输出。只看输出会严重低估（D2）。
同一条消息在流式输出时会重复写多行，按 message.id 只计一次。
"""
from __future__ import annotations

import json
from pathlib import Path

_FIELDS = {
    "input": "input_tokens",
    "cache_creation": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
    "output": "output_tokens",
}
_LEDGER = "额度记录.md"


def count_lines(path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    with p.open(encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def usage_since(path, line_offset: int) -> dict:
    """汇总 JSONL 中从第 line_offset 行开始（不含）的用量。"""
    totals = {key: 0 for key in _FIELDS}
    p = Path(path)
    if p.exists():
        seen = set()
        with p.open(encoding="utf-8", errors="replace") as fh:
            for index, line in enumerate(fh):
                if index < line_offset:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                usage = message.get("usage") or obj.get("usage")
                if not isinstance(usage, dict):
                    continue
                msg_id = message.get("id")
                if msg_id is not None:
                    if msg_id in seen:
                        continue
                    seen.add(msg_id)
                for key, field in _FIELDS.items():
                    totals[key] += int(usage.get(field) or 0)
    totals["total"] = sum(totals[k] for k in _FIELDS)
    return totals


def record_card_usage(collab_dir, card_id: str, usage: dict) -> None:
    ledger = Path(collab_dir) / _LEDGER
    if not ledger.exists():
        ledger.write_text("# 额度记录\n\n| 卡片 | 输入 | 缓存写入 | 缓存读取 | 输出 | 合计 |\n"
                          "|---|---|---|---|---|---|\n", encoding="utf-8")
    row = (f"| {card_id} | {usage['input']} | {usage['cache_creation']} | "
           f"{usage['cache_read']} | {usage['output']} | {usage['total']} |\n")
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(row)


def find_latest_session(base: Path):
    """找最近一次写入的 Claude 会话记录（就是正在调度的这一个）。没有就返回 None。"""
    base = Path(base)
    if not base.is_dir():
        return None
    sessions = [p for p in base.glob("*/*.jsonl") if p.is_file()]
    return max(sessions, key=lambda p: p.stat().st_mtime) if sessions else None
