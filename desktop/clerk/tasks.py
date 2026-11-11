"""行政提议与后台任务：模型只能提议，用户确认才建卡；后台子进程与本机服务分离。"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from collab.config import ConfigError, load_team, save_team_member
from collab.flow import card_title, record as flow_record
from collab.merge import merge_unfinished, sync_copy
from collab.phase import card_phase, process_is_alive, process_start
from collab.tasks import TaskError, new_card, revise_card
from collab.workspace import PROFILE_TEMPLATE, has_blanks, profile_path
import hashlib
import math
import re
from datetime import datetime

from collab import blocks, blocks_check, blocks_install, blocks_web
from collab.blocks import BlockError
from collab.providers import account_for, find as find_provider

COLLAB_PACKAGE = Path(__file__).resolve().parent.parent.parent / "collab"

FOLDER = Path(".collab") / "行政提议"
_FIELDS = ("title", "do", "not_do", "done_when")
_FIELD_NAMES = {"title": "标题", "do": "要做什么", "not_do": "不做什么", "done_when": "怎么算做完"}
_TEAM_ROLES = frozenset({"dispatcher", "tester", "developer", "reviewer"})
_TEAM_TOOLS = frozenset({"codex", "cursor", "agy"})
_TEAM_REVIEWER_TOOLS = _TEAM_TOOLS | {"none"}
_PROFILE_LABELS = ("做什么", "怎么启动", "怎么测试", "禁区", "技术栈")
_lock = threading.Lock()


class ReviseNotStarted(RuntimeError):
    """要求已经写进卡片，只是没能开工。这时确认框算处理完了。"""


class TaskBusy(Exception):
    """同一种后台任务已经在跑。"""


def _folder(project: str) -> Path:
    return Path(project) / FOLDER


def _apply_team_change(project: str, role: str, tool: str) -> None:
    """合并写入单个角色；配置读不出时抛错，不覆盖。"""
    save_team_member(project, role, tool)
    load_team(project)


_PID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # 程序生成的编号；带路径的（../）一律不认，免得读写资料夹外面的文件


def _path(project: str, pid: str) -> Path:
    if not isinstance(pid, str) or not _PID.fullmatch(pid):
        raise LookupError("找不到这个提议")
    return _folder(project) / f"{pid}.json"


def load_proposal(project: str, pid: str) -> Optional[dict]:
    if not isinstance(pid, str) or not _PID.fullmatch(pid):
        return None
    path = _path(project, pid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") == pid else None


def _save(project: str, data: dict) -> None:
    folder = _folder(project)
    folder.mkdir(parents=True, exist_ok=True)
    path = _path(project, data["id"])
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    spare.replace(path)


def _open_in_conversation(project: str, conversation: str) -> list:
    folder = _folder(project)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("conversation") == conversation and data.get("state") == "open":
            found.append(data)
    return found


def conversations_with_open(project: str) -> set:
    """哪些对话里有还没确认的提议。左栏每次刷新都问，只扫一遍文件夹。"""
    folder = _folder(project)
    if not folder.is_dir():
        return set()
    found = set()
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("state") == "open" and data.get("conversation"):
            found.add(data["conversation"])
    return found


def cancel_open(project: str, conversation: str) -> None:
    """对话删掉时，里面没确认的提议一起作废。"""
    for data in _open_in_conversation(project, conversation):
        decide(project, conversation, data["id"], "cancel")


def propose(conv, arguments: dict) -> str:
    """模型调用 propose_task：字段齐全才生成提议；否则告诉它还缺什么。"""
    missing = []
    values = {}
    for key in _FIELDS:
        value = str((arguments or {}).get(key) or "").strip()
        values[key] = value
        if not value:
            missing.append(_FIELD_NAMES[key])
    if missing:
        return "还缺：" + "、".join(missing) + "，先问用户"
    with _lock:
        for old in _open_in_conversation(conv.project, conv.id):
            old["state"] = "replaced"
            _save(conv.project, old)
            conv.emit({"type": "proposal_done", "id": old["id"], "state": "replaced", "card": old.get("card") or ""})
        pid = uuid.uuid4().hex
        data = {
            "id": pid, "conversation": conv.id, "kind": "task",
            "title": values["title"], "do": values["do"], "not_do": values["not_do"],
            "done_when": values["done_when"], "state": "open", "card": "", "time": time.time(),
        }
        _save(conv.project, data)
        _append_proposal_line(conv, pid)
        event = {k: data[k] for k in ("id", "kind", "title", "do", "not_do", "done_when", "state", "card")}
        conv.emit(dict(event, type="proposal"))
    return "已向用户出示确认框，等用户点确认或先不做。还没建卡。"


_BLOCK_KINDS = ("install", "connect", "connect_api", "web_install", "connect_search")
_STRIKES = 3  # 一段对话里行政起草的接法卡连续检测不通过几次就停
_HTTPS_URL = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\s]*)?")


def open_proposals(project: str, conversation: str) -> list:
    """这段对话里还没处理的提议（行政的对话程序只能通过这里查，不自己翻提议文件夹）"""
    return _open_in_conversation(project, conversation)


def card_label(data: dict) -> str:
    """提议卡在对行政说话时叫什么：接入卡用名字，安装卡用 label（“用 npm 装 Codex”），任务卡用标题"""
    return str(data.get("name") or data.get("label") or (data.get("block") or {}).get("name")
               or data.get("title") or "一张卡")


_NO_WAIT = contextlib.nullcontext()


def _publish_block(conv, data: dict) -> str:
    """积木库的三种提议共用：先存新提议，再把旧的标成被替换；新的存不上时旧的不动。
    接法卡放在 block 字段；card 字段在提议里只表示项目窗口的任务卡编号，这里永远是空的。"""
    expected = getattr(conv, "turn_expected", None)
    cond = getattr(conv, "cond", None)
    with _lock, (cond if expected is not None and cond is not None else _NO_WAIT):
        # 积木库：出卡要花时间核对，这期间用户点了停止，旧卡就不出了，也不顶掉新卡（锁的先后和确认卡片一致）
        if expected is not None and getattr(conv, "turn", expected) != expected:
            return "没有出卡：用户已经停止了这一轮。"
        olds = _open_in_conversation(conv.project, conv.id)
        pid = uuid.uuid4().hex
        data = dict(data, id=pid, conversation=conv.id, card="", state="open", time=time.time())
        _save(conv.project, data)
        for old in olds:
            old["state"] = "replaced"
            _save(conv.project, old)
            conv.emit({"type": "proposal_done", "id": old["id"], "state": "replaced",
                       "card": old.get("card") if isinstance(old.get("card"), str) else ""})
        _append_proposal_line(conv, pid)
        conv.emit(event_from_proposal(data))
    reply = "已向用户出示确认框，等用户点确认或先不做。用户确认之前，不能说已经办好。"
    voided = "、".join(f"「{card_label(old)}」" for old in olds)
    if voided:  # 同一段对话一次只留一张没处理的卡：行政不知道的话会说“两张都能点”（第 9 项走查发现）
        reply += f"这段对话里上面没处理的{voided}已经失效、点不了了；用户还要它，就再出一次。"
    if data.get("replaces"):  # 行政看不到卡上的字：会替换哪一块要在这里告诉它
        reply += f"这张卡会替换基础层里的「{data['replaces']}」，检测通过放进去后旧的那块就没了。"
    return reply


def _saved_block(project: str, block_id: str) -> Optional[dict]:
    """基础层里同编号的卡；没有返回 None，坏了返回空字典（也算占着这个编号）。"""
    if not blocks._card_path(block_id, project).is_file():
        return None
    try:
        return blocks.load_card(block_id, root=project)
    except BlockError:
        return {}


def _record_time(record: dict) -> float:
    try:
        return datetime.fromisoformat(str(record.get("time"))).timestamp()
    except ValueError:
        return 0.0


def _strikes(conv) -> int:
    """这段对话里行政起草、用户确认过的接法卡，从最近一张往前数，连续几张检测不通过。
    按对话算、不按编号算，换个编号绕不过去；预置模板不算，登录好了随时能再接。"""
    folder = _folder(conv.project)
    tried = []
    for path in folder.glob("*.json") if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(data, dict) and data.get("conversation") == conv.id and data.get("kind") == "connect"
                and data.get("state") == "confirmed" and not data.get("verified")
                and isinstance(data.get("block"), dict) and isinstance(data.get("decided"), (int, float))):
            tried.append(data)
    count = 0
    seen = set()
    for data in sorted(tried, key=lambda d: d["decided"], reverse=True):
        if data["block"].get("id") in seen:
            continue  # 同一个编号只认最近那张，否则后一张的失败会被前一张再算一次
        seen.add(data["block"].get("id"))
        since = math.floor(data["decided"])  # 记录的时间只精确到秒
        try:
            checks = [r for r in blocks.records(data["block"].get("id"), root=conv.project)
                      if r.get("type") == "check" and _record_time(r) >= since]
        except BlockError:
            checks = []
        if not checks:
            continue  # 还没检测（或检测没跑起来），不算不通过
        if checks[-1].get("ok"):
            break
        count += 1
    return count


def propose_install(conv, args: dict) -> str:
    """模型调用 propose_install：只能提议核实名单里的编号。"""
    install_id = str((args or {}).get("install_id") or "").strip()
    if install_id not in blocks_install.INSTALLS:
        return "这个编号不在核实名单里。先用 install_options 查名单里的编号；名单里没有的不能提议安装，请用户按官网自己装。"
    info = blocks_install.describe(install_id)
    info.pop("id", None)  # 安装方式的编号放在 install 字段，不能盖掉提议自己的编号
    return _publish_block(conv, dict(info, kind="install", install=install_id))


# 管家起草接法卡的样子：写错时附在回话里，照着改一次就对（用户真实接高德时连错 3 次，2026-11-10）
CARD_EXAMPLE = json.dumps({"id": "amap-gui", "kind": "cli", "name": "高德地图", "command": "amap-gui", "role": "tool",
                           "probe": ["status"], "ok_words": ["running"], "version_args": [],
                           "secrets": [{"env": "AMAP_KEY", "label": "高德 Key"}]}, ensure_ascii=False)


def _fill_card(drafted: dict, project) -> dict:
    """程序推得出来的项替管家补上：类别默认 cli；编号默认按命令名，和基础层里别的工具撞了就加尾号（复查）。"""
    card = dict(drafted, source="clerk")
    card.setdefault("kind", "cli")
    command = card.get("command")
    if "id" not in card and isinstance(command, str):
        slug = re.sub(r"[^a-z0-9-]+", "-", command.lower()).strip("-")[:36]
        if slug:
            for candidate in [slug] + [f"{slug}-{n}" for n in range(2, 10)]:
                saved = _saved_block(project, candidate)
                if saved is None or saved.get("command") == command:
                    card["id"] = candidate
                    break
            else:  # 都被别的工具占了：不自动起编号，交给下面的检查报“编号不对”，管家自己起一个
                card["id"] = None
    return card


def propose_connect_cli(conv, args: dict) -> str:
    """模型调用 propose_connect_cli：预置模板直接用；起草的卡来源强制写成 clerk，没核实过的要亮出完整命令。"""
    template = args.get("template")
    drafted = args.get("card")
    try:
        if template:
            block = blocks.builtin_card(template)
        elif drafted is not None:
            block = blocks.validate_card(_fill_card(drafted, conv.project) if isinstance(drafted, dict) else drafted)
        else:
            return "要填 template（codex、cursor 或 agy），或者起草好的 card。"
    except BlockError as exc:
        if template:
            return str(exc)
        return (f"{exc}。照这个样子重写接法卡再出卡（只能有这些项；AI 助手把 role、probe、ok_words 换成 "
                f"\"run\": [\"{{prompt}}\"] 这样的写法）：{CARD_EXAMPLE}")
    verified = blocks.is_verified(block)
    if not template and not verified:
        if block["id"] in blocks.BUILTIN:
            return f"{block['id']} 是预置接法的编号，起草的卡要换一个编号。"
        if _strikes(conv) >= _STRIKES:
            return (f"这段对话里起草的接法卡已经连续 {_STRIKES} 次检测不通过。先停下，"
                    "把卡在哪一步告诉用户，不要再提议。")
    data = {"kind": "connect", "block": block, "name": block["name"], "verified": verified,
            "category": blocks.CATEGORY_LABELS[blocks.category(block)]}
    if not verified:
        data["preview"] = blocks_check.preview(block)
    if block.get("secrets"):  # 每把密钥一个框：页面只拿到钥匙串账号名和显示名
        data["secrets"] = [{"account": blocks.secret_account(block["id"], item["env"]), "label": item["label"]}
                           for item in block["secrets"]]
    saved = _saved_block(conv.project, block["id"])
    if saved is not None:
        data["replaces"] = saved.get("name") or block["id"]
    return _publish_block(conv, data)


def _api_id(project: str, provider: str, account: str) -> str:
    """编号由程序起，一家只占一块（用户 2026-11-10 定）：腾讯云、阿里百炼就是服务商编号；其他平台按网址起。
    两个网址变成编号后一样（a-b.example 和 a.b.example）、或者太长被截短时，加一段按原网址算的短码区分。"""
    if provider != "other":
        return provider
    host = account.split(":", 1)[-1].lower()
    for card in blocks.list_cards(root=project):  # 同一个网址已经有一块：就用它的编号（第二轮复查第 7 条）
        if card.get("kind") == "llm_api" and card.get("key_account") == account:
            return card["id"]
    slug = "other-" + re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    saved = _saved_block(project, slug) if len(slug) <= 40 else {}
    if saved is None or saved.get("key_account") == account:
        return slug
    return f"{slug[:33].strip('-')}-{hashlib.sha1(host.encode('utf-8')).hexdigest()[:6]}"


def propose_connect_api(conv, args: dict) -> str:
    """模型调用 propose_connect_api：按整家接，不选模型。认得的服务商用它固定的地址和名字；
    不认得的按“其他”处理，必须给 https 地址，model 只在平台不给名单时用来试通。"""
    if blocks._has_secret(args):
        return "参数里有像密钥的东西。密钥只能由用户填在提议卡的密钥框里，不要放进参数，也不要让用户贴在聊天里。"
    provider = str(args.get("provider") or "").strip().lower()
    model = str(args.get("model") or "").strip()
    name = str(args.get("name") or "").strip()
    base_url = str(args.get("base_url") or "").strip()
    known = find_provider(provider)
    if known is None or provider == "other":
        provider = "other"
        if not _HTTPS_URL.fullmatch(base_url):
            return "这家不在预置名单里，要填它官方文档里写的接口地址，以 https:// 开头，后面跟网址，不能有空格。"
    else:
        base_url = known["base_url"]  # 认得的服务商地址固定，行政改不了，密钥不会被引到别处
        name = known["name"]  # 整家一块：名字就是服务商
        # 不带模型。例外：这家上次检测没通过（自动试的几个都不行），用户另给的那个要留着试通，不然永远试同样几个（复查第 1 条）
        last = blocks.last_record(provider, conv.project, type="check")
        if not (last and not last.get("ok") and last.get("status") == "fail"):  # 已接上、额度不足的不用（第二轮复查第 8 条）
            model = ""
    try:
        account = account_for(provider, base_url)
    except ValueError:
        return "接口地址不对，要以 https:// 开头，后面跟网址。"
    name = (name or account.split(":", 1)[-1])[:40]
    block = {"id": _api_id(conv.project, provider, account), "kind": "llm_api", "name": name,
             "provider": provider, "base_url": base_url, "key_account": account,
             "source": "clerk"}
    if model:
        block["model"] = model
    try:
        block = blocks.validate_card(block)
    except BlockError as exc:
        return str(exc)
    data = {"kind": "connect_api", "block": block, "name": name, "provider": provider}
    if model:
        data["model"] = model
    saved = _saved_block(conv.project, block["id"])
    if saved is not None:
        data["replaces"] = saved.get("name") or block["id"]
    return _publish_block(conv, data)


key_source = None  # 本机服务启动时设：钥匙串账号 -> 密钥（只在内存里），行政的搜索要用 Tavily 那把


def search_key() -> str:
    """基础层里接了联网搜索、钥匙串里也有密钥，才给；否则给空。"""
    if key_source is None:
        return ""
    try:
        card = blocks.load_card(blocks.SEARCH_PRESETS["tavily"]["id"], root=None)
    except BlockError:
        return ""
    if card.get("kind") != "search":
        return ""
    return str(key_source(card["key_account"]) or "")


# 代码托管站、个人主页：不管管家怎么说，一律算非官方（积木库管家方案第三节，用户选“从严”）
_CODE_HOSTS = ("github.com", "gitee.com", "gitlab.com", "npmjs.com", "bitbucket.org", "sourceforge.net",
               "github.io", "gitee.io", "gitlab.io")
_MAKER_LIMIT, _RISK_LIMIT = 60, 120


def code_hosting(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    try:  # 全角句号、大写这类写法先规范成一种（复查第 1 条）
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        host = host.replace("。", ".").replace("．", ".").replace("｡", ".")
    host = host.rstrip(".")
    return any(host == h or host.endswith("." + h) for h in _CODE_HOSTS)


def _one_line(value, limit: int) -> str:
    text = " ".join(blocks_check._mask(str(value or "")).split())  # 管家写的话：先打码（复查第 3 条）
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _need_risk(on_code_host: bool) -> str:
    why = "网址在代码托管站上（或跳转到了那里），" if on_code_host else ""
    return (f"没有出卡：{why}这个算非官方（别人做的），要在 risk 里写清风险（比如读浏览器登录、破解接口、可能封号、"
            "个人维护可能停更），用户才知道自己在装什么。先跟用户说清，再带上 risk 重新出卡。")


def propose_web_install(conv, args: dict) -> str:
    """模型调用 propose_web_install：程序自己读页面核对命令、查安装包底细，都过了才出卡。
    官方不官方：管家说是、而且不在代码托管站上才算官方；非官方必须写风险（积木库管家方案第三、四节）。"""
    args = args or {}
    url = str(args.get("url") or "").strip()
    line = str(args.get("install_command") or "").strip()
    command = str(args.get("command") or "").strip() or None
    official = args.get("official") is True and not code_hosting(url)
    maker = _one_line(args.get("maker"), _MAKER_LIMIT)
    risk = _one_line(args.get("risk"), _RISK_LIMIT)
    if not official and not risk:
        return _need_risk(args.get("official") is True)
    try:
        spec = blocks_web.verify_install(url, line, command=command, search_key=search_key() or None)
    except BlockError as exc:
        return f"没有出卡：{exc}"
    except Exception:  # noqa: BLE001 — 网站、npm 回的东西什么样都有，不能让行政这一轮断掉（复查第 8 条）
        return "没有出卡：核对时出了意外，换一页官网再试，或者请用户自己装"
    if official and code_hosting(spec.get("source")):  # 跳转到了代码托管站：按最后到的那一页算（复查第 1 条）
        official = False
        if not risk:
            return _need_risk(True)
    package = spec.get("package") or {}
    facts = []
    if spec["type"] == "npm":
        facts.append(f"npm 上第一次发布：{package.get('created') or '查不到'}")
        facts.append(f"发布者：{'、'.join(package.get('publishers') or []) or '查不到'}")
        facts.append(f"最新版本：{package.get('latest') or '查不到'}")
    elif spec["type"] == "brew":
        facts.append("Homebrew 官方目录里有这一项")
    else:
        facts.append(f"脚本来自：{spec['script_host']}")
    data = {"kind": "web_install", "label": f"从网上装 {spec['command']}", "tool": spec["command"],
            "command": spec["command_text"], "source": spec["source"], "domain": spec["domain"],
            "danger": spec["danger"], "new": bool(package.get("new")), "facts": facts,
            "found_by": spec["found_by"], "spec": spec, "official": official, "maker": maker, "risk": risk}
    return _publish_block(conv, data)


def propose_connect_search(conv, args: dict) -> str:
    """模型调用 propose_connect_search：接法预置（Tavily），卡上只有一个密钥框。"""
    block = blocks.search_card("tavily")
    data = {"kind": "connect_search", "block": block, "name": block["name"], "verified": True,
            "category": blocks.CATEGORY_LABELS["search"], "key_account": block["key_account"]}
    saved = _saved_block(conv.project, block["id"])
    if saved is not None:
        data["replaces"] = saved.get("name") or block["id"]
    return _publish_block(conv, data)


def _block_result(data: dict, repeat: bool) -> dict:
    kind = data["kind"]
    result = {"ok": True, "state": "confirmed", "repeat": repeat, "kind": kind, "card": ""}
    if kind == "install":
        result["install"] = data.get("install") or ""
        result["reply"] = f"已确认：{data.get('label') or data.get('install')}。"
    elif kind == "web_install":
        result["spec"] = data.get("spec")
        result["reply"] = f"已确认：{data.get('label') or ''}。"
    else:
        result["block"] = data.get("block")
        # 点确认只是开始检测，不等于接好了（用户真实看到“已确认接入”下面是“模型名不对”，2026-11-10）
        result["reply"] = f"你点了确认，开始检测「{data.get('name') or ''}」。接上了才能放进基础层，结果看上面那张卡。"
    if kind == "connect":
        result["approved"] = data.get("preview")
    return result


def propose_team_change(conv, arguments: dict) -> str:
    """模型调用 propose_team_change：角色与工具合法才生成团队设置提议。"""
    args = arguments if isinstance(arguments, dict) else {}
    role = str(args.get("role") or "").strip()
    tool = str(args.get("tool") or "").strip()
    if role not in _TEAM_ROLES:
        return "角色必须是调度员、写测试、写代码、复查之一，先问用户要改哪一项。"
    allowed = _TEAM_REVIEWER_TOOLS if role == "reviewer" else _TEAM_TOOLS
    if tool not in allowed:
        return "工具不对：复查可选 Codex、Cursor、agy 或 none；其他角色只能是 codex、cursor、agy。先问用户。"
    try:
        team = load_team(conv.project)
    except ConfigError as exc:
        return str(exc)
    before = team.get(role)
    before_s = before if isinstance(before, str) else ("" if before is None else str(before))
    with _lock:
        for old in _open_in_conversation(conv.project, conv.id):
            old["state"] = "replaced"
            _save(conv.project, old)
            conv.emit({"type": "proposal_done", "id": old["id"], "state": "replaced", "card": old.get("card") or ""})
        pid = uuid.uuid4().hex
        data = {
            "id": pid, "conversation": conv.id, "kind": "team",
            "role": role, "tool": tool, "before": before_s,
            "state": "open", "card": "", "time": time.time(),
        }
        _save(conv.project, data)
        _append_proposal_line(conv, pid)
        event = {
            "type": "proposal", "id": data["id"], "kind": "team",
            "role": data["role"], "tool": data["tool"], "before": data["before"],
            "state": data["state"], "card": data["card"],
        }
        conv.emit(event)
    return "已向用户出示确认框，等用户点确认或先不做。确认后才会写入团队配置。"


def _profile_is_free(project: str) -> bool:
    """只有没写过或还是空模板时才能保存，用户写过的内容不能盖掉。"""
    path = profile_path(project)
    if not path.exists():
        return True
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return not text or text == PROFILE_TEMPLATE.strip()


def propose_profile(conv, arguments: dict) -> str:
    """模型调用 propose_profile：内容完整才给用户看。"""
    text = str((arguments or {}).get("profile") or "").strip()
    if not _profile_is_free(conv.project):
        return "项目里已经有项目说明，不能覆盖。"
    missing = [label for label in _PROFILE_LABELS if label not in text]
    if missing:
        return "还缺：" + "、".join(missing) + "。"
    if has_blanks(text):
        return "还有没填完的地方，不知道的就问用户。"
    with _lock:
        for old in _open_in_conversation(conv.project, conv.id):
            old["state"] = "replaced"
            _save(conv.project, old)
            conv.emit({"type": "proposal_done", "id": old["id"], "state": "replaced", "card": old.get("card") or ""})
        pid = uuid.uuid4().hex
        data = {"id": pid, "conversation": conv.id, "kind": "profile", "profile": text + "\n",
                "state": "open", "card": "", "time": time.time()}
        _save(conv.project, data)
        _append_proposal_line(conv, pid)
        conv.emit(event_from_proposal(data))
    return "已向用户出示确认框，等用户点确认或先不做。确认后才会保存。"


def card_conversation_id(card_id: str) -> str:
    return hashlib.md5(("card:" + card_id).encode()).hexdigest()


def _take_pending(project: str, card_id: str, count: int) -> None:
    """只去掉这张确认框带走的那几条，之后新记下的话留着。"""
    path = Path(project) / ".collab" / "cards" / card_id / "待转达.jsonl"
    if count <= 0 or not path.exists():
        return
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    path.write_text("".join(line + "\n" for line in lines[count:]), encoding="utf-8")


def _append_pending(project: str, card_id: str, text: str) -> None:
    path = Path(project) / ".collab" / "cards" / card_id / "待转达.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"text": text, "time": time.time()}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _show_revise_box(conv, card_id: str, text: str, pending: int = 0) -> str:
    with _lock:
        for old in _open_in_conversation(conv.project, conv.id):
            if old.get("kind") == "revise":
                old["state"] = "replaced"
                _save(conv.project, old)
                conv.emit({"type": "proposal_done", "id": old["id"], "state": "replaced", "card": old.get("card") or ""})
                if int(old.get("pending") or 0) > 0:  # 记下的话不能被新框挤掉
                    pending += int(old["pending"])
                    text = str(old.get("text") or "") + "\n" + text
        pid = uuid.uuid4().hex
        data = {
            "id": pid, "conversation": conv.id, "kind": "revise",
            "card": card_id, "text": text, "state": "open", "time": time.time(), "pending": pending,
        }
        _save(conv.project, data)
        _append_proposal_line(conv, pid)
        conv.emit(event_from_proposal(data))
    return "已向用户出示确认框，等用户点确认或先不做。"


def propose_revise(conv, arguments: dict) -> str:
    """改需求：能改就出确认框，施工中只记下，这一轮结束后再问。"""
    text = str((arguments or {}).get("text") or "").strip()
    if not text:
        return "请写上要改什么"
    card_id = ""
    index_path = Path(conv.project) / ".collab" / "行政对话" / "索引.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    entry = index.get(conv.id) if isinstance(index, dict) else None
    if isinstance(entry, dict):
        card_id = str(entry.get("card") or "")
    if not card_id:
        return "没有这个工具"
    phase = card_phase(conv.project, card_id)
    if "revise" in (phase.get("actions") or []):
        return _show_revise_box(conv, card_id, text)
    if phase.get("code") in ("done", "dropped"):
        return "这张卡已经结束了，不能再改。告诉用户：新要求请回主对话提一张新卡。"
    _append_pending(conv.project, card_id, text)
    return "已记下，这一轮结束后给用户确认"


def show_saved_revise(conv, card_id: str, text: str, pending: int) -> None:
    _show_revise_box(conv, card_id, text, pending)


def _save_profile(project: str, text: str) -> None:
    if not _profile_is_free(project):
        raise RuntimeError("项目里已经有项目说明了，这次没有覆盖。")
    path = profile_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_suffix(".tmp")
    spare.write_text(text, encoding="utf-8")
    spare.replace(path)


def _append_proposal_line(conv, pid: str) -> None:
    folder = Path(conv.project) / ".collab" / "行政对话"
    folder.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"role": "proposal", "id": pid, "time": time.time()}, ensure_ascii=False)
    with (folder / f"{conv.id}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def decide(project: str, conversation: str, pid: str, decision: str) -> dict:
    """用户确认或取消。不调用模型。重复确认返回同一张卡。"""
    if decision not in ("confirm", "cancel"):
        raise LookupError("只能确认或先不做")
    with _lock:
        data = load_proposal(project, pid)
        if data is None or data.get("conversation") != conversation:
            raise LookupError("没有这个提议")
        if data.get("state") != "open":
            if decision == "confirm" and data.get("state") == "confirmed":
                if data.get("kind") in ("team", "profile"):
                    return {"ok": True, "card": "", "state": "confirmed", "repeat": True, "kind": data["kind"]}
                if data.get("kind") in _BLOCK_KINDS:
                    return _block_result(data, repeat=True)
                if data.get("card"):
                    return {"ok": True, "card": data["card"], "state": "confirmed", "repeat": True, "kind": "task"}
            raise RuntimeError("这个提议已经处理过了")
        if decision == "cancel":
            data["state"] = "cancelled"
            _save(project, data)
            if data.get("kind") == "revise":
                _take_pending(project, str(data.get("card") or ""), int(data.get("pending") or 0))
            return {"ok": True, "card": data.get("card") or "", "state": "cancelled", "repeat": False,
                    "kind": data.get("kind") or "task", "reply": "好的，先不改。"}
        kind = data.get("kind") or "task"
        if kind == "profile":
            _save_profile(project, str(data.get("profile") or ""))
            data["state"] = "confirmed"
            _save(project, data)
            return {"ok": True, "card": "", "state": "confirmed", "repeat": False, "kind": "profile"}
        if kind == "revise":
            card_id = str(data.get("card") or "")
            if decision == "cancel":
                data["state"] = "cancelled"
                _save(project, data)
                _take_pending(project, card_id, int(data.get("pending") or 0))
                return {"ok": True, "card": card_id, "state": "cancelled", "repeat": False,
                        "kind": "revise", "reply": "好的，先不改。"}
            phase_now = card_phase(project, card_id)
            planish = phase_now.get("code") in ("plan_ready", "plan_waiting", "plan_failed", "planning")
            reply = "已让 Codex 重写方案。" if planish else "已交给开发，做完再告诉你。"
            try:
                apply_revise(project, card_id, str(data.get("text") or ""))
            except ReviseNotStarted as exc:
                reply = str(exc)
            data["state"] = "confirmed"
            _save(project, data)
            _take_pending(project, card_id, int(data.get("pending") or 0))
            return {"ok": True, "card": card_id, "state": "confirmed", "repeat": False,
                    "kind": "revise", "reply": reply}
        if kind == "team":
            role = str(data.get("role") or "").strip()
            tool = str(data.get("tool") or "").strip()
            if role not in _TEAM_ROLES:
                raise RuntimeError("这个提议的角色不对")
            allowed = _TEAM_REVIEWER_TOOLS if role == "reviewer" else _TEAM_TOOLS
            if tool not in allowed:
                raise RuntimeError("这个提议的工具不对")
            try:
                _apply_team_change(project, role, tool)
            except ConfigError as exc:
                raise RuntimeError(str(exc)) from exc
            data["state"] = "confirmed"
            _save(project, data)
            return {"ok": True, "card": "", "state": "confirmed", "repeat": False, "kind": "team"}
        if kind in _BLOCK_KINDS:  # 真正去装、去检测、存进基础层是本机服务的事（55 号单）
            data["state"] = "confirmed"
            data["decided"] = time.time()
            _save(project, data)
            return _block_result(data, repeat=False)
        try:
            card = new_card(project, data["title"], data["do"], data["not_do"], data["done_when"],
                            conversation=conversation)
        except TaskError:
            raise
        data["state"] = "confirmed"
        data["card"] = card
        _save(project, data)
        try:
            launch(project, "plan", card, ["plan", project, card])
        except TaskBusy:
            pass
        return {"ok": True, "card": card, "state": "confirmed", "repeat": False, "kind": "task"}


def event_from_proposal(data: dict) -> dict:
    kind = data.get("kind") or "task"
    base = {
        "type": "proposal", "id": data["id"], "kind": kind,
        "state": data.get("state") or "open", "card": data.get("card") or "",
    }
    if kind == "profile":
        base["profile"] = data.get("profile") or ""
    elif kind == "revise":
        base["text"] = data.get("text") or ""
        base["card"] = data.get("card") or ""
    elif kind == "team":
        base["role"] = data.get("role") or ""
        base["tool"] = data.get("tool") or ""
        if "before" in data:
            base["before"] = data["before"]
    elif kind in _BLOCK_KINDS:
        for key in ("install", "tool", "label", "command", "source", "needs", "minutes", "block", "name",
                    "verified", "preview", "replaces", "provider", "model", "category", "secrets", "domain",
                    "danger", "new", "facts", "found_by", "key_account", "official", "maker", "risk"):
            if key in data:
                base[key] = data[key]
    else:
        base["title"] = data.get("title") or ""
        base["do"] = data.get("do") or ""
        base["not_do"] = data.get("not_do") or ""
        base["done_when"] = data.get("done_when") or ""
    return base


def _collab(project: str) -> Path:
    return Path(project) / ".collab"


def _record_path(project: str, kind: str) -> Path:
    folder = _collab(project) / "后台"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{kind}.json"


class StopWrongCard(Exception):
    """后台记录不是这张卡：不能从这里停。"""


_stop_lock = threading.Lock()
_stopping = set()


def _read_backend_raw(project: str, kind: str) -> Optional[Dict[str, Any]]:
    path = _record_path(project, kind)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def stop_construction(project: str, card_id: str) -> None:
    """只停这张卡。正在停时第二次直接成功。工具不响应则强停，总等待上限 8 秒。"""
    key = f"{project}\0{card_id}"
    with _stop_lock:
        if key in _stopping:
            return
        _stopping.add(key)
    try:
        rec = _read_backend_raw(project, "run")
        if rec is None or str(rec.get("card") or "") != str(card_id):
            raise StopWrongCard("这张卡不是从这里开工的，请到终端里停")
        try:
            pid = int(rec["pid"])
        except (TypeError, ValueError, KeyError):
            return
        started = rec.get("started")
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return
        deadline = time.time() + 8
        while time.time() < deadline:
            if not process_is_alive(pid, started):
                break
            time.sleep(0.05)
        if process_is_alive(pid, started):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        kill_deadline = time.time() + 2
        while time.time() < kill_deadline and process_is_alive(pid, started):
            time.sleep(0.05)
    finally:
        with _stop_lock:
            _stopping.discard(key)


def running(project: str, kind: str) -> Optional[Dict[str, Any]]:
    path = _record_path(project, kind)
    data = _read_backend_raw(project, kind)
    if data is None:
        return None
    pid = data.get("pid")
    if pid is None:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    if not process_is_alive(pid_i, data.get("started")):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


def apply_revise(project: str, card_id: str, text: str) -> None:
    """确认框和按钮都走这里：能改才改，施工中先确认没有别的卡在跑。"""
    words = (text or "").strip()
    if not words:
        raise RuntimeError("请写上要改什么")
    phase = card_phase(project, card_id)
    if "revise" not in (phase.get("actions") or []):
        raise RuntimeError("现在不能修改")
    if phase.get("code") in ("plan_ready", "plan_waiting", "plan_failed", "planning"):
        revise_card(project, card_id, words)
        try:
            launch(project, "plan", card_id, ["plan", project, card_id])
        except TaskBusy:
            pass
        return
    if running(project, "run") is not None:
        raise RuntimeError("已有一张卡在施工，等它做完再改")
    if running(project, "merge") is not None or merge_unfinished(project):
        raise RuntimeError("另一张卡还在放进项目，等它结束再改")
    start = Path(project) / ".collab" / "cards" / card_id / "开工.json"
    command = ""
    if start.is_file():
        try:
            command = str(json.loads(start.read_text(encoding="utf-8")).get("test_command") or "").strip()
        except (OSError, ValueError, TypeError):
            command = ""
    if not command:
        raise RuntimeError("这张卡没有测试命令，没法接着改。可以点“放弃这个需求”，再提一张新卡。")
    sync_copy(project, card_id)  # 同步不成会抛错，这时什么都没改
    revise_card(project, card_id, words)
    cdir = Path(project) / ".collab" / "cards" / card_id
    try:
        launch(project, "run", card_id, ["run", project, card_id, "--test-command", command])
    except (TaskBusy, OSError) as exc:
        flow_record(cdir, "开工失败", src="程序", dst="你", summary="这次没能开工", text=str(exc))
        raise ReviseNotStarted("要求已经记下，但这次没能开工，可以点“接着跑”。") from exc


def launch(project: str, kind: str, card_id: str, args: list) -> int:
    path = _record_path(project, kind)
    lock_path = path.with_suffix(".lock")
    log_path = _collab(project) / "cards" / card_id / "后台输出.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = None
    tmp_path = path.with_suffix(".json.tmp")
    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        if running(project, kind) is not None:
            raise TaskBusy("已有同类任务在跑")
        try:
            env = os.environ.copy()
            env.pop("SUPERAGENT_CHAT_TOKEN", None)
            log = open(log_path, "a", encoding="utf-8", buffering=1)
            proc = subprocess.Popen(
                [sys.executable, "-B", str(COLLAB_PACKAGE), *args],
                cwd=str(project),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
            log.close()
            started = process_start(proc.pid)
            payload = json.dumps(
                {"card": card_id, "pid": proc.pid, "time": time.time(), "started": started},
                ensure_ascii=False,
            )
            tmp_path.write_text(payload + "\n", encoding="utf-8")
            os.replace(str(tmp_path), str(path))
            try:
                cdir = _collab(project) / "cards" / card_id
                if kind == "plan":
                    flow_record(cdir, "写方案", src="程序", dst="Codex",
                                summary="开始写方案", pid=proc.pid, started=started)
                elif kind == "run":
                    flow_record(cdir, "开工", src="程序", dst="程序",
                                summary="开始施工", pid=proc.pid, started=started)
                elif kind == "merge":
                    flow_record(cdir, "合并开始", src="程序", dst="程序",
                                summary="开始放进项目", pid=proc.pid, started=started)
            except OSError:
                pass

            def _wait():
                proc.wait()
                try:
                    if path.is_file():
                        cur = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(cur, dict) and int(cur.get("pid") or 0) == proc.pid:
                            path.unlink()
                except (OSError, ValueError):
                    pass

            threading.Thread(target=_wait, daemon=True).start()
            return proc.pid
        except OSError:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            try:
                path.unlink()
            except OSError:
                pass
            if proc:
                try:
                    proc.kill()
                except OSError:
                    pass
            raise


_REPORT_CODES = frozenset({
    "plan_ready", "plan_failed", "review", "blocked", "interrupted", "start_failed",
    "done", "merge_failed",
})


_card_title = card_title


def _conversation_from_source(cdir: Path) -> str:
    path = cdir / "来源.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("conversation") or "").strip()


def _load_reported_code(cdir: Path) -> Optional[str]:
    path = cdir / "已汇报.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    code = data.get("code")
    return str(code) if isinstance(code, str) else None


def _save_reported_code(cdir: Path, code: str) -> None:
    path = cdir / "已汇报.json"
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps({"code": code}, ensure_ascii=False), encoding="utf-8")
    spare.replace(path)


def _flow_events(cdir: Path) -> list:
    from collab.flow import read_flow
    return read_flow(cdir)


def _user_stop_after_last_run(events: list) -> bool:
    runs = [e for e in events if e.get("kind") == "开跑"]
    if not runs:
        return False
    last = runs[-1]
    try:
        pid_i = int(last.get("pid"))
    except (TypeError, ValueError):
        return False
    if process_is_alive(pid_i):
        return False
    last_seq = int(last.get("seq") or 0)
    for e in events:
        if e.get("kind") != "停下":
            continue
        if int(e.get("seq") or 0) <= last_seq:
            continue
        if e.get("src") == "你":
            return True
    return False


def _report_text(code: str, title: str, phase: dict) -> str:
    reason = str(phase.get("detail") or "").strip().rstrip("。")
    if code == "plan_ready":
        return f"「{title}」的方案写好了，点左边的卡片看方案，批准后才开工。"
    if code == "plan_failed":
        base = f"「{title}」的方案没写成"
        if reason:
            base += f"：{reason}"
        else:
            base += "：原因不明"
        return base + "。可以点卡片重写。"
    if code == "review":
        return f"「{title}」做完了，等你确认。"
    if code == "blocked":
        base = f"「{title}」卡住了"
        if reason:
            base += f"：{reason}"
        else:
            base += "：原因不明"
        return base + "。"
    if code == "interrupted":
        return f"「{title}」中断了，可以点卡片接着跑。"
    if code == "start_failed":
        return f"「{title}」开工失败，可以点卡片接着跑，或者放弃这个需求。"
    if code == "done":
        return f"「{title}」已放进项目。"
    if code == "merge_failed":
        base = f"「{title}」没能放进项目"
        if reason:
            base += f"：{reason}"
        return base + "。"
    return ""


class TaskWatcher:
    """项目级巡视：单线程每 2 秒；业务集中在 tick。"""

    def __init__(self, desk) -> None:
        self._desk = desk
        self._tick_lock = threading.Lock()
        self._started = set()
        self._projects_lock = threading.Lock()

    def ensure_watching(self, project: str) -> None:
        p = str(project)
        if not p:
            return
        with self._projects_lock:
            if p in self._started:
                return
            self._started.add(p)

        def loop():
            while True:
                try:
                    self.tick(p)
                except Exception:
                    pass
                time.sleep(2)

        threading.Thread(target=loop, daemon=True).start()

    def tick(self, project: str) -> None:
        with self._tick_lock:
            self._tick_impl(project)

    def _tick_impl(self, project: str) -> None:
        root = Path(project) / ".collab" / "cards"
        if not root.is_dir():
            return
        for cdir in sorted(root.iterdir(), key=lambda p: p.name):
            try:
                self._tick_card(project, cdir)
            except Exception:
                continue

    def _tick_card(self, project: str, cdir: Path) -> None:
        if not cdir.is_dir():
            return
        state_path = cdir / "状态.json"
        if not state_path.is_file():
            return
        card_id = cdir.name
        has_source = (cdir / "来源.json").is_file()
        phase = card_phase(project, card_id)
        code = str(phase.get("code") or "")
        self._play_card_news(project, cdir, card_id)
        self._offer_saved_revise(project, cdir, card_id, phase)

        if has_source and code == "plan_waiting" and running(project, "plan") is None:
            try:
                launch(project, "plan", card_id, ["plan", project, card_id])
            except TaskBusy:
                pass

        prev = _load_reported_code(cdir)
        if prev is None:
            if has_source:
                _save_reported_code(cdir, code)
            return

        if code == prev:
            return

        if not has_source:
            _save_reported_code(cdir, code)
            return

        if code not in _REPORT_CODES:
            _save_reported_code(cdir, code)
            return

        if code == "interrupted" and _user_stop_after_last_run(_flow_events(cdir)):
            _save_reported_code(cdir, code)
            return

        conversation = _conversation_from_source(cdir)
        title = _card_title(cdir, card_id)
        text = _report_text(code, title, phase)
        if text and conversation:
            self._desk.notify(project, conversation, text)
        _save_reported_code(cdir, code)

    def _play_card_news(self, project: str, cdir: Path, card_id: str) -> None:
        pointer = cdir / "已播报.json"
        if not pointer.is_file():
            return
        try:
            last = int(json.loads(pointer.read_text(encoding="utf-8")).get("seq") or 0)
        except (OSError, ValueError, TypeError):
            last = 0
        news = {"方案", "方案失败", "开跑", "判定", "转人工", "结束", "停下", "合并失败", "合并"}
        cid = card_conversation_id(card_id)
        newest = last
        for ev in _flow_events(cdir):
            seq = int(ev.get("seq") or 0)
            if seq <= last:
                continue
            newest = max(newest, seq)
            summary = str(ev.get("summary") or "").strip()
            if ev.get("kind") in news and summary:
                self._desk.notify(project, cid, summary)
        if newest != last:
            spare = pointer.with_suffix(".tmp")
            spare.write_text(json.dumps({"seq": newest}, ensure_ascii=False), encoding="utf-8")
            spare.replace(pointer)

    def _offer_saved_revise(self, project: str, cdir: Path, card_id: str, phase: dict) -> None:
        path = cdir / "待转达.jsonl"
        if not path.is_file():
            return
        texts = []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            words = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
            if words:
                texts.append(words)
        if not texts:
            return
        cid = card_conversation_id(card_id)
        if phase.get("code") in ("done", "dropped"):
            # 卡片结束了，这些话转不出去，也不能悄悄丢掉
            self._desk.notify(project, cid, "这些话没有转交，因为这张卡已经结束了：" + "；".join(texts)
                              + "。还需要的话，请回主对话提一张新卡。")
            _take_pending(project, card_id, len(texts))
            return
        if "revise" not in (phase.get("actions") or []):
            return
        conv_file = Path(project) / ".collab" / "行政对话" / f"{cid}.jsonl"
        if not conv_file.is_file():
            return
        for old in _open_in_conversation(project, cid):
            if old.get("kind") == "revise" and old.get("state") == "open":
                return
        conv = self._desk._conversation(project, cid, create=False)  # noqa: SLF001
        if conv is None:
            return
        show_saved_revise(conv, card_id, "\n".join(texts), len(texts))
