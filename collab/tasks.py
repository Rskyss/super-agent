"""建任务卡：行政人员确认提议后调用。编号加锁，不能重号。"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from collab.card_attachments import (
    AttachmentError,
    check_attachment_dir,
    cleanup_saved_attachments,
    save_attachments,
    validate_attachments,
)
from collab.flow import record as flow_record
from collab.state import Card
from collab.workspace import PLAN_TEMPLATE, init_collab, render_board

_CARD_SHAPE = re.compile(r"^CARD-(\d+)$")


def _atomic_write_text(target: Path, content: str) -> None:
    """先写同目录临时文件再原子替换，防止巡视线程看到空文件或半写状态。"""
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


class TaskError(Exception):
    """用户能看懂的建卡失败原因。"""


def new_card(project, title, do, not_do, done_when, conversation="", request_id="", deferred_plan: bool = False) -> str:
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
        try:
            (cdir / "需求卡.md").write_text(text, encoding="utf-8")
            Card(card_id, state="待对齐").save(cdir / "状态.json")
            (cdir / "方案.md").write_text(PLAN_TEMPLATE, encoding="utf-8")
            source_data = {"conversation": conversation or ""}
            if request_id:
                source_data["request_id"] = request_id
            if deferred_plan:
                source_data["deferred_plan"] = True
            source_json = json.dumps(source_data, ensure_ascii=False)
            source_path = cdir / "来源.json"
            if deferred_plan:
                _atomic_write_text(source_path, source_json)
            else:
                source_path.write_text(source_json, encoding="utf-8")
            (cdir / "已汇报.json").write_text(
                json.dumps({"code": "new"}, ensure_ascii=False), encoding="utf-8")
            flow_record(cdir, "需求", src="你", dst="调度员", summary=title, text=text)
        except BaseException:
            # 写到一半失败：半张卡整个删掉，当作没建过；再建只会得到一张完整的卡（DSH 改版第 3 项复查）。
            shutil.rmtree(cdir, ignore_errors=True)
            raise
        render_board(collab)
        return card_id


def _clean_scope_files(files) -> list:
    """“继续改”框里写的文件（第三批 ㉟）：只认项目里的相对路径，不许绝对路径、..、.collab。"""
    out = []
    for raw in files or []:
        rel = str(raw or "").strip().replace("\\", "/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        bad_char = (any(c in "*?[]" or ord(c) < 32 or c in "\x7f\x85\u2028\u2029" or (c.isspace() and c != " ")
                        for c in rel) or rel.startswith(("-", "#")) or len(rel) > 200)  # 各种换行都挡（第 2 轮第 4 条）
        if not rel or bad_char or rel.startswith("/") or ".." in parts or not parts or parts[0] == ".collab":
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


_CRITERIA_HEADINGS = ("验收标准", "怎么算做完")
_CRITERIA_LIST_PREFIX = re.compile(r"^(?:[-*•]\s+|\d+[.、)\]]\s+)")
_CIRCLE_NUMS = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]")
_MAX_CRITERIA_CHANGES = 50
_MAX_CRITERION_LEN = 500


def _clean_criterion_text(raw, label: str) -> str:
    """校验单条验收标准文本：必须是单行非空字符串，禁止换行、控制字符与标题注入。"""
    if not isinstance(raw, str):
        raise TaskError(f"{label}必须是文字")
    if any(ord(c) < 32 or c in "\x7f\x85\u2028\u2029" for c in raw):
        raise TaskError(f"{label}只能写单行文字，不能包含换行或控制字符")
    cleaned = raw.strip()
    if not cleaned:
        raise TaskError(f"{label}不能为空")
    if cleaned.startswith("#") or "##" in cleaned:
        raise TaskError(f"{label}不能包含标题标记")
    if len(cleaned) > _MAX_CRITERION_LEN:
        raise TaskError(f"{label}不能超过 {_MAX_CRITERION_LEN} 个字")
    return cleaned


def _split_criteria_body(body: str) -> list:
    """从验收标准小节正文拆出逐条内容，与插件端拆分规则保持一致。"""
    raw_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if raw_lines and all(_CRITERIA_LIST_PREFIX.match(ln) for ln in raw_lines):
        chunks = raw_lines
    else:
        normalized = _CIRCLE_NUMS.sub("\n", body)
        chunks = re.split(r"[\r\n;；]+", normalized)
    items = []
    for chunk in chunks:
        cleaned = _CRITERIA_LIST_PREFIX.sub("", chunk.strip()).strip()
        if not cleaned or cleaned == "（待填）":
            continue
        if cleaned not in items:
            items.append(cleaned)
    return items


def _locate_criteria_section(body: str) -> tuple:
    """定位任务卡片中的验收标准小节（优先“## 验收标准”，其次“## 怎么算做完”）。"""
    for heading in _CRITERIA_HEADINGS:
        pattern = re.compile(rf"^##[ \t]+{re.escape(heading)}[ \t]*\r?\n", re.MULTILINE)
        match = pattern.search(body)
        if not match:
            continue
        start = match.end()
        nxt = re.search(r"(?:\r?\n)##[ \t]+", body[start:])
        end = start + nxt.start() if nxt else len(body)
        return start, end, body[start:end]
    raise TaskError("任务卡片里找不到验收标准，无法按条修改")


def _apply_criteria_changes(body: str, criteria_base, criteria_changes) -> str:
    """校验并应用结构化验收标准修改；空修改保留原正文。"""
    if criteria_changes is None:
        return body
    if not isinstance(criteria_changes, list):
        raise TaskError("验收标准修改明细格式不对")
    if len(criteria_changes) == 0:
        return body
    if len(criteria_changes) > _MAX_CRITERIA_CHANGES:
        raise TaskError(f"单次最多修改 {_MAX_CRITERIA_CHANGES} 条验收标准")
    if not isinstance(criteria_base, list) or any(not isinstance(item, str) for item in criteria_base):
        raise TaskError("缺少有效的旧验收标准基线")

    start, end, section_body = _locate_criteria_section(body)
    current_items = _split_criteria_body(section_body)
    expected_base = [item.strip() for item in criteria_base]
    if expected_base != current_items:
        raise TaskError("验收标准已发生变化，请刷新后再试")

    working = list(current_items)
    for idx, item in enumerate(criteria_changes, 1):
        if not isinstance(item, dict):
            raise TaskError(f"第 {idx} 项修改格式不对")
        keys = set(item.keys())
        if keys == {"replaceNth", "text"}:
            nth = item["replaceNth"]
            if not isinstance(nth, int) or isinstance(nth, bool):
                raise TaskError("修改的验收标准序号必须是整数")
            if nth < 1 or nth > len(current_items):
                raise TaskError(f"第 {nth} 条验收标准不存在")
            working[nth - 1] = _clean_criterion_text(item["text"], f"第 {nth} 条验收标准")
        elif keys == {"add"}:
            working.append(_clean_criterion_text(item["add"], "新增验收标准"))
        else:
            raise TaskError("不支持的验收标准修改操作")

    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(working, 1))
    suffix = "\n" if (section_body.endswith("\n") or end == len(body)) else ""
    return body[:start] + numbered + suffix + body[end:]


def validate_criteria_revision(project, card_id: str, criteria_base=None, criteria_changes=None) -> None:
    """预先校验结构化验收标准修改与基线是否有效，不写入任何文件。"""
    if criteria_changes is None:
        return
    if isinstance(criteria_changes, list) and len(criteria_changes) == 0:
        return
    cdir = Path(project) / ".collab" / "cards" / card_id
    req = cdir / "需求卡.md"
    body = req.read_text(encoding="utf-8") if req.is_file() else ""
    _apply_criteria_changes(body, criteria_base, criteria_changes)


def _load_card_for_revise(project, card_id: str) -> tuple:
    """预检任务卡片编号、目录与状态文件是否合法可读，不写入任何文件。"""
    if not isinstance(card_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", card_id):
        raise TaskError("卡片编号不对")
    cdir = Path(project) / ".collab" / "cards" / card_id
    state_path = cdir / "状态.json"
    if cdir.is_symlink() or state_path.is_symlink() or not state_path.is_file():
        raise TaskError("找不到这张卡片")
    try:
        card = Card.load(state_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TaskError("任务卡片状态文件损坏，无法修改") from exc
    return cdir, state_path, card


def validate_card_attachments(project, card_id: str, attachments=None) -> list:
    """预先校验图片附件（类型/大小/签名/目录无软链）；无图时立即返回且不读卡片，不写入任何文件。"""
    try:
        validated = validate_attachments(attachments)
    except AttachmentError as exc:
        raise TaskError(str(exc)) from exc
    if not validated:
        return []
    cdir, _, _ = _load_card_for_revise(project, card_id)
    try:
        check_attachment_dir(cdir)
    except AttachmentError as exc:
        raise TaskError(str(exc)) from exc
    return validated


QUEUED_REVISIONS = "待生效修改.jsonl"   # 第七批 7-1：跑着时确认的修改，等调度员在停顿点取走
_QUEUE_LOCK = ".待生效修改.lock"


class _QueueLock:
    """排队和取走用同一把文件锁：取走时刚好有人追加，不会被一起删掉（复查第 1 轮严重 3）。"""

    def __init__(self, cdir: Path):
        self.path = Path(cdir) / _QUEUE_LOCK
        self.handle = None

    def __enter__(self):
        self.handle = open(self.path, "a")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()


class card_apply_lock(_QueueLock):
    """同一张卡“取走排队的修改并生效、再开工”整段只许一个在做：任务页轮询、巡视、调度员停顿点都可能同时来，
    各拿一份旧内容改完互相覆盖（复查第 2 轮严重 1）。和排队锁是两把，先拿这把再拿排队锁，不会死锁。"""

    def __init__(self, cdir: Path):
        self.path = Path(cdir) / ".改要求生效.lock"
        self.handle = None

    def __enter__(self):
        if not self.path.parent.is_dir():   # 卡片文件夹不存在：交给后面的检查去报“找不到这张卡片”，不在这里先炸
            return self
        return super().__enter__()

    def __exit__(self, *exc):
        if self.handle is None:
            return None
        return super().__exit__(*exc)


def queue_revision(project, card_id: str, text: str, criteria_base=None, criteria_changes=None) -> None:
    """卡正在跑时用户点了“确认改”：先排队，这一轮跑到停顿点再生效（排队时先不动任务卡片）。"""
    cdir, _state_path, _card = _load_card_for_revise(Path(project), card_id)
    words = (text or "").strip()
    if not words:
        raise TaskError("请写上要改什么")
    item = {"text": words, "time": datetime.now().isoformat(timespec="seconds")}
    if isinstance(criteria_changes, list) and criteria_changes:
        item["criteria_changes"] = criteria_changes   # 生效时按当时最新的验收标准逐条套用，不存旧基线
    with _QueueLock(cdir):
        with (cdir / QUEUED_REVISIONS).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    flow_record(cdir, "改要求排队", src="你", dst="调度员", summary=f"你确认了修改，这一轮跑完生效：{words[:60]}",
                text=words)


def _read_queue(path: Path) -> list:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def queued_revisions(project, card_id: str) -> list:
    """排着队、还没生效的修改（读不出的行跳过）。"""
    return _read_queue(Path(project) / ".collab" / "cards" / card_id / QUEUED_REVISIONS)


def take_queued_revisions(project, card_id: str) -> list:
    """取走排队的修改（取走即清空，免得重复生效）；和排队共用一把锁。"""
    cdir = Path(project) / ".collab" / "cards" / card_id
    if not (cdir / QUEUED_REVISIONS).exists():
        return []
    with _QueueLock(cdir):
        items = _read_queue(cdir / QUEUED_REVISIONS)
        path = cdir / QUEUED_REVISIONS
        if path.exists():
            path.unlink()
    return items


def current_criteria(project, card_id: str) -> list:
    """任务卡片里现在的验收标准（逐条）。"""
    req = Path(project) / ".collab" / "cards" / card_id / "需求卡.md"
    body = req.read_text(encoding="utf-8") if req.is_file() else ""
    try:
        return _split_criteria_body(_locate_criteria_section(body)[2])
    except TaskError:
        return []


def apply_queued_items(project, card_id: str, items: list) -> tuple:
    """按顺序把排队的修改记成追加要求；每条都按当时最新的验收标准套用（连着两条改不同条目都能生效，复查严重 2）。
    返回 (生效的话, 没生效的 (话, 原因))。"""
    applied, failed = [], []
    for item in items:
        words = str(item.get("text") or "").strip()
        changes = item.get("criteria_changes") if isinstance(item.get("criteria_changes"), list) else None
        try:
            revise_card(project, card_id, words, criteria_base=current_criteria(project, card_id) if changes else None,
                        criteria_changes=changes)
        except (TaskError, OSError, ValueError) as exc:
            failed.append((words, str(exc)))
            continue
        applied.append(words)
    return applied, failed


def redesign_note(project, card_id: str, files=None, criteria_changes=None) -> str:
    """第三批 ⑳：这次“继续改”比原施工单大很多吗（新加的要改文件超过原范围一半，或者改了验收标准）。
    是就返回一句提醒（只提醒，不拦），不是返回空。"""
    from collab.scope import read_allowed
    try:
        body = (Path(project) / ".collab" / "cards" / str(card_id) / "需求卡.md").read_text(encoding="utf-8")
    except OSError:
        body = ""
    have = set(read_allowed(body) or [])
    new = [f for f in _clean_scope_files(files or []) if f not in have]
    bits = []
    if have and len(new) * 2 > len(have):
        bits.append(f"新加 {len(new)} 个文件（原来允许改 {len(have)} 个）")
    elif not have and new:
        bits.append(f"新加 {len(new)} 个文件")
    if criteria_changes:
        bits.append("改了验收标准")
    if not bits:
        return ""
    return ("这次修改比原施工单大很多（" + "、".join(bits) + "），像是重新设计。"
            "建议先改施工单，或者另开一张卡。")


def revise_card(project, card_id: str, text: str, files=None,
                criteria_base=None, criteria_changes=None, attachments=None) -> None:
    """把新要求追加到任务卡片；如有结构化逐条修改或截图，全部预检通过后再落盘。
    验收中或卡住时回到开发，写方案时保持待对齐。
    files：还要改的文件，加进允许改动的范围（写了就算用户点头，记“追加范围”，第三批 ㉟）。"""
    project = Path(project)
    cdir, state_path, card = _load_card_for_revise(project, card_id)
    words = (text or "").strip()
    if not words:
        raise TaskError("请写上要改什么")
    scope = _clean_scope_files(files)  # 不合规矩的在动任何东西之前就退回
    validated_images = validate_card_attachments(project, card_id, attachments)
    req = cdir / "需求卡.md"
    body = req.read_text(encoding="utf-8") if req.is_file() else ""
    body = _apply_criteria_changes(body, criteria_base, criteria_changes)
    if scope:
        body = _add_scope(body, scope)
    if body and not body.endswith("\n"):
        body += "\n"

    saved_rels = []
    created_paths = []
    created_dir = False
    if validated_images:
        try:
            saved_rels, created_paths, created_dir = save_attachments(cdir, validated_images)
        except AttachmentError as exc:
            raise TaskError(str(exc)) from exc

    append_block = f"\n## 追加要求\n{words}\n"
    if saved_rels:
        append_block += "\n" + "\n".join(f"![截图]({rel})" for rel in saved_rels) + "\n"

    try:
        _atomic_write_text(req, body + append_block)
    except BaseException:
        if created_paths or created_dir:
            cleanup_saved_attachments(cdir / "附件", created_paths, created_dir)
        raise

    before = cdir / "测试前改动.json"
    if before.exists():
        before.unlink()
    if card.state in ("待验收", "需Claude介入"):
        card.rework = 0
        card.transition("开发中")
        card.save(state_path)
    render_board(project / ".collab")
    if scope:
        flow_record(cdir, "追加范围", src="你", dst="调度员", files=scope,
                    summary=f"加进允许改动的范围：{'、'.join(scope)}")
    extra_flow = {}
    if isinstance(criteria_changes, list) and len(criteria_changes) > 0:
        extra_flow["criteria_changes"] = criteria_changes
        if isinstance(criteria_base, list):
            extra_flow["criteria_base"] = criteria_base
    if saved_rels:
        extra_flow["attachments"] = list(saved_rels)
    flow_record(cdir, "追加要求", src="你", dst="调度员", summary=words[:80], text=words, **extra_flow)
