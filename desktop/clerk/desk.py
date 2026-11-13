"""行政人员的对话：保存在项目 .collab/行政对话/ 里，只留本机。

页面用“从第几条之后”来取事件，和调度员聊天用同一种事件，所以页面的显示逻辑不用另写一套。
每段对话互不影响：切走时回复继续，切回来接着看。用户说第一句之前不保存，不留空对话。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

from desktop.clerk.client import ModelError
from desktop.clerk import blocks_tools, tasks as clerk_tasks
from desktop.clerk.tools import LABELS, PROPOSE_REVISE, TOOLS, run_tool, team_incomplete_hint
from collab import blocks

FOLDER = Path(".collab") / "行政对话"
_AUTH_HEADER = re.compile(r"(?i)(bearer|authorization:)\s+[A-Za-z0-9._~+/=-]{12,}")
KEY_NOTICE = "你刚才贴的内容里有一串像密钥的字（也可能只是编号），已经隐藏，没有发给模型。如果真是密钥，为了安全，请到那家平台把它作废，换一把新的，以后填在提议的密钥框里。"
INDEX = "索引.json"
HISTORY_LIMIT = 40  # 带给模型的最近几句话，够接上话，也不让费用越滚越大
TITLE_LIMIT = 24
MAX_TOOL_ROUNDS = 5
BLOCKS_TOOL_ROUNDS = 8  # 积木库：翻手册、搜、读网页、出提议一套下来步数多（用户真实接高德时 5 步不够，2026-11-10）
# 模型把工具调用当文字吐出来（DeepSeek 的 DSML、常见的 <tool_call> 等写法）：从这里起都不给用户看
# 只认这几种协议写法：<｜…（DeepSeek）、<|tool_calls_begin|>、<tool_call>；正常的表格、代码例子不动（复查）
_TOOL_MARKUP = re.compile(r"<(?:｜|\|[A-Za-z_▁]{1,40}\|>|/?tool_calls?>)", re.I)
_MAYBE_MARKUP = re.compile(r"<[^\s>]{0,44}$")  # 比协议名最长 40 再多留两边的竖线  # 边打字边显示时，结尾这一截可能是协议写法的开头，先不显示
_LAST_ROUND = ("（这是系统提示，不是用户说的）这一轮能用的步骤用完了，不能再调用任何工具，也不要写出工具调用。"
               "用大白话告诉用户做到哪一步、还差什么，请用户说“继续”接着做。")

# 行政说“提议/确认框已经弹出来了”这一类话（第 9 项整体验收：模型有时没调用出提议的工具却这么说）
_CARD = r"(?:安装提议|接入提议|提议|提议卡|接法卡|安装卡|卡片|确认框|确认卡)"  # 旧叫法：提议卡|接法卡|安装卡 仍认
_DONE = (r"(?:弹出来了|弹出了|已经?弹出|出来了|出好了|已经?发出|已经?发送|已经?给出|已经?生成|已经?显示|已经?出示"
         r"|已经出了|已出(?![现错结]))")
_CLAIM_AFTER = re.compile(_CARD + r"([^。！？\n]{0,15}?)" + _DONE)  # “提议已经弹出来了”
_CLAIM_BEFORE = re.compile(r"(?:已经?[^。！？\n]{0,4}?(?:弹出|出示|生成|发(?![现过])|出(?![现错过结]))"
                           r"|(?:给你|为你)(?:弹出|出示|出|生成|发)了)"
                           r"了?[一个张份]{0,2}[^。！？\n]{0,4}?" + _CARD  # “我已经给你出了一个提议”
                           + r"|已经?把" + _CARD + r"(?:发给你|给你|发你)了")  # “我已经把提议发给你了”
_NOT_CARD_ITSELF = re.compile(r"结果|命令|检测|里的|内容")  # “提议的检测结果出来了”说的不是提议本身
_IF_BEFORE = re.compile(r"(?:如果|要是|一旦|等到)[^，,。！？\n]{0,6}$")  # “如果确认框出来了”（不跨逗号）
_ASKING = re.compile(r"吗|？|\?|要不要|可以|之前|能不能")  # “要我给你出一个提议吗？”是在问，不是说出了
_WAIT_THEN = re.compile(r"等[^，,；;]*再")  # “等确认框弹出了再点”
_PAREN = re.compile(r"（[^）]*）|\([^)]*\)")  # “提议（含检测命令）已经弹出来了”：括号里的不算
_LATER_AFTER = re.compile(r"^(?:以后|之后|后)")  # “确认框出来了以后”
# “提议”当动词，两头一起看（复查第 1–5 轮换个说法就漏，不再一个个补词）：
# 前面是说话的人或副词（我提议、我们提议、我个人提议、我想/也/还是/就提议）；或后面跟着要做的事（提议你先…、提议先…、提议把/将…）。
# “提议给你……”两种意思都有（“我提议给你换个模型”“接入提议给你看看”），只按前面那头认。
# 前面是 的/个/张/出/进 这类时是名词（“上面的提议”“出了一个提议”“填进提议里”），不按动词排。
_PROPOSE_AS_VERB = re.compile(r"(?<=[我们咱他她人想也还是就再倒才都只刚又])提议"
                              r"|(?<![的张个份置卡])提议(?:你|先|把|将)")
_CLAIM_REMINDER = ("（这是系统提示，不是用户说的）你刚才说已经出了提议或确认框，但这一轮你没有调用任何 propose_ 开头的工具，"
                   "用户什么提议都看不到。要出提议就现在调用对应的工具；不需要，就如实告诉用户刚才没有出提议。")
_CARD_SHOWN = "已向用户出示确认框"  # 出提议工具成功时回给模型的话（clerk_tasks._publish_block 等）


def claims_card(text: str) -> bool:
    """回话里是不是说了“提议已经出了”（说检测结果、命令内容，或“如果/等…出来了以后”都不算）"""
    # 按半句看（逗号也切开）：“提议已经弹出来了，你可以点确认”后半句的“可以”不该让前半句算成在问（第三轮复查）
    for sentence in re.findall(r"[^，,；;。！？!?\n]+[，,；;。！？!?]?", _PAREN.sub("", text or "")):
        if _ASKING.search(sentence) or _WAIT_THEN.search(sentence) or _PROPOSE_AS_VERB.search(sentence):
            continue
        for pattern in (_CLAIM_AFTER, _CLAIM_BEFORE):
            for found in pattern.finditer(sentence):
                if pattern is _CLAIM_AFTER and _NOT_CARD_ITSELF.search(found.group(1)):
                    continue
                if _IF_BEFORE.search(sentence[:found.start()]):
                    continue
                if _LATER_AFTER.match(sentence[found.end():found.end() + 2]):
                    continue
                return True
    return False


# 说得像提议就在屏幕上（“提议就在上面”“填进提议里”）：屏幕上其实没有时要提醒
# 用户真实接腾讯云混元时管家没出提议却这么说了三次，上面的 claims_card 一句都没认出来（2026-11-10）
_CARD_WORD = r"(?:安装提议|接入提议|提议|卡片|接法卡|提议卡|安装卡|确认卡|确认框|这张卡|卡)"  # 旧叫法：接法卡|提议卡|安装卡 仍认
_POINTS = re.compile(
    _CARD_WORD + r"[^。！？\n]{0,4}?(?:给你|送到|发到|放到|摆在|就在|还在|在你?的?屏幕上|在上面)"
    r"|(?:屏幕上|上面)的?[^。！？\n]{0,3}?" + _CARD_WORD
    + r"|(?:填进|填到|填在|写进|写在)" + _CARD_WORD + r"(?:上|里)?"
    + r"|" + _CARD_WORD + r"(?:上|里)[^。！？\n]{0,6}?(?:填|点确认|点一下|选)")
_FUTURE = re.compile(r"会|将|再|到时|之后|以后|等|装好|接好|待会|下一步")


def points_at_card(text: str) -> bool:
    """回话说得像屏幕上已经有提议（在问、在说以后的事不算）"""
    for sentence in re.findall(r"[^，,；;。！？!?\n]+[，,；;。！？!?]?", _PAREN.sub("", text or "")):
        if (_ASKING.search(sentence) or _WAIT_THEN.search(sentence) or _FUTURE.search(sentence)
                or _PROPOSE_AS_VERB.search(sentence)):
            continue
        found = _POINTS.search(sentence)
        if found and not _NOT_CARD_ITSELF.search(sentence[found.start():found.end() + 4]):
            return True
    return False


# 项目窗口的行政说“你点确认我就建成任务”却没出确认框（试跑记录 ⑥，2026-11-11 用户截图）
_TASK_CLAIM = re.compile(r"点(?:一下|个)?[“\"「]?确认[”\"」]?(?:按钮)?[^。！？\n]{0,12}?(?:建|生成|变成|开成|做成)[^。！？\n]{0,6}?(?:任务|卡片|卡)")


_TASK_ASKING = re.compile(r"吗|？|\?|要不要|能不能")
_TASK_NOT = re.compile(r"(?:不要|别|先别|不用|不必)[^。！？\n]{0,4}点")  # “请先不要点确认”不是说框已经在了


def claims_task_box(text: str) -> bool:
    """回话里像是确认框已经在屏幕上了（“点确认就建成任务卡片”）；在问、在说以后的事不算。"""
    for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?", _PAREN.sub("", text or "")):
        # 只排除真在问、在说以后的事；“你可以点确认”“如果没问题，点确认”照样是在催用户点一个不存在的按钮（复查第 1 轮）
        if _TASK_ASKING.search(sentence) or _WAIT_THEN.search(sentence) or re.search(r"的话|之后|以后", sentence):
            continue
        found = _TASK_CLAIM.search(sentence)
        if found and not _TASK_NOT.search(sentence[:found.start() + 1]):
            return True
    return False


def _claim_reminder(open_cards: list) -> str:
    """上面还有没处理的提议时，点名那个：说的是它就照实说在上面，说的是新提议就现在出"""
    if not open_cards:
        return _CLAIM_REMINDER
    names = "、".join(f"「{clerk_tasks.card_label(c)}」"
                     for c in open_cards[:3])
    return (f"（这是系统提示，不是用户说的）你刚才说出了提议或确认框，但这一轮你没有调用任何 propose_ 开头的工具。"
            f"上面还有没处理的提议：{names}。你说的如果是上面这个，就照实告诉用户它在上面；说的是新的提议，就现在调用对应的工具。")
ROLE = ("你是超级助手的行政人员，用户的前台和行政。用大白话简短回答，不说技术术语。"
        "你能查项目说明、台账、卡片和团队设置；也能查本机 PATH 里有没有装 Codex、Cursor（agent 命令）、agy，只查不运行。"
        "问进度必须先查，不能凭记忆。"
        "问这个项目是什么、怎么用时，先读项目说明；没有或不够，再调用 read_project_docs 读项目自带资料，只按资料回答。"
        "项目说明还没写时，读完资料后可以调用 propose_profile 起草。不知道的项不要编，先问用户。"
        "你不能改代码、运行命令、推送或把改动放进项目。"
        "用户明确要改产品或写代码时，先问清楚，再调用 propose_task。"
        "用户明确要改团队分工（调度员、写测试、写代码、复查用谁）时，先问清楚，再调用 propose_team_change。"
        "不要私自做工具连通检测；需要的话提醒用户去「设置」里点「检测」。"
        "闲聊、问进度不调用 propose_task 或 propose_team_change。不能说“已经建好卡”或“已经改好团队”，用户点确认才算。"
        "不清楚时一次只问一个问题，给出推荐答案和一句话理由。"
        "不知道就直说，不编造。"
        "这个项目叫：{name}。")
NAMING = "用不超过 12 个字概括这段对话的主题，只回答标题本身。\n用户：{user}\n回答：{reply}"
_ID_SHAPE = re.compile(r"[0-9a-f]{32}")


def _section(text: str, heading: str) -> str:
    mark = f"## {heading}"
    if mark not in text:
        return ""
    body = text.split(mark, 1)[1]
    lines = []
    for line in body.splitlines():
        if line.startswith("## "):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _who(project) -> str:
    """积木库窗口里叫积木库管家，项目窗口里叫行政（积木库管家方案第一节第 1 条）。"""
    return "管家" if blocks_tools.is_blocks_home(project) else "行政"


class _ThisTurn:
    """交给出提议的对话：多带一个“是哪一轮出的”，出提议前程序再看一眼这一轮被停掉没有（复查第二轮第 2 条）。"""

    def __init__(self, conv, turn: int):
        self._conv, self.turn_expected = conv, turn

    def __getattr__(self, name):
        return getattr(self._conv, name)


class _Stopped(Exception):
    """用户点了停止：这一轮后面到的内容都不要了。"""


class _Conversation:
    def __init__(self, project: str, cid: str):
        self.project, self.id = project, cid
        self.events, self.messages, self.partial = [], [], []
        self.cond = threading.Condition()
        self.busy = False
        self.turn = 0
        self.deleted = False   # 删掉后，还在路上的回答和标题都不再写
        self.manuals = []      # 积木库管家这段对话翻开过的手册（按翻开顺序）

    def emit(self, event: dict) -> None:
        with self.cond:
            self.events.append(dict(event, seq=len(self.events) + 1))
            self.cond.notify_all()


class ClerkDesk:
    def __init__(self, client, blocks_client=None):
        self._client = client  # 取当前模型连接的函数：密钥可能晚一点才配好
        self._blocks_client = blocks_client
        self._open = {}
        self._focus = {}
        self._lock = threading.Lock()
        self._write_lock = clerk_tasks.CONV_FILE_LOCK  # 对话文件的追加和改写最后一条不能交错（复查 A 第 1 条），和提议共用

    # ---------- 存取 ----------

    def _folder(self, project: str) -> Path:
        return Path(project) / FOLDER

    def _load_index(self, project: str) -> dict:
        """索引只是目录：读不了或缺了哪段，就从对话文件补回来，不能让对话消失。"""
        try:
            saved = json.loads((self._folder(project) / INDEX).read_text(encoding="utf-8"))
            index = saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            index = {}
        for path in self._folder(project).glob("*.jsonl"):
            entry = index.get(path.stem)
            if _ID_SHAPE.fullmatch(path.stem) and not (isinstance(entry, dict) and entry.get("title")):
                title = self._first_words(path)
                if title:
                    index[path.stem] = {"title": title, "updated": path.stat().st_mtime}
        return index

    def _first_words(self, path: Path) -> str:
        for role, text in self._read_lines(path):
            if role == "user":
                return self._short(text)
        return ""

    @staticmethod
    def _last_item(path: Path) -> str:
        try:
            raws = [r for r in path.read_bytes().splitlines() if r.strip()]
            last = json.loads(raws[-1]) if raws else {}
        except (OSError, ValueError):
            return ""
        return str(last.get("item") or "") if isinstance(last, dict) and last.get("role") == "assistant" else ""

    @staticmethod
    def _read_lines(path: Path) -> list:
        """读对话文件。提议行 role=proposal 只记位置，不进模型上下文。"""
        lines = []
        try:
            raws = path.read_bytes().splitlines()
        except OSError:
            return lines
        for raw in raws:
            try:
                saved = json.loads(raw.decode("utf-8"))
                role = saved["role"]
            except (ValueError, KeyError, TypeError):
                continue
            if role == "proposal" and isinstance(saved.get("id"), str):
                lines.append(("proposal", saved["id"]))
                continue
            if role == "manual" and saved.get("name") in blocks_tools.MANUALS:
                lines.append(("manual", saved["name"]))
                continue
            if role == "action" and isinstance(saved.get("label"), str) and isinstance(saved.get("text"), str):
                lines.append(("action", {"label": saved["label"], "text": saved["text"]}))
                continue
            text = saved.get("text")
            if role in ("user", "assistant", "notice") and isinstance(text, str):
                lines.append((role, text))
        return lines

    def _save_index(self, project: str, cid: str, **fields) -> None:
        with self._lock:
            index = self._load_index(project)
            entry = index.get(cid) if isinstance(index.get(cid), dict) else {}
            index[cid] = dict(entry, **fields)
            folder = self._folder(project)
            folder.mkdir(parents=True, exist_ok=True)
            spare = folder / (INDEX + ".tmp")
            spare.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
            spare.replace(folder / INDEX)

    def _record(self, conv: _Conversation, role: str, text: str, item: str = "") -> None:
        if conv.deleted:
            return
        folder = self._folder(conv.project)
        folder.mkdir(parents=True, exist_ok=True)
        entry = {"role": role, "text": text, "time": time.time()}
        if item:
            entry["item"] = item
        line = json.dumps(entry, ensure_ascii=False)
        with self._write_lock, (folder / f"{conv.id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if role != "notice":  # 提醒只给用户看，不进模型的对话
            conv.messages.append({"role": role, "content": text})
        self._save_index(conv.project, conv.id, updated=time.time())

    def _record_line(self, conv: _Conversation, line: dict) -> None:
        """手册、动作这类只留给自己和用户看的行：不进模型的对话，不改“最近更新”。"""
        if conv.deleted:
            return
        folder = self._folder(conv.project)
        folder.mkdir(parents=True, exist_ok=True)
        with self._write_lock, (folder / f"{conv.id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(line, time=time.time()), ensure_ascii=False) + "\n")

    def _open_manual(self, conv: _Conversation, name: str) -> None:
        if name in conv.manuals:
            return
        conv.manuals.append(name)
        try:
            self._record_line(conv, {"role": "manual", "name": name})
        except OSError:
            pass  # 记不下来只是重开后要再翻一次，不能打断回话

    def _conversation(self, project: str, cid: str, create: bool = False):
        if not _ID_SHAPE.fullmatch(cid or ""):
            return None
        key = (str(project), cid)
        with self._lock:
            conv = self._open.get(key)
        if conv is not None:
            return conv
        path = self._folder(project) / f"{cid}.jsonl"
        if not create and not path.exists():
            return None
        conv = _Conversation(str(project), cid)
        last_item = self._last_item(path)
        loaded = self._read_lines(path)
        for at, (role, payload) in enumerate(loaded):
            if role == "proposal":
                data = clerk_tasks.load_proposal(project, payload)
                if data:
                    conv.emit(dict(clerk_tasks.event_from_proposal(data), history=True,
                                   item=f"h{len(conv.events)}"))
                continue
            if role == "notice":
                conv.emit({"type": "notice", "text": payload, "item": f"h{len(conv.events)}", "history": True})
                continue
            if role == "manual":
                if payload not in conv.manuals:
                    conv.manuals.append(payload)
                continue
            if role == "action":
                conv.emit({"type": "activity", "item": f"h{len(conv.events)}", "label": payload["label"],
                           "text": payload["text"], "status": "done", "history": True})
                continue
            conv.messages.append({"role": role, "content": payload})
            kind = "user" if role == "user" else "message"
            # 最后一条是行政的提醒时留着它原来的编号，重开后再来的提醒还能改写它（复查 A 第 4 条）
            item = last_item if (last_item and at == len(loaded) - 1 and role == "assistant") else f"h{len(conv.events)}"
            conv.emit({"type": kind, "text": payload, "item": item, "history": True})
        with self._lock:
            if not create and not path.exists():  # 读的时候被删了：不能放回内存，否则之后的通知会把文件写回来
                return None
            return self._open.setdefault(key, conv)

    def _card_conversations(self, project: str) -> set:
        """卡片自己的对话。编号由卡片算出来，索引坏了也认得出。"""
        cards = Path(project) / ".collab" / "cards"
        if not cards.is_dir():
            return set()
        return {clerk_tasks.card_conversation_id(item.name) for item in cards.iterdir()}

    # ---------- 给页面用 ----------

    def list(self, project: str) -> dict:
        current = self._focus.get(str(project))
        pending = clerk_tasks.conversations_with_open(project)
        of_cards = self._card_conversations(project)
        threads = []
        with self._lock:
            opened = dict(self._open)
        for cid, entry in self._load_index(project).items():
            if isinstance(entry, dict) and _ID_SHAPE.fullmatch(cid) and entry.get("title") \
                    and (self._folder(project) / f"{cid}.jsonl").exists():
                if entry.get("card") or cid in of_cards:
                    continue
                conv = opened.get((str(project), cid))
                threads.append({
                    "id": cid, "title": entry["title"], "updated": entry.get("updated") or 0,
                    "current": cid == current,
                    "busy": bool(conv and conv.busy),
                    "pending": cid in pending,
                })
        threads.sort(key=lambda item: item["updated"], reverse=True)
        return {"threads": threads, "current": current, "cached": False}

    def delete(self, project: str, cid: str) -> None:
        """删掉一段主对话。顺序：作废提议 → 移出内存 → 删文件 → 最后改索引。"""
        gone = LookupError("这段对话已经不在了")
        if not _ID_SHAPE.fullmatch(cid or ""):
            raise gone
        path = self._folder(project) / f"{cid}.jsonl"
        if not path.exists():
            raise gone
        if self._index_entry(project, cid).get("card") or cid in self._card_conversations(project):
            raise gone
        key = (str(project), cid)
        with self._lock:
            conv = self._open.get(key)
            busy = bool(conv and conv.busy)
        if busy:
            raise RuntimeError("这段对话还在回复，等说完或先点停止再删")
        clerk_tasks.cancel_open(project, cid)
        with self._lock:  # 移出内存和删文件要一起：中间不能有人重新打开它
            conv = self._open.get(key)
            if conv is not None and conv.busy:
                raise RuntimeError("这段对话还在回复，等说完或先点停止再删")
            try:
                path.unlink()
            except OSError as exc:
                raise RuntimeError("没能删掉这段对话，请检查文件夹权限。") from exc
            if conv is not None:
                conv.deleted = True
            self._open.pop(key, None)
            if self._focus.get(str(project)) == cid:
                self._focus[str(project)] = None
            index = self._load_index(project)
            if cid in index:
                index.pop(cid, None)
                folder = self._folder(project)
                folder.mkdir(parents=True, exist_ok=True)
                spare = folder / (INDEX + ".tmp")
                spare.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
                spare.replace(folder / INDEX)

    def focus(self, project: str, cid: str) -> None:
        self._focus[str(project)] = cid or None

    def events(self, project: str, cid: str, since: int = 0, wait: float = 0) -> dict:
        conv = self._conversation(project, cid)
        if conv is None:
            return {"events": [], "open": False, "busy": False}  # 页面看到 open=False 就不再轮询
        with conv.cond:
            if len(conv.events) <= since and wait > 0:
                conv.cond.wait(wait)
            return {"events": list(conv.events[since:]), "open": True, "busy": conv.busy}

    def send(self, project: str, cid: str, text: str) -> str:
        use_blocks = self._blocks_client is not None and blocks_tools.is_blocks_home(project)
        client = self._blocks_client() if use_blocks else self._client()
        if client is None:
            raise RuntimeError("积木库管家还没连上：请先在设置里绑定" if use_blocks else "行政人员还没配置好，请先填写模型密钥")
        # 积木库里贴了像密钥的字：保存和发给模型之前就换掉（项目窗口不变）
        has_secret = blocks_tools.is_blocks_home(project) and bool(blocks._SECRET_TEXT.search(text))
        if has_secret:
            text = blocks._SECRET_TEXT.sub("[密钥已隐藏]", text)
        first = not cid
        if first:
            cid = uuid.uuid4().hex
        conv = self._conversation(project, cid, create=first)
        if conv is None:
            raise RuntimeError("找不到这段对话")
        with conv.cond:
            if conv.busy:
                raise RuntimeError(f"{_who(project)}还在回复：等它说完，或先点“停止”")
            conv.busy = True
            conv.turn += 1
            conv.partial = []
            turn = conv.turn
        try:
            self._record(conv, "user", text)
            if has_secret:
                self._record(conv, "notice", KEY_NOTICE)
            if first:
                self._save_index(project, cid, title=self._short(text))
        except OSError:
            with conv.cond:
                conv.busy = False
            raise RuntimeError("这句话没能保存，请检查磁盘空间或文件夹权限。") from None
        conv.emit({"type": "user", "text": text})
        if has_secret:
            conv.emit({"type": "notice", "text": KEY_NOTICE})
        conv.emit({"type": "turn_started"})
        threading.Thread(target=self._reply, args=(conv, client, turn, first), daemon=True).start()
        return cid

    def stop(self, project: str, cid: str) -> None:
        conv = self._conversation(project, cid)
        if conv is None:
            return
        with conv.cond:
            if not conv.busy:
                return
            conv.turn += 1
            conv.busy = False
            partial = "".join(conv.partial)
        if partial:
            self._keep_reply(conv, partial)
        conv.emit({"type": "turn_done"})

    def _keep_reply(self, conv: _Conversation, text: str) -> None:
        try:
            self._record(conv, "assistant", text)
        except OSError:
            conv.emit({"type": "error", "text": "这段回复没能保存，请检查磁盘空间或文件夹权限。"})

    # ---------- 回话 ----------

    @staticmethod
    def _short(text: str) -> str:
        line = next((part.strip() for part in text.splitlines() if part.strip()), "新对话")
        return line[:TITLE_LIMIT]

    def _index_entry(self, project: str, cid: str) -> dict:
        entry = self._load_index(project).get(cid)
        return entry if isinstance(entry, dict) else {}

    def _card_of(self, conv: _Conversation) -> str:
        return str(self._index_entry(conv.project, conv.id).get("card") or "")

    def _tools_for(self, conv: _Conversation) -> list:
        if blocks_tools.is_blocks_home(conv.project):
            return blocks_tools.TOOLS
        if not self._card_of(conv):
            return TOOLS
        kept = [tool for tool in TOOLS
                if tool.get("function", {}).get("name") not in ("propose_task", "propose_team_change")]
        return kept + [PROPOSE_REVISE]

    def open_card(self, project: str, card_id: str) -> dict:
        """打开这张卡自己的对话。重复打开还是同一段，不把旧进展再播一遍。"""
        from collab.flow import read_flow
        from collab.phase import card_phase
        from desktop.view import _title
        cdir = Path(project) / ".collab" / "cards" / card_id
        if not (cdir / "状态.json").is_file():
            raise LookupError("没有这张卡片")
        need = ""
        req = cdir / "需求卡.md"
        if req.is_file():
            need = req.read_text(encoding="utf-8")
        title = _title(cdir, card_id)
        cid = hashlib.md5(("card:" + card_id).encode()).hexdigest()
        folder = self._folder(project)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{cid}.jsonl"
        if not path.exists():
            path.write_text("", encoding="utf-8")
        self._save_index(project, cid, title=title, card=card_id, updated=time.time())
        played = cdir / "已播报.json"
        if not played.exists():
            events = read_flow(cdir)
            seq = int(events[-1].get("seq") or 0) if events else 0
            played.write_text(json.dumps({"seq": seq}, ensure_ascii=False), encoding="utf-8")
        return {
            "id": card_id, "conversation": cid, "title": title, "phase": card_phase(project, card_id),
            "do": _section(need, "要做什么"), "not_do": _section(need, "不做什么"),
            "done_when": _section(need, "怎么算做完"),
        }

    def _prompt(self, conv: _Conversation) -> list:
        if blocks_tools.is_blocks_home(conv.project):
            role = blocks_tools.role_text()
            for name in conv.manuals:  # 翻过的手册后面每句都带着
                role += "\n\n" + blocks_tools.manual_block(name)
            # 管家看不见屏幕：每一句都告诉它屏幕上现在有没有待处理的提议，省得它以为自己出过（用户真实遇到，2026-11-10）
            open_cards = clerk_tasks.open_proposals(conv.project, conv.id)
            if open_cards:
                names = "、".join(f"「{clerk_tasks.card_label(c)}」" for c in open_cards[:3])
                role += (f"\n\n屏幕上现在有一张待处理的提议：{names}，用户还没点。"
                         "它只管它自己那件事：用户要办的是另一样东西，要另出一个提议。")
            else:
                role += "\n\n屏幕上现在没有待处理的提议。要让用户填密钥、点确认，必须先调用 propose_ 开头的工具出提议。"
        else:
            role = ROLE.format(name=Path(conv.project).name)
            hint = team_incomplete_hint(conv.project)
            if hint:
                role = f"{role} {hint}"
        card_id = self._card_of(conv)
        if card_id:
            from collab.phase import card_phase
            phase = card_phase(conv.project, card_id)
            detail = run_tool(conv.project, "read_card", {"card_id": card_id})
            role += (
                " 这段对话只谈这张卡。用户只和你说话，不能直接指挥执行的工具。"
                "新任务请用户回主对话提。"
                f"当前阶段：{phase.get('label') or ''}。{clerk_tasks.next_step_words(phase)}\n{detail}"
                "\n用户问进度时，按上面此刻的状态答（对话里以前说过的进度可能已经过时），卡住了就说原因和能点的按钮。"
            )
        return [{"role": "system", "content": role}] + conv.messages[-HISTORY_LIMIT:]

    def _alive(self, conv: _Conversation, turn: int) -> bool:
        with conv.cond:
            return conv.turn == turn

    @staticmethod
    def _scrub(conv: _Conversation, client, text):
        """积木库里：模型连接的报错、回话先去掉密钥再显示、再存（报错里可能带着请求头）。项目窗口不变。"""
        if not text or not blocks_tools.is_blocks_home(conv.project):
            return text
        key = getattr(client, "api_key", "") or ""
        if isinstance(key, str) and key:
            text = text.replace(key, "[已隐藏]")
        # 只去掉这次连接的密钥原文和请求头写法；行政教人配工具的正常说明（export XX_API_KEY=…）不能被弄坏
        return _AUTH_HEADER.sub(r"\1 [已隐藏]", text)

    def _reply(self, conv: _Conversation, client, turn: int, first: bool) -> None:
        # done：前几轮已定稿的干净文字；raw：这一轮模型说的原文；sent：已经发给页面的字数
        holder = {"item": uuid.uuid4().hex, "done": "", "raw": "", "sent": 0}

        def on_text(piece: str) -> None:
            # 边打字边显示也不能露出工具指令：攒着看，只发干净的那部分；点停止时存的也是这部分（复查）
            holder["raw"] += self._scrub(conv, client, piece)
            raw = holder["raw"]
            found = _TOOL_MARKUP.search(raw)
            safe = holder["done"] + (raw[:found.start()] if found else _MAYBE_MARKUP.sub("", raw))
            fresh = safe[holder["sent"]:] if len(safe) > holder["sent"] else ""
            with conv.cond:  # 确认这一轮还在和显示这段字一步完成，停止插不进中间（复查第三轮）
                if conv.turn != turn:
                    raise _Stopped
                if fresh:
                    conv.partial.append(fresh)
                    holder["sent"] = len(safe)
                    conv.emit({"type": "delta", "item": holder["item"], "text": fresh})

        def new_round() -> None:
            """一轮模型回话结束：这一轮的干净部分定稿，乱码只管到这一轮为止（复查：不能压住下一轮的话）。"""
            raw = holder["raw"]
            found = _TOOL_MARKUP.search(raw)
            holder["done"] += raw[:found.start()] if found else raw
            holder["raw"] = ""

        def seal(text: str) -> None:
            words = self._scrub(conv, client, (text or "").strip())
            if not words:
                return
            with conv.cond:  # 停止后才回来的旧回话不能再存、再显示，也不能清掉新一轮的话（复查）
                if conv.turn != turn:
                    raise _Stopped
                conv.partial = []  # 这些话已经存好，点停止时不能再存一遍
                conv.emit({"type": "message", "item": holder["item"], "text": words})
                self._keep_reply(conv, words)
            holder.update(item=uuid.uuid4().hex, done="", raw="", sent=0)
            holder["said"] = True
            holder["sealed"] = holder.get("sealed", "") + words + "\n"  # 起标题要用（出提议后这一轮不再有回话）

        on_text.new_round = new_round
        on_text.card_shown = lambda: holder.update(said=True)  # 只出提议不说话也算回过话，不补“没有得到回复”

        failure, reply = None, ""
        try:
            reply = self._tool_loop(conv, client, turn, on_text, seal)
        except _Stopped:
            return
        except ModelError as exc:
            failure, reply = str(exc), self._no_markup(conv, exc.partial or "")
        except Exception:  # 回话线程不能悄悄死掉，否则页面一直转
            failure, reply = f"{_who(conv.project)}这次没回复成功，请再试一次。", "".join(conv.partial)
        failure, reply = self._scrub(conv, client, failure), self._scrub(conv, client, reply)
        with conv.cond:
            if conv.turn != turn:
                return
            conv.busy = False
        if not (reply or "").strip() and not failure and not holder.get("said"):
            reply = "这次没有得到回复，请再说一次。"
        if reply:
            conv.emit({"type": "message", "item": holder["item"], "text": reply})
            self._keep_reply(conv, reply)
        if failure:
            conv.emit({"type": "error", "text": failure})
        conv.emit({"type": "turn_done"})
        named = reply or holder.get("sealed") or ("（出了一个提议）" if holder.get("said") else "")
        if first and named and not failure:  # 第一句就出了提议也起标题（Codex 复查第 2 条）
            threading.Thread(target=self._name, args=(conv, client, named), daemon=True).start()

    def _tool_loop(self, conv: _Conversation, client, turn: int, on_text, seal) -> str:
        """带工具的多轮：有 tool_calls 就执行后继续问；到上限后再问一次，这次不带工具。"""
        messages = self._prompt(conv)
        allowed = self._tools_for(conv)
        names = {tool.get("function", {}).get("name") for tool in allowed}
        parts = []
        proposed = reminded = False  # 这一轮出过提议没有；“说出了提议却没出”提醒过没有（只提醒一次）
        # 积木库和项目窗口主对话都提醒；卡片对话不提醒：那里的修改提议是一轮结束才出框，提醒会让同一条修改记两遍（复查发现）
        in_blocks = blocks_tools.is_blocks_home(conv.project)
        can_propose = (in_blocks or not self._card_of(conv)) and any(
            str(n or "").startswith("propose_") for n in names)
        limit = BLOCKS_TOOL_ROUNDS if blocks_tools.is_blocks_home(conv.project) else MAX_TOOL_ROUNDS
        for round_no in range(limit + 1):
            if not self._alive(conv, turn):
                raise _Stopped
            use_tools = round_no < limit
            if not use_tools and round_no:
                messages.append({"role": "user", "content": _LAST_ROUND})
            if round_no:
                getattr(on_text, "new_round", lambda: None)()
            try:
                answer = client.chat(messages, on_text=on_text, tools=allowed if use_tools else None)
            except ModelError as exc:
                prior = "\n".join(parts)
                if prior:
                    extra = (exc.partial or "").strip()
                    merged = prior if not extra else f"{prior}\n{extra}"
                    raise ModelError(str(exc), partial=merged) from exc
                raise
            reply = self._no_markup(conv, answer.get("text") or "")
            if reply.strip():
                parts.append(reply.strip())
            calls = answer.get("tool_calls") or []
            if (use_tools and not calls and can_propose and not proposed and not reminded
                    and round_no < limit - 1
                    and (claims_card(reply) or points_at_card(reply) or (not in_blocks and claims_task_box(reply)))):
                open_now = clerk_tasks.open_proposals(conv.project, conv.id)  # 只读一次（复查）
                if claims_card(reply) or claims_task_box(reply) or not open_now:
                    reminded = True  # 说出了提议、这一轮却没出：提醒它一次，真出提议或如实说没出；上面有没处理的提议就点名
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "user", "content": _claim_reminder(open_now)})
                    continue
            if not use_tools or not calls:
                return "\n".join(parts) if parts else reply
            if any(str(call.get("name") or "").startswith("propose_") for call in calls):
                seal("\n".join(parts))
                parts = []
            messages.append({
                "role": "assistant",
                "content": reply or None,
                "tool_calls": [{
                    "id": call.get("id") or f"call{round_no}_{i}",
                    "type": "function",
                    "function": {"name": call.get("name") or "", "arguments": call.get("arguments") or "{}"},
                } for i, call in enumerate(calls)],
            })
            shown_now = False
            for i, call in enumerate(calls):
                if not self._alive(conv, turn):
                    raise _Stopped
                if shown_now and blocks_tools.is_blocks_home(conv.project):
                    break  # 积木库：同一批里出提议之后的不再执行，免得刚出的提议被顶掉、提议后还在忙（Codex 复查第 1 条）
                call_id = call.get("id") or f"call{round_no}_{i}"
                name = call.get("name") or ""
                raw = call.get("arguments") or "{}"
                if blocks_tools.is_blocks_home(conv.project):
                    raw = self._forget_keys(conv, client, raw)  # 已知密钥原文一进门就去掉（复查第二轮第 1 条）
                args = {}  # 参数看不懂时下面记动作也要有东西用
                try:
                    args = json.loads(raw)
                    if not isinstance(args, dict):
                        raise ValueError("not a dict")
                    if blocks_tools.is_blocks_home(conv.project):  # 转义写法解析后才变回密钥：再去一遍（复查第三轮）
                        args = self._forget_keys_in(conv, client, args)
                    if name not in names:
                        result = "没有这个工具"
                    elif name == "propose_task":
                        result = clerk_tasks.propose(conv, args)
                    elif name == "propose_team_change":
                        result = clerk_tasks.propose_team_change(conv, args)
                    elif name == "propose_profile":
                        result = clerk_tasks.propose_profile(conv, args)
                    elif name == "propose_revise":
                        result = clerk_tasks.propose_revise(conv, args)
                    elif name == "propose_install":
                        result = clerk_tasks.propose_install(_ThisTurn(conv, turn), args)
                    elif name == "propose_connect_cli":
                        result = clerk_tasks.propose_connect_cli(_ThisTurn(conv, turn), args)
                    elif name == "propose_connect_api":
                        result = clerk_tasks.propose_connect_api(_ThisTurn(conv, turn), args)
                    elif name == "propose_web_install":
                        result = clerk_tasks.propose_web_install(_ThisTurn(conv, turn), args)
                    elif name == "propose_connect_search":
                        result = clerk_tasks.propose_connect_search(_ThisTurn(conv, turn), args)
                    elif blocks_tools.is_blocks_home(conv.project):
                        result = blocks_tools.run(conv.project, name, args)
                    else:
                        result = run_tool(conv.project, name, args)
                except (ValueError, TypeError):
                    result = "参数看不懂"
                except OSError:
                    result = "存不下来：请用户检查磁盘空间或文件夹权限，这次没有生成确认框。"
                if name.startswith("propose_") and str(result).startswith(_CARD_SHOWN):
                    proposed = shown_now = True  # 真出了提议才算；出提议工具返回错误（缺字段、不合格）不算
                label = LABELS.get(name, "读 资料")
                activity = {"type": "activity", "item": call_id, "label": label, "status": "done"}
                if blocks_tools.is_blocks_home(conv.project) and name in names:
                    if name.startswith("propose_") and not str(result).startswith(_CARD_SHOWN):
                        label = activity["label"] = "没出提议"  # 被程序拦下的不能算“出了提议”
                    plain = args if isinstance(args, dict) else {}
                    said = self._forget_keys(conv, client, blocks_tools.describe(name, plain, str(result)))
                    activity["text"] = said
                    # 停止后才回来的结果不记、不改手册、不显示；查和写一步完成，停止插不进中间（复查两轮第 4 条）
                    with conv.cond:
                        if conv.turn != turn:
                            raise _Stopped
                        result = self._with_manual(conv, name, plain, str(result))
                        try:
                            self._record_line(conv, {"role": "action", "name": name, "label": label, "text": said})
                        except OSError:
                            pass
                        conv.emit(activity)
                else:
                    conv.emit(activity)
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
            if shown_now and blocks_tools.is_blocks_home(conv.project):
                # 积木库：提议一出来这一轮就结束，提议后不再说话（用户 2026-11-10：上面文字、下面提议，提议后的话都是重复）。
                # 会替换哪个积木块写在提议上，失效的旧提议自己会变样；下一句话的开头也会告诉管家屏幕上有哪个提议
                getattr(on_text, "card_shown", lambda: None)()
                return "\n".join(parts)
        return "\n".join(parts)

    @staticmethod
    def _no_markup(conv: _Conversation, text: str) -> str:
        """模型把工具调用写成了文字：那一段不给用户看，换成一句大白话。"""
        found = _TOOL_MARKUP.search(text or "")
        if not found:
            return text
        kept = text[:found.start()].rstrip()
        note = f"（刚才那一步没真正做成。跟{_who(conv.project)}说“继续”，它会接着做。）"
        return f"{kept}\n\n{note}" if kept else note

    def _forget_keys(self, conv: _Conversation, client, text: str) -> str:
        """去掉这次模型连接的密钥、联网搜索的密钥原文（通用写法的密钥另由打码规则管）。"""
        search_key = clerk_tasks.search_key()
        if search_key:
            text = text.replace(search_key, "[已隐藏]")
        return self._scrub(conv, client, text)

    def _forget_keys_in(self, conv: _Conversation, client, value):
        """参数里每一处文字都去掉已知密钥（字典、列表一层层往下找）。"""
        if isinstance(value, str):
            return self._forget_keys(conv, client, value)
        if isinstance(value, dict):
            return {k: self._forget_keys_in(conv, client, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._forget_keys_in(conv, client, v) for v in value]
        return value

    def _with_manual(self, conv: _Conversation, name: str, args: dict, result: str) -> str:
        """积木库：翻手册记下来；没翻过这类手册就用了这类工具，把手册附在结果后面（只附一次）。"""
        if name == "read_manual":
            wanted = str(args.get("name") or "")
            if wanted in blocks_tools.MANUALS:
                self._open_manual(conv, wanted)
            return result
        manual = blocks_tools.MANUAL_OF.get(name)
        if not manual or manual in conv.manuals:
            return result
        self._open_manual(conv, manual)
        return (f"{result}\n\n（你还没翻《{blocks_tools.MANUALS[manual]}》，下面附上，接下来照着办。）\n"
                + blocks_tools.manual_block(manual))

    def notify(self, project: str, conversation: str, text: str, item: str = "") -> str:
        """往来源对话写一条行政通知并发 message 事件，返回这条的编号；对话不存在返回空（由调用方决定是否仍记已汇报）。
        给了 item：改写那一条（同一个人的“没动静”提醒只留一条，第二轮 ㉞）；它还是对话最后一条时，记录里也改写。"""
        if not _ID_SHAPE.fullmatch(conversation or ""):
            return ""
        conv = self._conversation(project, conversation, create=False)
        if conv is None:
            return ""
        if item and self._rewrite_last(conv, item, text):
            conv.emit({"type": "message", "item": item, "text": text})
            return item
        item = uuid.uuid4().hex
        conv.emit({"type": "message", "item": item, "text": text})
        try:
            self._record(conv, "assistant", text, item=item)
        except OSError:
            conv.emit({"type": "error", "text": "这段回复没能保存，请检查磁盘空间或文件夹权限。"})
        return item

    def _rewrite_last(self, conv: _Conversation, item: str, text: str) -> bool:
        """对话记录最后一条就是 item 那条：把它的字换掉（模型看到的那条也换）。不是最后一条就不动，返回 False。"""
        path = self._folder(conv.project) / f"{conv.id}.jsonl"
        with self._write_lock:
            return self._rewrite_last_locked(conv, path, item, text)

    def _rewrite_last_locked(self, conv: _Conversation, path: Path, item: str, text: str) -> bool:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return False
        try:
            last = json.loads(lines[-1])
        except ValueError:
            return False
        if last.get("item") != item:
            return False
        last["text"] = text
        lines[-1] = json.dumps(last, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for m in reversed(conv.messages):
            if m.get("role") == "assistant":
                m["content"] = text
                break
        return True

    def decide_proposal(self, project: str, conversation: str, pid: str, decision: str) -> dict:
        """用户点确认或先不做。不经过模型。"""
        from collab.tasks import TaskError
        try:
            result = clerk_tasks.decide(project, conversation, pid, decision)
        except LookupError as exc:
            raise LookupError(str(exc)) from exc
        except TaskError as exc:
            raise RuntimeError(str(exc)) from exc
        except RuntimeError:
            raise
        conv = self._conversation(project, conversation)
        if conv is None and not result.get("repeat"):
            raise LookupError("找不到这段对话")
        if conv is not None and not result.get("repeat"):
            kind = result.get("kind") or "task"
            if kind == "profile":
                text = "项目说明已保存。" if result["state"] == "confirmed" else "好的，先不保存。"
            elif kind == "team":
                text = "团队设置已更新。" if result["state"] == "confirmed" else "好的，先不改团队设置。"
            elif kind == "revise":
                text = result.get("reply") or ("已交给开发，做完再告诉你。" if result["state"] == "confirmed" else "好的，先不改。")
            elif kind in clerk_tasks._BLOCK_KINDS:  # 积木库的提议种类只在 tasks.py 定义一次（走查发现这里漏了新种类）
                text = result["reply"] if result["state"] == "confirmed" else "好的，先不做。"
            elif result["state"] == "confirmed":
                text = f"已生成卡片 {result['card']}，接下来写方案。"
            else:
                text = "好的，先不做。"
            conv.emit({"type": "proposal_done", "id": pid, "state": result["state"],
                       "card": result.get("card") or ""})
            conv.emit({"type": "message", "text": text, "item": uuid.uuid4().hex})
            self._keep_reply(conv, text)
        return result

    def _name(self, conv: _Conversation, client, reply: str) -> None:
        """起名失败不要紧，标题先用用户的第一句话。"""
        words = NAMING.format(user=conv.messages[0]["content"][:300], reply=reply[:300])
        try:
            title = client.chat([{"role": "user", "content": words}])["text"].strip().strip("《》\"'“”")
        except ModelError:
            return
        if title:
            if not conv.deleted:
                self._save_index(conv.project, conv.id, title=self._short(title))
