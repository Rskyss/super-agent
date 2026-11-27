"""已知没过名单（跑卡卡点修理 卡 B2′，11-27 缩小版）：架构师提前放进项目、等别的卡来做、本来就该不过的测试（等卡），
事先登记在这里；全部测试这一关只对名单，不猜。名单在主项目的 .collab/已知没过.json，不进代码仓库。

一条：file（测试文件，整个文件）、tests（固定为空）、kind（只有“等卡”）、card（等哪张卡）、by、at、why。
只能登记整个文件：全部测试没过时，程序在副本里把这些文件拿掉、原样再跑一遍，拿掉后全过才放行。
"""
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from collab.ledger import ledger_add

FILE_NAME = "已知没过.json"
KINDS = ("等卡",)   # 卡 B2′（11-27 缩小版）：只认“等卡”，不认“偶发”、不重跑


def _path(project) -> Path:
    return Path(project) / ".collab" / FILE_NAME


def _valid(item) -> bool:
    """写坏的条目不算（只会多挡、不会多放）：文件、种类要对，tests 必须是一串不空的名字，等卡要写卡号。"""
    if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not item["file"].strip():
        return False
    if item.get("tests") not in ([], None) or not _safe_rel(item["file"]):
        return False   # 只能登记整个文件（卡 B2′ 拿掉文件再跑），路径只认项目里的相对路径
    if item.get("kind") not in KINDS:
        return False
    # 每条都要能查清谁在何时因为什么登记（复查第 3 轮中等 5）：缺一个或类型不对就是写坏的条目
    for field in ("by", "at", "why"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            return False
    return item["kind"] != "等卡" or (isinstance(item.get("card"), str) and bool(item["card"].strip()))


_TEST_FILE = re.compile(r"(^test_.+\.py$|.+_test\.py$|.+\.(test|spec)\.[cm]?[jt]sx?$)")


def _safe_rel(rel: str) -> bool:
    """只认项目里的一个测试文件：相对路径、不出项目、文件名像测试文件（test_x.py、x_test.py、x.test.ts、x.spec.js……），
    不认目录、包入口（__init__.py）、conftest.py 这类会让别的测试跟着消失的文件（复查第 2 轮）。"""
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
    return (bool(parts) and not str(rel).startswith("/") and not str(rel).endswith("/") and ".." not in parts
            and parts[0] != ".collab" and bool(_TEST_FILE.match(parts[-1])))


def _card_done(project, card_id: str) -> bool:
    """等的那张卡已经放进项目（或放弃了）：这条不再生效，哪怕撤单那一步没成功（复查第 1 轮 9）。"""
    try:
        state = json.loads((Path(project) / ".collab" / "cards" / card_id / "状态.json")
                           .read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError, AttributeError):
        return False
    return state in ("已合并", "已放弃")


class LedgerMissed(OSError):
    """名单已经换好、只是台账没记上（撤单时）：提示要说“已撤”，不是“没撤”。"""


class Broken(ValueError):
    """名单文件在、却读不懂（写坏了、有写错的条目）：全部测试这关停下，增删也不动它，免得把原件盖掉（复查第 2 轮 9）。"""


def _read_all(project) -> list:
    """没有名单文件 = 空名单；文件在却读不懂、或有一条写错 → Broken（不能当空名单悄悄放宽，也不能丢掉写错的那条）。"""
    path = _path(project)
    if path.is_symlink():   # 包括指向不存在的文件：不跟着读、不覆盖（复查第 3 轮严重 8）
        raise Broken(f"已知没过名单是符号链接（{path}），先换回普通文件")
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("items")
    except (OSError, ValueError, AttributeError) as exc:
        raise Broken(f"已知没过名单读不懂（{path}）：{exc}") from None
    if not isinstance(items, list) or not all(_valid(i) for i in items):
        raise Broken(f"已知没过名单里有写错的条目（{path}），先手动修好")
    return items


def load(project) -> list:
    """读名单里还生效的条目（等的卡已放进项目或放弃的不算）；没有名单 = 空；读不懂抛 Broken。"""
    path = _path(project)
    if not path.exists() and not path.is_symlink():
        return []   # 没登记过名单：不建锁、不碰 .collab，老行为一字不变（复查第 2 轮）
    # 和增删用同一把锁（共享锁）：台账还没记成功、名单可能要回滚时，关卡不能先看到新登记（复查第 3 轮严重 9）
    with _locked(project, shared=True):
        return [i for i in _read_all(project) if not (i["kind"] == "等卡" and _card_done(project, i["card"]))]


@contextmanager
def _locked(project, shared: bool = False):
    """增删名单时整个项目一把锁：两处同时改（登记和放进项目撤单）不会把撤掉的又写回来。"""
    import fcntl
    path = _path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        fh = open(lock, "a+")
    except PermissionError:
        if not shared:
            raise
        fh = open(lock, "r")   # 读名单只要共享锁：锁文件只读也能读（复查第 3 轮）
    with fh:
        fcntl.flock(fh, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _save(project, items: list) -> None:
    """先写临时文件再换上去：写到一半出错不会留下半份名单。"""
    path = _path(project)
    fd, tmp = tempfile.mkstemp(prefix=".已知没过-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _describe(item: dict) -> str:
    what = "、".join(item.get("tests") or []) or "整个文件"
    tail = f"等 {item['card']}" if item.get("kind") == "等卡" else "偶发"
    return f"{item['file']}（{what}，{tail}）"


def add(project, file: str, tests, kind: str, card: str = None, why: str = "", by: str = "Claude") -> dict:
    file = str(file or "").strip()
    if tests:
        raise ValueError("名单只能登记整个测试文件（全部测试没过时会把这些文件拿掉再跑一遍），不能只登记其中几项")
    tests = []
    if file and not _safe_rel(file):
        raise ValueError("文件要写项目里的相对路径，比如 tests/accept.test.ts")
    cur = Path(project)
    for part in Path(file).parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError(f"路径中间的“{part}”是快捷方式（符号链接），不能登记，写实际的路径")
    card = str(card or "").strip() or None
    if not file:
        raise ValueError("要写是哪个测试文件")
    if kind not in KINDS:
        raise ValueError(f"种类只能是：{'、'.join(KINDS)}")
    if kind == "等卡" and not card:
        raise ValueError("“等卡”要写等哪张卡")
    if not str(why or "").strip():
        raise ValueError("要写为什么登记")
    item = {"file": file, "tests": tests, "kind": kind, "card": card if kind == "等卡" else None,
            "by": by, "at": f"{datetime.now():%Y-%m-%d %H:%M}", "why": str(why).strip()}
    with _locked(project):
        items = _read_all(project)
        _save_logged(project, items, items + [item], [f"已知没过名单登记：{_describe(item)}，原因：{item['why']}"])
    return item


def _save_logged(project, old: list, new: list, notes: list) -> None:
    """先记台账、再换名单（卡 B2′，复查第 4 轮：反过来的话，换完名单、记台账前进程被杀，豁免就悄悄生效了）。
    台账记不上就不换名单；换名单失败，台账补一行“没成”。"""
    for note in notes:
        ledger_add(project, note)
    try:
        _save(project, new)
    except BaseException:
        try:
            ledger_add(project, "（上一行没成：名单文件没写进去，名单还是原样）")
        except BaseException:  # noqa: BLE001
            pass
        raise


def _drop(project, keep, note: str) -> int:
    """撤条目：先换名单、再记台账（和登记反过来）——中途被打断时只会少一条豁免，不会留着已经说撤掉的豁免。"""
    path = _path(project)
    if not path.exists() and not path.is_symlink():
        return 0   # 没登记过名单：不建锁、不碰 .collab（放进项目时撤单也一样，复查第 3 轮）
    with _locked(project):
        items = _read_all(project)
        gone = [i for i in items if not keep(i)]
        if gone:
            _save(project, [i for i in items if keep(i)])
            try:
                ledger_add(project, f"已知没过名单撤掉：{'、'.join(_describe(item) for item in gone)}（{note}）")
            except OSError as exc:
                raise LedgerMissed(f"名单里的条目已经撤掉了，但台账没记上（{exc}）") from exc
    return len(gone)


def remove(project, file: str, tests=None) -> int:
    """撤掉这个文件的条目；给了 tests 只撤项目完全一样的那条。返回撤了几条。"""
    if any(not str(t).strip() for t in (tests or [])):
        raise ValueError("项名不能是空的（不写 --test 才是撤整个文件的条目）")
    want = sorted(str(t).strip() for t in (tests or []))
    return _drop(project, lambda i: not (i["file"] == file and (not want or sorted(i.get("tests") or []) == want)),
                 "手动撤掉")


def drop_card(project, card_id: str) -> int:
    """卡放进项目后，撤掉“等这张卡”的条目。"""
    return _drop(project, lambda i: not (i["kind"] == "等卡" and i.get("card") == card_id), f"{card_id} 已放进项目")


def match(item_key, entries: list):
    """(文件, 项) 在名单里的哪一条；不在返回 None。"""
    file, name = item_key
    for entry in entries:
        if entry["file"] == file and (not entry.get("tests") or name in entry["tests"]):
            return entry
    return None


def classify(items, entries: list):
    """没过的项分三堆：(名单里等卡的, 名单里偶发的, 名单外的)。一项同时被两条盖住时按“等卡”算。"""
    waiting, flaky, outside = set(), set(), set()
    for key in items:
        hits = [e for e in entries if match(key, [e])]
        if any(e["kind"] == "等卡" for e in hits):
            waiting.add(key)
        elif hits:
            flaky.add(key)
        else:
            outside.add(key)
    return waiting, flaky, outside


def cards_of(items, entries: list) -> list:
    """这些项等的是哪几张卡（摘要里写）。"""
    out = []
    for key in sorted(items):
        for e in entries:
            if e["kind"] == "等卡" and match(key, [e]) and e.get("card") not in out:
                out.append(e.get("card"))
    return out
