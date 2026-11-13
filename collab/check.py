"""检测连通：派活前确认团队里每个人都真能干活。

做法：先花几秒查“装没装”（找不到命令就直接标红，不白等）；装了的，让它在一个临时文件夹里
建一个文件，**建出来才算通**——这样能抓住“装了却干不了活”的情况（没登录、额度用完、模型名不对、
没有写文件的权限）。同一家工具担任几个岗位只测一次；几家同时测。临时文件夹用完即删。
"""
from __future__ import annotations

import functools
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from collab.agents import ask, watched_runner
from collab.config import load_models, load_role_models, load_team

COMMANDS = {"codex": "codex", "cursor": "agent", "agy": "agy", "qwen": "codex"}   # Cursor 的命令行叫 agent；千问用 Codex 的壳
TOOL_NAMES = {"codex": "Codex", "cursor": "Cursor", "agy": "agy", "qwen": "千问", "none": "不复查"}
ROLE_ORDER = ("dispatcher", "tester", "developer", "reviewer")
ROLE_NAMES = {"dispatcher": "调度员", "tester": "写测试", "developer": "写代码", "reviewer": "复查"}
PROBE_FILE = "连通检测.txt"
PROBE_PROMPT = f"在当前文件夹里新建一个文件 {PROBE_FILE}，内容只写 OK。只做这一件事，不要做别的。"
DEFAULT_TIMEOUT = 120


def _command_of(tool: str) -> str:
    if tool.startswith("custom:"):
        return tool[len("custom:"):].split()[0]
    return COMMANDS[tool]


def _name_of(tool) -> str:
    if tool and tool.startswith("custom:"):
        return _command_of(tool)
    return TOOL_NAMES.get(tool or "", tool or "")


def explain(output: str, tool: str):
    """把失败的输出翻成大白话：(原因, 怎么办)。"""
    raw = output or ""
    text = raw.lower()
    cmd = _command_of(tool)
    if raw.startswith("超时"):
        return "超时：规定时间内没做完", "网络或工具可能卡住了，稍后再检测一次"
    if raw.startswith("看门人"):
        return "长时间没有任何动静，被停下了", "稍后再检测一次"
    if "invalid request parameters" in text or "invalidparameter" in text:
        # 模型不认 Codex 发的请求（千问接的模型常见）；排在“没登录”前面：输出里常混着别的插件的登录提示（11-13 误报过）
        return "这个模型不接受这样的请求", "换这一家的另一个模型再检测；仍不行就换一家"
    if any(k in text for k in ("not logged", "log in", "login", "unauthorized", "authenticat", "sign in", "401")):
        return "没登录", f"在终端里运行一次 {cmd}，按提示登录后再检测一次"
    if any(k in text for k in ("quota", "rate limit", "usage limit", "429", "insufficient", "out of credit")):
        return "额度用完或被限流", "等额度恢复后再检测一次，或这个岗位换一家"
    if "model" in text and any(k in text for k in ("not found", "invalid", "unknown", "does not exist", "not supported")):
        return "模型名不对", "在项目的 .collab/配置.json 里改成这家支持的模型名"
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    tail = lines[-1][:120] if lines else "没有输出"
    return "命令跑完了，但没建出文件", f"看看这家工具在自动模式下能不能写文件。它最后说：{tail}"


def check_tool(tool: str, models: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """检测一家工具。返回 {ok, status(ok/missing/fail), reason, how, seconds}。"""
    cmd = _command_of(tool)
    if tool == "qwen":  # 先看密钥：没密钥时不管装没装 Codex 都用不了
        from collab.config import QWEN_KEY_FILE, qwen_key
        if not qwen_key():
            return {"ok": False, "status": "missing", "reason": "千问还没配密钥",
                    "how": f"把千问 Token Plan 的密钥放进本机文件 {QWEN_KEY_FILE}（只有自己能读），或让 Claude 帮你放",
                    "seconds": 0}
    if shutil.which(cmd) is None:
        return {"ok": False, "status": "missing", "reason": f"没装：电脑上找不到 {cmd} 这个命令",
                "how": f"先安装 {_name_of(tool)}，并在终端里运行一次 {cmd} 登录", "seconds": 0}
    if tool.startswith("custom:"):
        return {"ok": True, "status": "ok", "reason": "已找到这个命令（自定义调度员只能检查装没装）", "how": "",
                "seconds": 0}
    folder = Path(tempfile.mkdtemp(prefix="超级助手检测-"))
    start = time.time()
    try:
        runner = functools.partial(watched_runner, poll=0.5, quiet_limit=timeout + 5)
        result = ask(tool, PROBE_PROMPT, folder, expect_file=PROBE_FILE, timeout=timeout, runner=runner,
                     models=models)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    seconds = round(time.time() - start)
    if result.ok:
        return {"ok": True, "status": "ok", "reason": f"通了 · {seconds} 秒", "how": "", "seconds": seconds}
    reason, how = explain(result.output, tool)
    return {"ok": False, "status": "fail", "reason": reason, "how": how, "seconds": seconds}


def check_team(project_dir, team=None, timeout: float = DEFAULT_TIMEOUT, on_result=None,
               roles=None, models=None, role_models=None) -> list:
    """检测整个团队，按 调度员、写测试、写代码、复查 的顺序返回；每出一个结果就回调一次 on_result。
    roles：只测这几个岗位（点某一行的“检测生效”时用）。
    role_models：页面上每个岗位此刻选的模型（还没保存的也算）；同一家工具、同样设置的几个岗位只测一次。"""
    team = team or load_team(project_dir)
    given_models = models  # 调用方传没传整家模型（下面会被换成整份设置，判断要用原值，复查第 2 轮）
    effective_models = load_models(project_dir)
    if isinstance(models, dict):
        effective_models.update({k: str(v).strip() for k, v in models.items() if k in effective_models and str(v).strip()})
    models = effective_models
    lock = threading.Lock()
    results = {}

    def report(role: str, tool, body: dict, shared_with=None):
        item = dict(body, role=role, role_name=ROLE_NAMES[role], tool=tool, tool_name=_name_of(tool),
                    shared_with=shared_with)
        with lock:
            results[role] = item
        if on_result:
            on_result(item)

    saved_roles = {}
    try:
        saved_roles = load_role_models(project_dir, team)
    except Exception:  # noqa: BLE001 — 配置读不出时用整体设置
        saved_roles = {}
    # 只传了整家模型（旧接口）时，就按传进来的测，不拿存着的岗位设置盖掉（复查第 1 轮）
    picked = dict(saved_roles) if (role_models is not None or given_models is None) else {}
    if isinstance(role_models, dict):
        picked.update({r: v for r, v in role_models.items() if isinstance(v, dict)})

    def models_of(role: str, tool: str) -> dict:
        mine = dict(models)
        item = picked.get(role) or {}
        if item.get("tool") == tool and str(item.get("model") or "").strip():
            mine[tool] = str(item["model"]).strip()
            if tool == "codex" and item.get("effort"):
                mine["codex_effort"] = str(item["effort"]).strip()
        return mine

    owners = {}  # (工具, 模型设置) -> 用它的岗位（同样设置只测一次）
    settings = {}
    for role in ROLE_ORDER:
        if roles is not None and role not in roles:
            continue
        tool = team.get(role)
        if role == "dispatcher" and not tool:
            report(role, None, {"ok": False, "status": "unset", "reason": "还没选调度员", "how": "先选一位调度员",
                                "seconds": 0})
        elif role == "reviewer" and tool == "none":
            report(role, tool, {"ok": True, "status": "skipped", "reason": "不复查，不用检测", "how": "", "seconds": 0})
        else:
            mine = models_of(role, tool) if tool in COMMANDS else models
            key = (tool, mine.get(tool), mine.get("codex_effort") if tool == "codex" else "")
            owners.setdefault(key, []).append(role)
            settings[key] = mine

    def run(key):
        tool = key[0]
        body = check_tool(tool, settings[key], timeout)
        first, *rest = owners[key]
        report(first, tool, body)
        for role in rest:
            report(role, tool, body, shared_with=first)

    if owners:
        with ThreadPoolExecutor(max_workers=len(owners)) as pool:
            list(pool.map(run, owners))
    return [results[r] for r in ROLE_ORDER if r in results]
