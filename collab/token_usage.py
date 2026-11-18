"""读 Codex 会话记录里的 token 用量（只读，任何情况都不向外抛异常）。

用量来自 Codex 自己写在会话记录里的累计数：
  $CODEX_HOME/sessions/年/月/日/rollout-<时间>-<会话编号>.jsonl
每完成一轮追加一条 event_msg -> token_count -> info.total_token_usage。
这里只读文件、只算数，不碰任何设置，也不影响卡片流程。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

_TAIL_START = 256 * 1024          # 先从最后 256 KB 找，找不到就翻倍往前
_LOG_HEAD = 16 * 1024              # 实时输出只看开头 16 KB
_SESSION_RE = re.compile(r"^session id:\s*(\S+)\s*$")
_USAGE_KEYS = ("total_tokens", "input_tokens", "cached_input_tokens",
               "output_tokens", "reasoning_output_tokens")
_SUM_KEYS = ("total", "input", "cached", "output", "reasoning", "children")
_path_cache: dict = {}


def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _num(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):  # 无穷大、非数字一律当 0
        return 0


def codex_home() -> str:
    """CODEX_HOME 优先，没设才用 ~/.codex。"""
    try:
        return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    except Exception:  # noqa: BLE001 — 连路径都取不到时给个空串，上层只当读不到
        return ""


def session_id_from_text(text) -> str:
    """只认行首的 `session id: <编号>`；正文里引用的不算。"""
    try:
        for line in str(text or "").splitlines():
            match = _SESSION_RE.match(line.strip())
            if match:
                return match.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _cache_key(uuid: str) -> tuple:
    try:
        return (os.path.abspath(codex_home()), uuid)
    except Exception:  # noqa: BLE001
        return ("", uuid)


def find_session_file(uuid):
    """按文件名末尾 `-<uuid>.jsonl` 找会话；先看今天、昨天，没有再整个 sessions 找。"""
    try:
        uid = str(uuid or "").strip()
        if not uid:
            return None
        key = _cache_key(uid)
        cached = _path_cache.get(key)
        if cached and Path(cached).is_file():
            return cached
        root = Path(codex_home()) / "sessions"
        if not root.is_dir():
            return None
        suffix = f"-{uid}.jsonl"
        found = []
        today = date.today()
        for offset in (0, 1):
            day = today - timedelta(days=offset)
            folder = root / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d")
            if folder.is_dir():
                found.extend(p for p in folder.iterdir() if p.is_file() and p.name.endswith(suffix))
        if not found:
            found = [p for p in root.rglob(f"*{suffix}") if p.is_file()]
        if not found:
            return None
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        _path_cache[key] = str(found[0])
        return str(found[0])
    except Exception:  # noqa: BLE001 — 找不到就当没有
        return None


def _usage_from_line(line: str):
    line = (line or "").strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "event_msg":
        return None
    payload = data.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    usage = info.get("total_token_usage")
    if not isinstance(usage, dict):
        return None
    values = {}
    for key in _USAGE_KEYS:
        value = usage.get(key)
        if value is None and key != "total_tokens":
            value = 0
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        values[key] = value
    return {"ok": True, "total": values["total_tokens"], "input": values["input_tokens"],
            "cached": values["cached_input_tokens"], "output": values["output_tokens"],
            "reasoning": values["reasoning_output_tokens"]}


def read_total(path):
    """从文件尾部往前找最后一条完整、有效的累计用量；每条本身就是累计数，只取最后一条。"""
    try:
        if path is None:
            return _fail("没有会话记录文件")
        target = Path(path)
        if not target.is_file():
            return _fail("找不到会话记录文件")
        size = target.stat().st_size
        if size <= 0:
            return _fail("会话记录还是空的")
        window = _TAIL_START
        while True:
            start = max(0, size - window)
            with target.open("rb") as fh:
                fh.seek(start)
                raw = fh.read(size - start)
            lines = raw.decode("utf-8", errors="replace").splitlines()
            if start > 0 and lines:
                lines = lines[1:]  # 第一行可能被截断，丢掉
            for line in reversed(lines):
                got = _usage_from_line(line)
                if got is not None:
                    return got
            if start == 0:
                return _fail("会话记录里还没有能看懂的用量")
            window *= 2
    except Exception as exc:  # noqa: BLE001 — 坏文件、权限问题都只算读不到
        return _fail(f"读会话记录时出问题（{exc}）")


def _first_line(path: Path) -> str:
    with path.open("rb") as fh:
        return fh.readline().decode("utf-8", errors="replace")


def children_total(uuid) -> int:
    """主会话同一天的目录里，找父会话是它、自己编号不同的辅助会话，把它们的累计加总。"""
    try:
        uid = str(uuid or "").strip()
        if not uid:
            return 0
        main = find_session_file(uid)
        if not main:
            return 0
        folder = Path(main).parent
        total = 0
        for path in folder.iterdir():
            if not path.is_file() or path.suffix != ".jsonl" or path.name == Path(main).name:
                continue
            try:
                meta = json.loads(_first_line(path))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict) or meta.get("type") != "session_meta":
                continue
            payload = meta.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("session_id") != uid or payload.get("id") == uid:
                continue
            got = read_total(path)
            if got.get("ok"):
                total += _num(got.get("total"))
        return total
    except Exception:  # noqa: BLE001 — 辅助会话读不到就不算
        return 0


def usage_from_text(text):
    """从一段输出里认编号、找会话、读累计；成功时把辅助会话单独放在 children。"""
    try:
        uuid = session_id_from_text(text)
        if not uuid:
            return _fail("输出里没有会话编号")
        path = find_session_file(uuid)
        if not path:
            return _fail("没找到这次运行的会话记录")
        got = read_total(path)
        if got.get("ok"):
            got = dict(got)
            got["children"] = children_total(uuid)
        return got
    except Exception as exc:  # noqa: BLE001
        return _fail(f"算用量时出问题（{exc}）")


def usage_from_log(path):
    """实时输出只看开头 16 KB（会话编号在头部），再按普通输出读。"""
    try:
        if path is None:
            return _fail("没有实时输出文件")
        target = Path(path)
        if not target.is_file():
            return _fail("实时输出文件还没生成")
        with target.open("rb") as fh:
            raw = fh.read(_LOG_HEAD)
        return usage_from_text(raw.decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"读实时输出时出问题（{exc}）")


def new_tokens(t) -> int:
    """新消耗 = 输入 - 缓存读取 + 输出（Codex 自己打印的 tokens used）。"""
    if not isinstance(t, dict):
        return 0
    return _num(t.get("input")) - _num(t.get("cached")) + _num(t.get("output"))


def sum_tokens(items):
    """把多轮用量相加；空列表返回 None；失败项只记次数，不把原因当数字。"""
    if not items:
        return None
    try:
        items = list(items)
    except TypeError:  # 传进来的不是一组用量（比如一个数字），按读不到处理
        return {"ok": False, "missing": 1, "reason": "用量数据格式不对，读不到"}
    good, missing, first_reason = [], 0, ""
    for item in items:
        if isinstance(item, dict) and item.get("ok"):
            good.append(item)
        else:
            missing += 1
            if not first_reason and isinstance(item, dict):
                first_reason = str(item.get("reason") or "").strip()
    if not good:
        return {"ok": False, "missing": missing, "reason": first_reason or "这一步没有读到用量"}
    total = {"ok": True}
    for key in _SUM_KEYS:
        total[key] = sum(_num(item.get(key)) for item in good)
    total["missing"] = missing
    return total
