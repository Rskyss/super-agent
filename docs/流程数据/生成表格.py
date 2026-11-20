"""把每张卡流转记录里的“谁干了哪一步、用了多久、用了多少 token、交没交成”整理成表（用户 11-19 要求：攒数据看怎么优化流程）。

用法（在开发助手项目根目录）：python3 docs/流程数据/生成表格.py
输出：docs/流程数据/各卡用时.md（每次全量重写，数据都来自 .collab/cards/*/流转.jsonl，不手改）。
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / ".collab" / "cards"
OUT = Path(__file__).resolve().parent / "各卡用时.md"


def events_of(cdir):
    out = []
    try:
        for line in (cdir / "流转.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def title_of(cdir, cid):
    try:
        for line in (cdir / "需求卡.md").read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return cid


def state_of(cdir):
    try:
        return json.loads((cdir / "状态.json").read_text(encoding="utf-8")).get("state", "")
    except (OSError, ValueError):
        return ""


def mins(sec):
    return f"{sec / 60:.1f}"


def wan(n):
    return f"{n / 10000:.1f} 万" if n else "—"


def rows_of(events):
    """每次派活配上它的回复：岗位、步骤、工具、模型、用时、token、结果。"""
    tasks = {e.get("seq"): e for e in events if e.get("kind") == "派活"}
    rows = []
    for e in events:
        if e.get("kind") != "回复" or e.get("seconds") is None:
            continue
        t = tasks.get(e.get("reply_to")) or {}
        tok = e.get("tokens") or {}
        rows.append({
            "role": e.get("src") or "", "step": e.get("step") or t.get("step") or "",
            "round": e.get("round") or t.get("round") or "",
            "tool": e.get("tool") or t.get("tool") or "", "model": t.get("model") or "（没记）",
            "sec": float(e.get("seconds") or 0), "tokens": int(tok.get("total") or 0) if isinstance(tok, dict) else 0,
            "ok": e.get("ok") is True, "summary": str(e.get("summary") or "")[:40].replace("|", "／"),
        })
    return rows


def main():
    cards = sorted(p for p in CARDS.glob("CARD-*") if p.is_dir())
    agg = defaultdict(lambda: {"n": 0, "sec": 0.0, "tokens": 0, "ok": 0})
    lines = ["# 各卡用时（自动生成，别手改）", "",
             f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}（按本机时间）。数据来自每张卡的流转记录；"
             "“用时”是那一步自己报的耗时，不受时区影响。", "",
             "## 一、按“步骤 + 工具 + 模型”汇总", "",
             "（见文末，先列每张卡的明细）", ""]
    detail = ["## 二、每张卡的明细", ""]
    for cdir in cards:
        cid = cdir.name
        ev = events_of(cdir)
        rows = rows_of(ev)
        total_sec = sum(r["sec"] for r in rows)
        total_tok = sum(r["tokens"] for r in rows)
        returns = sum(1 for e in ev if e.get("kind") == "退回")
        stops = sum(1 for e in ev if e.get("kind") == "转人工")
        detail.append(f"### {cid} {title_of(cdir, cid)}")
        detail.append("")
        detail.append(f"状态：{state_of(cdir)} · 干活合计 {mins(total_sec)} 分钟 · {wan(total_tok)} token · "
                      f"复查退回 {returns} 次 · 停下转人工 {stops} 次")
        detail.append("")
        if rows:
            detail.append("| 步骤 | 第几轮 | 谁 | 工具 · 模型 | 用时（分） | token | 交成了 | 一句话 |")
            detail.append("|---|---|---|---|---|---|---|---|")
            for r in rows:
                detail.append(f"| {r['step']} | {r['round']} | {r['role']} | {r['tool']} · {r['model']} | "
                              f"{mins(r['sec'])} | {wan(r['tokens'])} | {'是' if r['ok'] else '否'} | {r['summary']} |")
                key = (r["step"], r["tool"], r["model"])
                a = agg[key]
                a["n"] += 1
                a["sec"] += r["sec"]
                a["tokens"] += r["tokens"]
                a["ok"] += 1 if r["ok"] else 0
        else:
            detail.append("（没有带用时的派活记录）")
        detail.append("")
    summary = ["| 步骤 | 工具 · 模型 | 次数 | 平均用时（分） | 合计用时（分） | 平均 token | 交成率 |",
               "|---|---|---|---|---|---|---|"]
    for (step, tool, model), a in sorted(agg.items(), key=lambda kv: (kv[0][0], -kv[1]["sec"])):
        summary.append(f"| {step} | {tool} · {model} | {a['n']} | {mins(a['sec'] / a['n'])} | {mins(a['sec'])} | "
                       f"{wan(a['tokens'] // a['n'])} | {a['ok']}/{a['n']} |")
    lines = lines[:5] + summary + [""] + detail
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写 {OUT}，{len(cards)} 张卡")


if __name__ == "__main__":
    main()
