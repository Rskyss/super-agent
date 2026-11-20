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
MSG_TABLE = "交代"   # 第③步：派活时的具体交代（docs/规则包/方案-第③步.md）
_SLOT = re.compile(r"\{([\u4e00-\u9fffA-Za-z0-9_]+)\}")

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


def messages(root=None) -> dict:
    """交代名 → (文件, [空位])。目录文件读不到时返回空。"""
    text = _read(_root(root) / INDEX)
    if text is None:
        return {}
    found = {}
    for cells in table(text, MSG_TABLE):
        if len(cells) >= 3 and cells[0]:
            slots = [x.strip() for x in re.split(r"[、,，]", cells[2]) if x.strip() and x.strip() != "—"]
            found[cells[0]] = (cells[1], slots)
    return found


def _has_separator(text: str) -> bool:
    return "---" in text.splitlines()


def _body(text: str) -> str:
    """`---` 以下是发给模型的话：连着的几行直接接起来，一个空行＝换一行（文件末尾的空行也算）。"""
    lines = text.splitlines()
    if "---" in lines:
        lines = lines[lines.index("---") + 1:]
    out, run = [], []
    for line in lines:
        if line.strip():
            run.append(line)
        else:
            out.append("".join(run))
            run = []
    out.append("".join(run))
    return "\n".join(out)


def say(name: str, root=None, **slots) -> str:
    """一份交代：读 agents/ 里登记的文件，往空位里填程序给的东西。读不到、空位没填满时，在话里写明，不悄悄发半截话。"""
    entry = messages(root).get(name)
    text = _read(_root(root) / entry[0]) if entry else None
    if text is None:
        where = entry[0] if entry else f"目录里没有登记“{name}”"
        return f"（交代文件缺失：{where}。按你知道的通用规矩干活，并在回复里写一句“交代文件缺失”。）"
    if not _has_separator(text):   # 分隔线没了：分不出哪些是给人看的说明，不能整份发给模型（Codex 复查 T1 第 3 条）
        return (f"（交代文件有问题：{entry[0]} 缺“---”分隔线，分不出发给你的话。"
                "按你知道的通用规矩干活，并在回复里写一句“交代文件有问题”。）")
    left = []
    wrong = sorted(set(entry[1]) - set(slots)) + sorted(set(slots) - set(entry[1]))   # 按登记表核对（第 1 条）

    def fill(m):
        key = m.group(1)
        if key in slots:
            return str(slots[key])
        left.append(key)
        return m.group(0)

    body = _SLOT.sub(fill, _body(text))
    bad = list(dict.fromkeys(left + wrong))
    if bad:
        body += f"（交代文件有问题：{entry[0]} 的空位“{'、'.join(bad)}”和登记表对不上，请在回复里写一句。）"
    return body


def file_text(rel: str, root=None) -> str:
    """整份原样读一个规则文件（积木库管家的身份说明、手册）；读不到时写明缺失，不悄悄给空。"""
    text = _read(_root(root) / rel)
    return text if text is not None else _missing(rel)


def tool_notes(rel: str, root=None) -> dict:
    """工具说明表（交代文件 --- 以下的“| 工具 | 参数 | 说明 |”表）→ {(工具, 参数或""): 说明}。读不到返回空。"""
    text = _read(_root(root) / rel)
    if text is None or not _has_separator(text):
        return {}
    notes, header = {}, True
    raw = text.splitlines()
    for line in raw[raw.index("---") + 1:]:   # 表格一行一行读，不按“连着的行接起来”处理
        cells = [c.strip().replace("\0", "|") for c in line.replace("\\|", "\0").strip().strip("|").split("|")]   # \| 是字面的竖线
        if len(cells) < 3 or not line.strip().startswith("|"):
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if header:
            header = False
            continue
        if cells[2]:   # 说明格空着就算没有（Codex 复查第 2 轮）
            notes[(cells[0], "" if cells[1] in ("—", "-") else cells[1])] = cells[2]
    return notes


_TOOL_TABLES: dict = {}   # 工具说明表 → 程序里实际有的 (工具, 参数)；体检按它核对缺行（第③步）


def describe_tools(specs: list, rel: str, root=None) -> list:
    """给工具定义填上说明（说明写在 agents/ 的工具说明表里，程序只管工具名、参数名，第③步）。
    键的先后照原来（name、description、parameters；参数里 type 在前），发给模型的样子一字不差。"""
    notes = tool_notes(rel, root)
    keys = _TOOL_TABLES.setdefault(rel, set())
    for spec in specs:
        fn0 = spec.get("function", {})
        keys.add((fn0.get("name", ""), ""))
        keys.update((fn0.get("name", ""), p) for p in (fn0.get("parameters", {}).get("properties", {}) or {}))
    for spec in specs:
        fn = spec.get("function", {})
        name = fn.get("name", "")
        rest = {k: v for k, v in fn.items() if k not in ("name", "description")}
        fn.clear()
        fn["name"] = name
        gap = f"（工具说明缺失：见 agents/{rel}，按工具名和参数名理解用法）"   # 删了一行不悄悄变空（Codex 复查）
        fn["description"] = notes.get((name, ""), gap)
        fn.update(rest)
        for pname, pspec in (fn.get("parameters", {}).get("properties", {}) or {}).items():
            pspec["description"] = notes.get((name, pname), gap)
    return specs


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
    for rel, _slots in messages(root).values():
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
    registered |= {rel for rel, _slots in messages(root).values()}
    for name, (rel, slots) in messages(root).items():
        body = _read(base / rel)
        if body is None:
            continue
        if not _has_separator(body):
            problems.append(f"{rel} 缺“---”分隔线（单独一行），分不出给人看的说明和发给模型的话")
            continue
        if not _body(body).strip():
            problems.append(f"{rel} 的“---”下面是空的，发给模型的话没了")
        if _body(body).lstrip().startswith("| 工具 |"):
            continue   # 工具说明表没有空位，说明里的花括号是原样的字（比如“{prompt}”）
        used = set(_SLOT.findall(_body(body)))
        for key in sorted(set(slots) - used):
            problems.append(f"{rel} 里少了空位“{{{key}}}”（目录登记了，程序会填，删了模型就收不到）")
        for key in sorted(used - set(slots)):
            problems.append(f"{rel} 里多了空位“{{{key}}}”（目录没登记，程序填不了）")
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
    try:   # 行政、积木库管家的工具定义：导入时会登记“程序里有哪些工具”，体检据此查缺行
        import desktop.clerk.blocks_tools  # noqa: F401
        import desktop.clerk.tools  # noqa: F401
    except Exception:  # noqa: BLE001 — 只装了跑卡部分时不查这一项
        pass
    for rel, keys in sorted(_TOOL_TABLES.items()):
        notes = tool_notes(rel, root)
        for name, param in sorted(keys - set(notes)):
            problems.append(f"{rel} 里少了工具说明：{name}{('.' + param) if param else ''}（这一行删了或说明空着）")
    banned = []
    for cells in table(index, BANNED_TABLE):
        if cells and cells[0]:
            skip = {x.strip() for x in re.split(r"[、,，]", cells[2])} if len(cells) > 2 else set()
            banned.append((re.sub(r"\s", "", cells[0]), skip))
    checked = sorted({rel for files in roles(root).values() for rel in files}
                     | {rel for rel, _slots in messages(root).values()})
    for rel in checked:   # 规则和交代都查（Codex 复查 T2～T6 第 1 条），例外一栏写的文件跳过这一条
        flat = re.sub(r"\s", "", _read(base / rel) or "")
        for words, skip in banned:
            if words and rel not in skip and words in flat:
                problems.append(f"{rel} 里出现了不该再有的说法“{words}”")
    return problems
