"""规则包读取器（docs/规则包/，用户 2026-11-20 批准）。

给大模型的规则都在仓库最外层的 agents/ 里，按角色和手册分成多份 md；agents/README.md 里的
“谁读哪几份”表登记每个角色读哪些文件。程序派活时用 rules_for(角色) 把这些文件原样拼成一段，
放在派活的话最前面：模型不用自己翻文件；每次一字不差，服务能记住重复的开头，第二轮起更便宜。

读不到文件不报错停卡，而是在拼出来的规则里写明“规则文件缺失”，并由 run_card 在流转里记一条提醒，
不悄悄用空规则跑。装好的应用里 agents/ 和 collab/ 并排放在 super-agent/ 下（desktop/mac/build.sh）。
"""
from __future__ import annotations

import re
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
INDEX = "README.md"
ROLE_TABLE = "谁读哪几份"
MUST_TABLE = "不能删的要点"
BANNED_TABLE = "不能再出现的说法"

# 程序派活用到的角色（dispatch.py、planner.py 里 _rules("…") 的那几个，tests/test_rulebook.py 守着两边一致）。
# 目录表不管坏成什么样，这几个角色找不到或没写文件，都按“规则文件缺失”报（Codex 复查第 2 轮）。
PROGRAM_ROLES = ("写测试", "写代码", "复查", "写方案", "备交接资料", "起草交代")

_HEAD = "【规则】下面是你这个角色要守的规则（来自规则包 agents/，按角色挑好的）。和后面的具体交代冲突时，以具体交代为准。\n\n"
_TAIL = "【规则完】\n\n"


def _root(root) -> Path:
    return Path(root) if root is not None else AGENTS_DIR


def _read(path: Path):
    """读不到或只有空白都算没有（空文件和缺文件一样要报出来，Codex 复查第 1 轮第 1 条）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text if text.strip() else None


def table(text: str, heading: str) -> list:
    """取“## 标题”下面第一张表的数据行（去掉表头和分隔行），每行是去掉首尾空白的格子列表。"""
    rows, inside, seen_table = [], False, False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == heading
            continue
        if not inside:
            continue
        stripped = line.strip()
        if stripped.startswith("|"):
            seen_table = True
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue
            rows.append(cells)
        elif seen_table:
            break
    return rows[1:]  # 第一行是表头


def roles(root=None) -> dict:
    """角色名 → 按顺序要读的文件（相对 agents/）。目录文件读不到时返回空。"""
    text = _read(_root(root) / INDEX)
    if text is None:
        return {}
    found = {}
    for cells in table(text, ROLE_TABLE):
        if len(cells) >= 2 and cells[0]:
            found[cells[0]] = [f.strip() for f in re.split(r"[、,，]", cells[1]) if f.strip()]
    return found


def _missing(rel: str) -> str:
    return f"（规则文件缺失：{rel}。按你知道的通用规矩干活，并在回复里写一句“规则文件缺失”。）"


def rules_for(role: str, root=None) -> str:
    """这个角色要读的规则全文，带开头结尾标记，结尾空一行，直接拼在派活的话前面。"""
    base = _root(root)
    table_ = roles(root)
    if not table_:
        parts = [_missing(INDEX)]
    elif not table_.get(role):
        parts = [_missing(f"目录表里没有“{role}”这一行或没写文件")]
    else:
        parts = []
        for rel in table_[role]:
            body = _read(base / rel)
            parts.append(body.strip() if body is not None else _missing(rel))
    return _HEAD + "\n\n".join(parts) + "\n\n" + _TAIL


def missing_files(root=None) -> list:
    """登记了却读不到的文件（目录文件本身读不到时只报它）。"""
    base = _root(root)
    if _read(base / INDEX) is None:
        return [INDEX]
    table_ = roles(root)
    seen = [f"目录表里没有“{role}”这一行或没写文件" for role in PROGRAM_ROLES if not table_.get(role)]
    for files in table_.values():
        for rel in files:
            if rel not in seen and _read(base / rel) is None:
                seen.append(rel)
    return seen


def health(root=None) -> list:
    """规则包体检：返回发现的问题（大白话），空列表＝健康。"""
    base = _root(root)
    index = _read(base / INDEX)
    if index is None:
        return [f"规则包目录文件 {INDEX} 读不到"]
    problems = [f"登记了但读不到：{rel}" for rel in missing_files(root)]
    registered = {rel for files in roles(root).values() for rel in files}
    for path in sorted(base.rglob("*.md")):
        rel = path.relative_to(base).as_posix()
        if rel != INDEX and rel not in registered:
            problems.append(f"没登记进“{ROLE_TABLE}”表的文件：{rel}（不登记就没人读）")
    for cells in table(index, MUST_TABLE):
        if len(cells) < 2:
            continue
        rel, words = cells[0], cells[1].replace("\\|", "|")
        body = _read(base / rel)
        if body is None:
            problems.append(f"要点所在的文件读不到：{rel}（要点：{words}）")
        elif words not in body:
            problems.append(f"{rel} 里少了要点“{words}”（防：{cells[2] if len(cells) > 2 else ''}）")
    banned = [re.sub(r"\s", "", cells[0]) for cells in table(index, BANNED_TABLE) if cells and cells[0]]
    for path in sorted(base.rglob("*.md")):
        rel = path.relative_to(base).as_posix()
        if rel == INDEX:
            continue
        flat = re.sub(r"\s", "", _read(path) or "")
        for words in banned:
            if words and words in flat:
                problems.append(f"{rel} 里出现了不该再有的说法“{words}”")
    return problems
