"""超级助手的本机数据服务：只监听 127.0.0.1。

- 右边流转面板：只读项目的协作记录，不写任何文件；
- 左边聊天视图：/api/chat/* 连着项目的调度员（发消息、允许/拒绝、停止、取事件、看图片）。
  这些地址必须带上窗口启动时给的一次性口令（请求头 X-Chat-Token 或参数 token），
  免得浏览器里别的网页借这台电脑往调度员那里塞话。

用法：python3 desktop/server.py [--port 0] [--watch-parent] [--token 口令]
启动后第一行打印 PORT=端口号，桌面窗口读这一行来打开页面。
--watch-parent：启动它的程序（桌面窗口）退出后自己也退出，不留在后台。
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import re
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # 直接用 python3 desktop/server.py 启动时也能找到 collab
    sys.path.insert(0, str(ROOT))

from collab.check import DEFAULT_TIMEOUT as CHECK_TIMEOUT, check_team  # noqa: E402
from collab.cli import cmd_approve, cmd_confirm_profile  # noqa: E402
from collab.dispatch import uncommitted_count  # noqa: E402
from collab.config import (  # noqa: E402
    DEFAULT_MODELS,
    DEFAULT_TEAM,
    ConfigError,
    load_clerk,
    load_model_options,
    load_models,
    load_role_models,
    load_team,
    save_clerk,
    save_models,
    save_role_models,
    clean_role_models,
    clean_models,
    save_team,
)
from collab.flow import read_flow, record as flow_record  # noqa: E402
from collab.ledger import ledger_add  # noqa: E402
from collab.merge import merge_unfinished, remove_copy, undo_interrupted  # noqa: E402
from collab.phase import card_phase  # noqa: E402
from collab.scope import read_allowed  # noqa: E402
from collab.state import Card, InvalidTransition  # noqa: E402
from collab.workspace import has_blanks, profile_path, render_board  # noqa: E402
from desktop.chat import ChatHub  # noqa: E402
from collab import abilities, abilities_run, blocks, blocks_check, blocks_env, blocks_install
from collab.abilities import AbilityError
from collab.blocks import BlockError
from desktop.clerk import blocks_tools, tasks as clerk_tasks
from desktop.clerk.client import DEFAULT_BASE_URL, DEFAULT_MODEL, ModelClient, ModelError  # noqa: E402
from desktop.clerk.desk import ClerkDesk  # noqa: E402
from desktop.clerk.providers import PROVIDERS, account_for, find as find_provider  # noqa: E402
from desktop.clerk.tasks import (  # noqa: E402
    StopWrongCard, TaskBusy, TaskWatcher, apply_revise, launch, running, stop_construction,
)
from desktop.preview import PreviewHub  # noqa: E402
from collab.tasks import TaskError  # noqa: E402
from desktop.shell_path import adopt_terminal_path, merged_path, terminal_path  # noqa: E402
from desktop.view import card_view, event_text, list_cards, set_archived  # noqa: E402

_TOOLS = [{"value": "codex", "label": "Codex"}, {"value": "cursor", "label": "Cursor"}, {"value": "agy", "label": "agy"}]
_TEAM_OPTIONS = {"dispatcher": _TOOLS, "worker": _TOOLS,
                 "reviewer": _TOOLS + [{"value": "none", "label": "不复查"}]}

STATIC = Path(__file__).resolve().parent / "static"
_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".webp": "image/webp"}
_MAX_BODY = 1024 * 1024  # 聊天消息最多 1MB，够用了
_CARD_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_BLOCK_KINDS = clerk_tasks._BLOCK_KINDS  # 只在 desktop/clerk/tasks.py 定义一次
_JOB_LIMIT = 100         # 积木库做完的后台任务最多留这么多
def _blocks_connected(result: dict) -> bool:
    """检测通过、只是额度不足、或拿到了模型名单只是试的模型没通（用户 2026-11-11 选 A）（用户 2026-11-09 定也算接上了）"""
    return bool(result.get("ok")) or result.get("status") in ("quota", "listed")


# 和 Mac 窗口 BlocksWindow.isValidAccount 一样；tavily 和 cli:编号:名字 是沙盒接入 64 号单加的
_SAVED_ACCOUNT = re.compile(r"tencent|bailian|other:[a-z0-9.-]{1,253}|tavily|cli:[a-z0-9][a-z0-9-]{0,39}:[A-Z][A-Z0-9_]{1,63}")
_TERMINAL_TTL = 60       # 终端路径一分钟内只读一次


class Handler(BaseHTTPRequestHandler):
    server_version = "SuperAgentPanel/0.2"

    def log_message(self, *args):  # 不往终端刷访问日志
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _start_test_command(self, project: str, card_id: str) -> str:
        start_path = Path(project) / ".collab" / "cards" / card_id / "开工.json"
        if not start_path.is_file():
            return ""
        try:
            return str(json.loads(start_path.read_text(encoding="utf-8")).get("test_command") or "")
        except (OSError, ValueError, TypeError):
            return ""

    def _plan_payload(self, project: str, card_id: str) -> dict:
        project_p = Path(project)
        cdir = project_p / ".collab" / "cards" / card_id
        plan_path = cdir / "方案.md"
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
        need = (cdir / "需求卡.md").read_text(encoding="utf-8") if (cdir / "需求卡.md").is_file() else ""
        allowed = read_allowed(need) or []
        start_path = cdir / "开工.json"
        test_cmd = ""
        if start_path.is_file():
            try:
                test_cmd = str(json.loads(start_path.read_text(encoding="utf-8")).get("test_command") or "")
            except (OSError, ValueError, TypeError):
                test_cmd = ""
        new_profile = False
        for ev in reversed(read_flow(cdir)):
            if ev.get("kind") == "方案":
                new_profile = bool(ev.get("new_profile"))
                break
        prof_file = profile_path(project)
        profile_text = ""
        if new_profile and prof_file.is_file():
            profile_text = prof_file.read_text(encoding="utf-8")
        return {
            "plan": plan_text, "allowed": allowed, "test_command": test_cmd,
            "new_profile": new_profile, "profile_text": profile_text,
        }

    def _card_action_error(self, text: str) -> None:
        self._json({"error": text}, 409)

    def _do_card_action(self, project: str, card_id: str, action: str, text: str = "") -> None:
        preview = getattr(self.server, "preview", None)
        if preview is not None:
            preview.stop()
        phase = card_phase(project, card_id)
        actions = phase.get("actions") or []
        cdir = Path(project) / ".collab" / "cards" / card_id
        state_path = cdir / "状态.json"
        if not state_path.is_file():
            self._card_action_error("找不到这张卡片。")
            return
        card = Card.load(state_path)

        if action == "accept":
            if running(project, "merge") is not None:
                self._card_action_error("另一张卡正在放进项目，等它结束再试")
                return
            if action not in actions:
                self._card_action_error("现在不能做这个操作，请先看我说的阶段。")
                return
            try:
                launch(project, "merge", card_id, ["merge", project, card_id])
            except (TaskBusy, OSError):
                self._card_action_error("另一张卡正在放进项目，等它结束再试")
                return
            self._json({"ok": True})
            return

        if action == "revise":
            try:
                apply_revise(project, card_id, text)
            except RuntimeError as exc:
                self._card_action_error(str(exc))
                return
            self._json({"ok": True})
            return

        if action not in actions:
            self._card_action_error("现在不能做这个操作，请先看我说的阶段。")
            return

        if action == "accept_issues":
            # 用户接受复查剩下的问题（流程修理 ㉒）：卡片进入待确认，照常点“确认没问题，放进项目”才放进去
            issues = phase.get("issues") or phase.get("detail") or ""  # 复查员剩下的全部问题，不是截短的摘要
            try:
                card.transition("待验收")
            except InvalidTransition:
                self._card_action_error("这张卡现在不能这样做。")
                return
            card.save(state_path)
            flow_record(cdir, "接受", src="你", dst="程序", summary="接受剩下的问题，进入待确认", text=issues)
            flow_record(cdir, "结束", src="程序", dst="你", state="待验收",
                        summary="你接受了复查剩下的问题，等你确认放进项目")
            render_board(Path(project) / ".collab")
            ledger_add(project, f"{card_id}：接受复查剩下的问题，进入待确认")
            self._json({"ok": True})
            return

        if action == "discard":
            try:
                told = undo_interrupted(project, card_id)
                card.transition("已放弃")
            except InvalidTransition:
                self._card_action_error("这张卡现在不能标记为先不做。")
                return
            except RuntimeError as exc:
                self._card_action_error(str(exc))
                return
            card.save(state_path)
            flow_record(cdir, "放弃", src="你", dst="程序", summary=("先不做。" + told) if told else "先不做")
            render_board(Path(project) / ".collab")
            ledger_add(project, f"{card_id}：标记为先不做")
            remove_copy(project, card_id)
            self._json({"ok": True})
            return

        if action == "replan":
            busy = running(project, "plan")
            if busy is not None and str(busy.get("card") or "") != card_id:
                self._card_action_error("另一张卡的方案正在写，等它写完再重写")
                return
            try:
                launch(project, "plan", card_id, ["plan", project, card_id])
            except TaskBusy:
                self._card_action_error("另一张卡的方案正在写，等它写完再重写")
                return
            self._json({"ok": True})
            return

        if action == "approve":
            if running(project, "merge") is not None or merge_unfinished(project):
                self._card_action_error("另一张卡还在放进项目，等它结束再开工")
                return
            run_busy = running(project, "run")
            if run_busy is not None and str(run_busy.get("card") or "") != card_id:
                self._card_action_error("已有一张卡在施工，等它做完再开工")
                return
            prof = profile_path(project)
            if not prof.is_file():
                self._card_action_error("还没有项目说明：请先在文档里写好「项目说明」。")
                return
            settings_path = Path(project) / ".collab" / "配置.json"
            settings = {}
            if settings_path.is_file():
                try:
                    settings = json.loads(settings_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    settings = {}
            phase_data = self._plan_payload(project, card_id)
            need_confirm = bool(phase_data["new_profile"] or not settings.get("project_profile_confirmed"))
            if need_confirm:
                body = prof.read_text(encoding="utf-8")
                if has_blanks(body):
                    self._card_action_error("项目说明里还有空白：请先补全再在协作区里确认。")
                    return
                ns = argparse.Namespace(project=project)
                if cmd_confirm_profile(ns) != 0:
                    self._card_action_error("项目说明还不能确认：请先按提示补全。")
                    return
            test_cmd = self._start_test_command(project, card_id).strip()
            if not test_cmd:
                self._card_action_error("还没有施工用的测试命令：请先写好方案。")
                return
            ns2 = argparse.Namespace(project=project, card_id=card_id)
            code = cmd_approve(ns2)
            if code != 0:
                self._card_action_error("方案里还有空白：请先在卡片里补全。")
                return
            try:
                launch(project, "run", card_id, ["run", project, card_id, "--test-command", test_cmd])
            except TaskBusy:
                flow_record(cdir, "开工失败", src="程序", dst="你", summary="已有一张卡在施工，等它做完再开工")
                self._card_action_error("已有一张卡在施工，等它做完再开工")
                return
            except OSError as exc:
                flow_record(cdir, "开工失败", src="程序", dst="你",
                            summary="开工没能启动起来", text=str(exc))
                self._card_action_error("开工没能启动起来，可以点接着跑再试，或者先放弃这个需求")
                return
            self._json({"ok": True})
            return

        if action == "resume":
            test_cmd = self._start_test_command(project, card_id).strip()
            if not test_cmd:
                self._card_action_error("还没有施工用的测试命令：请先写好方案。")
                return
            run_args = ["run", project, card_id, "--test-command", test_cmd]
            if phase.get("code") == "blocked":
                run_args.append("--resume-blocked")
            try:
                launch(project, "run", card_id, run_args)
            except TaskBusy:
                flow_record(cdir, "开工失败", src="程序", dst="你", summary="已有一张卡在施工，等它做完再开工")
                self._card_action_error("已有一张卡在施工，等它做完再开工")
                return
            except OSError as exc:
                flow_record(cdir, "开工失败", src="程序", dst="你",
                            summary="开工没能启动起来", text=str(exc))
                self._card_action_error("开工没能启动起来，可以点接着跑再试，或者先放弃这个需求")
                return
            self._json({"ok": True})
            return

        if action == "stop":
            try:
                stop_construction(project, card_id)
            except StopWrongCard as exc:
                self._card_action_error(str(exc))
                return
            flow_record(cdir, "停下", src="你", dst="程序", summary="施工已停下")
            self._json({"ok": True})
            return

        self._card_action_error("看不懂这个操作。")

    def _static(self, rel: str) -> None:
        target = (STATIC / rel).resolve()
        if STATIC not in target.parents or not target.is_file():
            self._json({"error": "没有这个文件"}, 404)
            return
        self._send(200, target.read_bytes(), _TYPES.get(target.suffix, "application/octet-stream"))

    # ---------- 积木库 ----------

    def _blocks_key(self, body: dict, account: str, provider: str, base_url: str) -> str:
        """页面带了密钥用带的；没带就用 Mac 窗口从钥匙串交来的那把（60 号单），都没有给空。
        存的只在“账号和地址对得上”时才用：认得的服务商只能发到它自己的地址，“其他”按主机名分账号。
        卡片或行政设置被改过、对不上就当没存，免得点一下检测就把这家的密钥发到别处。"""
        given = str(body.get("api_key") or "").strip()
        if given:
            return given
        try:
            if not account or account_for(provider, base_url) != account:
                return ""
        except ValueError:
            return ""
        fixed = str((find_provider(provider) or {}).get("base_url") or "")
        if fixed and base_url.rstrip("/") != fixed.rstrip("/"):
            return ""
        return self.server.blocks_saved_keys.get(account, "")

    def _blocks_tool_keys(self, card: dict, body: dict) -> dict:
        """普通工具要的几把密钥：页面这次填的优先，没填的用钥匙串里存的。只认这张卡自己的账号名。"""
        given = body.get("secret_keys") if isinstance(body.get("secret_keys"), dict) else {}
        keys = {}
        for item in card.get("secrets") or []:
            account = blocks.secret_account(card["id"], item["env"])
            value = given.get(account)
            value = value.strip() if isinstance(value, str) else ""
            keys[account] = value or self.server.blocks_saved_keys.get(account, "")
        return keys

    def _blocks_search_key(self, body: dict) -> str:
        given = str(body.get("api_key") or "").strip()
        return given or self.server.blocks_saved_keys.get(blocks.SEARCH_PRESETS["tavily"]["key_account"], "")

    def _blocks_take_keys(self, body: dict) -> None:
        """Mac 窗口交来钥匙串里的整份密钥：只记在内存里，整份替换；不合格的那条丢掉，不回显。"""
        keys = body.get("keys")
        if not isinstance(keys, dict):
            self._json({"error": "格式不对"}, 400)
            return
        clean = {}
        for account, key in keys.items():
            key = key.strip() if isinstance(key, str) else ""
            if (isinstance(account, str) and _SAVED_ACCOUNT.fullmatch(account) and key and len(key) <= 500
                    and "\n" not in key and "\r" not in key):
                clean[account] = key
        self.server.blocks_saved_keys = clean
        self._json({"ok": True, "count": len(clean)})

    def _blocks_home(self) -> str:
        """积木库资料夹只由程序定，页面传的不认；第一次用到时建好资料夹本身。"""
        home = blocks.home()
        home.mkdir(parents=True, exist_ok=True)
        if str(home) not in self.server.blocks_migrated:  # 旧的“一块一个模型”变成整家一块，每个资料夹只做一次
            with self.server.blocks_lock:
                if str(home) not in self.server.blocks_migrated:
                    try:
                        blocks.migrate_api_cards(home)
                    except Exception:  # noqa: BLE001 — 合并不成不能挡住整个窗口，下次打开再试
                        pass
                    else:
                        self.server.blocks_migrated.add(str(home))
        return str(home)

    def _blocks_path(self, fresh: bool = False) -> str:
        """把终端的路径补进本进程的 PATH，返回终端路径。出提议、详情、体检、检测、安装都用这同一份，
        前后算出的完整命令才对得上。读终端要几秒，一分钟内只读一次。"""
        server = self.server
        with server.blocks_path_lock:
            now = time.monotonic()
            cached = server.blocks_terminal
            if fresh or cached is None or now - cached[0] > _TERMINAL_TTL:
                cached = server.blocks_terminal = (now, server.terminal_path() or "")
            # 脚本安装常装进 ~/.local/bin，终端里不一定有它：一并补上，装好的后面才检测得到（复查第 7 条）
            local_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
            os.environ["PATH"] = merged_path(merged_path(os.environ.get("PATH", ""), cached[1]), local_bin)
            return cached[1]

    def _blocks_client(self, provider: str, base_url: str, model: str, api_key: str):
        """积木库行政的模型连接。默认的要知道是哪家：腾讯云、百炼要关“思考”。"""
        factory = self.server.blocks_client_factory
        if factory:
            return factory(base_url, model, api_key)
        return blocks_check._default_client(base_url, model, api_key, provider)

    def _blocks_job(self, kind: str, work, **extra) -> str:
        """开一个后台任务：work(job) 的返回放进 result。做完的任务最多留 _JOB_LIMIT 个。"""
        job_id = uuid.uuid4().hex
        job = dict(kind=kind, done=False, result=None, error="", **extra)
        with self.server.blocks_lock:
            jobs = self.server.blocks_jobs
            jobs[job_id] = job
            # 检测通过（或只是额度不足）、还没放进基础层的留着，等用户点
            finished = [k for k, v in jobs.items()
                        if v["done"] and not (v.get("card") and _blocks_connected((v["result"] or {}))
                                              and not v.get("saved"))]
            dropped = set(finished[:max(0, len(jobs) - _JOB_LIMIT)])
            for k in dropped:
                del jobs[k]
            links = self.server.blocks_proposal_jobs
            for pid in [p for p, j in links.items() if j in dropped]:
                del links[pid]

        def run():
            try:
                job["result"] = work(job)
            except BlockError as exc:
                job["error"] = str(exc)
            except Exception:  # noqa: BLE001 — 原文可能带着密钥或路径，不往页面送
                job["error"] = "后台任务出错了，请再试一次"
            finally:
                job["done"] = True
        threading.Thread(target=run, daemon=True).start()
        return job_id

    def _blocks_check_work(self, home: str, card: dict, approved=None, api_key: str = "", keys=None,
                           auto_save: bool = False, model=None):
        def work(job):
            terminal = self._blocks_path()
            if card["kind"] == "llm_api":
                return blocks_check.check_api(card, api_key, root=home, whole=True,
                                              client_factory=self.server.blocks_client_factory,
                                              models_fetcher=self.server.blocks_models_fetcher)
            if card["kind"] == "search":
                return blocks_check.check_search(card, api_key, root=home)
            if not blocks.is_verified(card) and approved != blocks_check.preview(card):
                return {"ok": False, "status": "changed", "reason": "要跑的命令和你确认时看到的不一样",
                        "how": "跟管家说重新提议，确认新的完整命令"}
            result = blocks_check.check_cli(card, root=home, approved=approved, terminal_path=terminal, keys=keys, model=model)
            if auto_save and _blocks_connected(result):  # “待接入”点一下：通过就直接放进基础层（用户 2026-11-09 选 A）
                with self.server.blocks_lock:
                    if not blocks._card_path(card["id"], home).is_file():
                        blocks.save_card(card, home)
                    job["saved"] = True
            return result
        return work

    def _blocks_web_install_work(self, home: str, spec: dict):
        def work(job):
            server = self.server
            try:
                self._blocks_path()
                result = blocks_install.run_web_install(spec, root=home)
            finally:
                job["tail"] = blocks_install.output_tail()
                with server.blocks_lock:
                    server.blocks_installing = None
            if result.get("ok"):
                self._blocks_path(fresh=True)
            return result
        return work

    def _blocks_install_work(self, home: str, install_id: str):
        def work(job):
            server = self.server
            try:
                self._blocks_path()
                result = blocks_install.run_install(install_id, root=home)  # 找命令用补过终端路径的 PATH，不传 prefix
            finally:
                job["tail"] = blocks_install.output_tail()  # 这次安装自己的输出，之后别的安装不会换掉它
                with server.blocks_lock:
                    server.blocks_installing = None
            if result.get("ok"):
                self._blocks_path(fresh=True)  # 刚装好的工具可能在新的文件夹里
            return result
        return work

    def _blocks_pending(self, home: str, items: list, broken: list) -> list:
        """待接入：上一次体检认出、装好了的预置工具（Codex、Cursor、agy），基础层里还没有的。"""
        taken = {i["id"] for i in items} | {b["id"] for b in broken}
        last = blocks.last_record("env", home, type="env") or {}
        found = {i.get("name") for i in last.get("items") or [] if isinstance(i, dict) and i.get("status") == "有"}
        pending = []
        for name in blocks.BUILTIN:
            if name in found and name not in taken:
                card = blocks.builtin_card(name)
                job_id = self.server.blocks_pending_jobs.get(name, "")
                pending.append({"id": name, "name": card["name"], "category": "agent", "job": job_id})
        return pending

    def _blocks_get(self, path: str, query: dict) -> None:
        try:
            self._blocks_get_inner(path, query)
        except (BlockError, ConfigError) as exc:
            self._json({"error": str(exc)}, 409)
        except OSError:
            self._json({"error": "积木库资料夹读写出错，请检查磁盘和权限"}, 500)

    def _blocks_get_inner(self, path: str, query: dict) -> None:
        home = self._blocks_home()
        if path == "/api/blocks/state":
            setting = load_clerk(home)
            account = ""
            if setting:
                try:
                    account = account_for(setting["provider"], setting.get("base_url", ""))
                except (KeyError, ValueError):
                    account = ""
            self._json({"setting": setting, "key_account": account, "connected": self.server.blocks_clerk is not None,
                        "saved_accounts": sorted(self.server.blocks_saved_keys),
                        "installing": blocks_install.installing() or bool(self.server.blocks_installing),
                        "home": home,
                        "providers": [{"id": p["id"], "name": p["name"], "base_url": p.get("base_url", ""), "models": p["models"]} for p in PROVIDERS]})
        elif path == "/api/blocks/list":
            items, broken = [], []
            for card in blocks.list_cards(home):
                if "problem" in card:
                    broken.append({"id": card["id"], "problem": card["problem"]})
                    continue
                recs = blocks.records(card["id"], home)
                last_check_idx = -1
                last = None
                for idx, r in enumerate(recs):
                    if r.get("type") == "check":
                        last_check_idx = idx
                        last = r
                if last is None:
                    status = "never"
                elif last.get("ok"):
                    status = "ok"
                elif any(r.get("type") == "model_check" and r.get("ok") for r in recs[last_check_idx + 1:]):
                    status = "ok"
                elif last.get("status") in ("quota", "listed"):
                    status = last.get("status")
                else:
                    status = "fail"

                models_list = (last or {}).get("models") or []
                models_count = len(models_list) if (card["kind"] == "llm_api" or (card["kind"] == "cli" and card.get("role") != "tool")) else 0
                items.append({"id": card["id"], "name": card["name"], "kind": card["kind"],
                              "category": blocks.category(card),
                              "verified": blocks.is_verified(card),
                              "status": status,
                              "reason": (last or {}).get("reason") or "", "time": (last or {}).get("time") or "",
                              "seconds": (last or {}).get("seconds") or 0,
                              "models": models_count,
                              "models_from": (last or {}).get("models_from", ""),
                              "model_status": blocks.model_status(card["id"], home)})
            self._json({"blocks": items, "broken": broken, "pending": self._blocks_pending(home, items, broken)})
        elif path == "/api/blocks/detail":
            card_id = query.get("id", "")
            if not blocks._ID.fullmatch(card_id):
                self._json({"error": "编号不对"}, 400)
                return
            try:
                card = blocks.load_card(card_id, home)
            except BlockError:
                self._json({"error": "基础层里没有这个积木块"}, 404)
                return
            preview = ""
            if card["kind"] == "cli":
                self._blocks_path()
                preview = blocks_check.preview(card)
            secrets = [{"account": blocks.secret_account(card["id"], item["env"]), "label": item["label"],
                        "saved": bool(self.server.blocks_saved_keys.get(blocks.secret_account(card["id"], item["env"])))}
                       for item in card.get("secrets") or []]
            last_check = blocks.last_record(card_id, home, type="check")
            recs = blocks.records(card_id, home)
            last_check_idx = -1
            for idx, r in enumerate(recs):
                if r.get("type") == "check":
                    last_check_idx = idx
            if last_check is None:
                status = "never"
            elif last_check.get("ok"):
                status = "ok"
            elif any(r.get("type") == "model_check" and r.get("ok") for r in recs[last_check_idx + 1:]):
                status = "ok"
            elif last_check.get("status") in ("quota", "listed"):
                status = last_check.get("status")
            else:
                status = "fail"
            self._json({"card": card, "verified": blocks.is_verified(card), "preview": preview,
                        "category": blocks.category(card), "secrets": secrets,
                        "search_saved": bool(card["kind"] == "search" and self._blocks_search_key({})),
                        "key_account": card.get("key_account", ""),
                        "last_check": last_check,
                        "last_install": blocks.last_record(card_id, home, type="install"),
                        "status": status,
                        # 此刻是不是正在检测或试模型（一次一个的那把锁）：页面切走再回来时按它定按钮（68c 收尾）
                        "checking": blocks_check.checking(card_id),
                        "models_from": (last_check or {}).get("models_from", ""),
                        "model_status": blocks.model_status(card_id, home)})
        elif path == "/api/blocks/job":
            job_id = query.get("id", "")
            if not job_id and query.get("proposal"):
                job_id = self.server.blocks_proposal_jobs.get(query.get("proposal"), "")
            job = self.server.blocks_jobs.get(job_id)
            if not job:
                self._json({"error": "没有这个任务"}, 404)
                return
            answer = {"job": job_id, "kind": job["kind"], "done": job["done"], "result": job["result"], "error": job["error"], "saved": bool(job.get("saved"))}
            if job["kind"] == "install":
                if "tail" in job:
                    answer["tail"] = list(job["tail"])
                elif self.server.blocks_installing == job_id:
                    answer["tail"] = blocks_install.output_tail()
                else:
                    answer["tail"] = []
            self._json(answer)
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _blocks_post(self, path: str, body: dict) -> None:
        try:
            self._blocks_post_inner(path, body)
        except (BlockError, ConfigError) as exc:
            self._json({"error": str(exc)}, 409)
        except OSError:
            self._json({"error": "积木库资料夹读写出错，请检查磁盘和权限"}, 500)

    def _blocks_post_inner(self, path: str, body: dict) -> None:
        home = self._blocks_home()
        if path == "/api/blocks/bind":
            self._blocks_bind(home, body)
        elif path == "/api/blocks/keys":
            self._blocks_take_keys(body)
        elif path == "/api/blocks/connect":
            setting = load_clerk(home)
            base_url = ""
            account = ""
            if setting:
                base_url = setting.get("base_url") or (find_provider(setting["provider"]) or {}).get("base_url", "")
                try:
                    account = account_for(setting["provider"], base_url)
                except (KeyError, ValueError):
                    account = ""
            key = self._blocks_key(body, account, (setting or {}).get("provider", ""), base_url)
            if not key:
                self._json({"error": "没有密钥"}, 400)
                return
            if not setting:
                self._json({"error": "还没绑定积木库管家"}, 409)
                return
            if body.get("check") is True:  # 用户手填的密钥先发一句试试；窗口从钥匙串自动重连的不测，不多花额度
                try:
                    card = {"id": "connect-" + uuid.uuid4().hex[:8], "kind": "llm_api", "name": "积木库管家",
                            "provider": setting["provider"], "base_url": base_url, "model": setting["model"],
                            "key_account": account_for(setting["provider"], base_url)}
                    result = blocks_check.check_api(card, key, record=False,
                                                    client_factory=self.server.blocks_client_factory)
                except Exception as exc:  # noqa: BLE001
                    result = {"ok": False, "reason": blocks_check._mask(str(exc), key) or "检测出错"}
                if not result.get("ok"):
                    self._json({"ok": False, "reason": result.get("reason") or "",
                                "how": result.get("how") or "检查密钥，再试一次"})
                    return
            try:
                client = self._blocks_client(setting["provider"], base_url, setting["model"], key)
            except Exception:  # noqa: BLE001 — 原文可能带着密钥
                self._json({"error": "连不上积木库管家的模型，请到设置里重新绑定"}, 409)
                return
            self.server.blocks_clerk = client
            self._json({"ok": True})
        elif path == "/api/blocks/remove":
            card_id = str(body.get("id") or "")
            if not blocks._ID.fullmatch(card_id):
                self._json({"error": "编号不对"}, 400)
                return
            if blocks_check.checking(card_id):
                self._json({"error": "正在检测这个积木块，等检测完再移出"}, 409)
                return
            try:
                blocks.remove_card(card_id, home)
            except BlockError:
                self._json({"error": "基础层里没有这个积木块"}, 404)
                return
            self._json({"ok": True})
        elif path == "/api/blocks/scan":
            def work(job):
                terminal = self._blocks_path()
                return blocks_env.scan(root=home, app_path=os.environ.get("PATH", ""), terminal_path=terminal,
                                       record=True)
            self._json({"job": self._blocks_job("scan", work)})
        elif path == "/api/blocks/check":
            card_id = str(body.get("id") or "")
            if not blocks._ID.fullmatch(card_id):
                self._json({"error": "编号不对"}, 400)
                return
            try:
                card = blocks.load_card(card_id, home)
            except BlockError:
                self._json({"error": "基础层里没有这个积木块"}, 404)
                return
            model = body.get("model")
            if model is not None:
                is_preset = (card.get("kind") == "cli" and card.get("role") != "tool"
                             and card.get("id") in blocks.BUILTIN
                             and card.get("command") == blocks.BUILTIN[card["id"]]["command"]
                             and blocks.is_verified(card))
                if not is_preset:
                    self._json({"error": "没核实过的卡不能试模型"}, 400)
                    return
                last_check = blocks.last_record(card_id, home, type="check")
                models = (last_check or {}).get("models") or []
                if not models or model not in models:
                    self._json({"error": "这个模型不在这家的名单里，先检测一次拿名单"}, 400)
                    return
            approved = body.get("approved")
            api_key = (self._blocks_key(body, card.get("key_account", ""), card.get("provider", ""),
                                        card.get("base_url", "")) if card["kind"] == "llm_api" else
                       self._blocks_search_key(body) if card["kind"] == "search" else "")
            keys = self._blocks_tool_keys(card, body) if card["kind"] == "cli" else {}
            if card["kind"] == "search" and not api_key:
                self._json({"error": "要先填密钥"}, 400)
                return
            if any(not v for v in keys.values()):
                self._json({"error": "要先把这个工具的密钥都填好"}, 400)
                return
            if card["kind"] == "cli" and not blocks.is_verified(card) and not isinstance(approved, str):
                self._json({"error": "这块的接法没核实过：先把完整命令给用户看，用户确认后再检测"}, 400)
                return
            if card["kind"] == "llm_api" and not api_key:
                self._json({"error": "要先填密钥"}, 400)
                return
            job = self._blocks_job("check", self._blocks_check_work(home, card, approved, api_key, keys, model=model))
            self._json({"job": job})
        elif path == "/api/blocks/connect_builtin":
            name = str(body.get("id") or "")
            if name not in blocks.BUILTIN:
                self._json({"error": "只有 Codex、Cursor、agy 能一键接入"}, 400)
                return
            if blocks._card_path(name, home).is_file():
                self._json({"error": "基础层里已经有这个积木块"}, 409)
                return
            card = blocks.builtin_card(name)
            job = self._blocks_job("check", self._blocks_check_work(home, card, auto_save=True), card=card)
            self.server.blocks_pending_jobs[name] = job
            self._json({"job": job})
        elif path == "/api/blocks/save":
            self._blocks_save(home, str(body.get("job") or ""))
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _blocks_bind(self, home: str, body: dict) -> None:
        provider = str(body.get("provider") or "").strip()
        model = str(body.get("model") or "").strip()
        item = find_provider(provider) if provider else None
        # 认得的服务商用它自己的地址，页面传的不理：密钥不能被发到别处
        base_url = str((item or {}).get("base_url") or body.get("base_url") or "").strip()
        try:
            account = account_for(provider, base_url) if item is not None else ""
        except ValueError:
            account = ""
        key = self._blocks_key(body, account, provider, base_url)
        if not key or not model or item is None:
            self._json({"error": "要选服务商、填模型和密钥"}, 400)
            return
        if not base_url.startswith("https://"):
            self._json({"error": "地址不对，要以 https:// 开头"}, 400)
            return
        if not account:
            self._json({"error": "地址不对"}, 400)
            return
        card = {"id": "bind-" + uuid.uuid4().hex[:8], "kind": "llm_api", "name": "积木库管家", "provider": provider,
                "base_url": base_url, "model": model, "key_account": account}
        try:
            result = blocks_check.check_api(card, key, need_tools=True, record=False,
                                            client_factory=self.server.blocks_client_factory)
            if not result.get("ok"):
                self._json({"ok": False, "reason": result.get("reason") or "", "how": result.get("how") or ""})
                return
            client = self._blocks_client(provider, base_url, model, key)
            save_clerk(home, {"provider": provider, "model": model, "base_url": base_url})  # 连接建好了才保存
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "reason": blocks_check._mask(str(exc), key) or "绑定出错",
                        "how": "检查服务商、模型和密钥，再试一次"})
            return
        self.server.blocks_clerk = client
        self._json({"ok": True, "key_account": account, "reason": result.get("reason") or "",
                    "how": result.get("how") or ""})

    def _blocks_save(self, home: str, job_id: str) -> None:
        job = self.server.blocks_jobs.get(job_id)
        if not job:
            self._json({"error": "没有这个任务"}, 404)
            return
        card = job.get("card")
        if job["kind"] != "check" or not card or not job["done"] or not _blocks_connected(job["result"] or {}):
            self._json({"error": "检测还没通过，不能放进基础层"}, 409)
            return
        with self.server.blocks_lock:  # 查编号和写入之间不能被别的保存插进来
            if not job.get("saved"):
                if blocks._card_path(card["id"], home).is_file() and not job.get("replaces"):
                    self._json({"error": "基础层里已经有同编号的一块：跟管家说重新提议"}, 409)
                    return
                blocks.save_card(card, home)
                job["saved"] = True
                try:  # 升级前留下的旧提议（一块一个模型）放进来后，立刻并成整家一块
                    if card["kind"] == "llm_api" and blocks.migrate_api_cards(home) and not blocks._card_path(card["id"], home).is_file():
                        job["saved_id"] = card["provider"]
                        # 这次的检测结果就是最新的：直接记到整家块上，不靠只精确到秒的时间比先后（第三轮复查第 4 条）
                        fresh = {k: v for k, v in (job.get("result") or {}).items() if k != "type"}
                        blocks.add_record(card["provider"], dict(fresh, type="check"), home)
                except Exception:  # noqa: BLE001
                    pass
        self._json({"ok": True, "id": job.get("saved_id") or card["id"], "key_account": card.get("key_account", "")})

    def _ability_status(self, card: dict, home: str) -> tuple[str, str]:
        block_id = card.get("block", "")
        try:
            bcard = blocks.load_card(block_id, home)
        except BlockError:
            return "unusable", f"用的积木块「{block_id}」已经移出基础层"

        recs = blocks.records(block_id, home)
        last_check_idx = -1
        last = None
        for idx, r in enumerate(recs):
            if r.get("type") == "check":
                last_check_idx = idx
                last = r
        is_usable = True
        if last is not None:
            if last.get("ok"):
                is_usable = True
            elif any(r.get("type") == "model_check" and r.get("ok") for r in recs[last_check_idx + 1:]):
                is_usable = True
            elif last.get("status") in ("quota", "listed"):
                is_usable = True
            else:
                is_usable = False
        if not is_usable:
            bname = bcard.get("name", block_id)
            return "unusable", f"积木块「{bname}」上次检测没通过"

        try:
            abilities.fits_block(card, bcard)
        except AbilityError as exc:
            return "unusable", str(exc)

        recent = abilities.runs(card["id"], home, limit=1)
        if recent and not recent[-1].get("ok"):
            return "failed", recent[-1].get("reason", "")

        return "ok", ""

    def _safe_result_folder(self, home: str, ability_id: str, run: str) -> Path | None:
        folder = abilities.result_folder(ability_id, run, home)
        p1 = Path(home) / abilities.RESULT_DIR
        p2 = p1 / ability_id
        p3 = folder
        if p1.is_symlink() or p2.is_symlink() or p3.is_symlink():
            return None
        if not p3.is_dir():
            return None
        try:
            p1_real = p1.resolve()
            expected = p1_real / ability_id / run
            if p3.resolve() != expected.resolve():
                return None
        except Exception:
            return None
        return p3

    def _abilities_get(self, path: str, query: dict) -> None:
        try:
            self._abilities_get_inner(path, query)
        except (AbilityError, BlockError) as exc:
            self._json({"error": str(exc)}, 409)
        except OSError:
            self._json({"error": "积木库资料夹读写出错，请检查磁盘和权限"}, 500)

    def _abilities_get_inner(self, path: str, query: dict) -> None:
        home = self._blocks_home()
        abilities.ensure_builtin(home)
        if path == "/api/abilities/list":
            raw_list = abilities.list_abilities(home)
            items, broken = [], []
            for item in raw_list:
                if "problem" in item:
                    broken.append({"id": item["id"], "problem": item["problem"]})
                    continue
                block_id = item.get("block", "")
                try:
                    bcard = blocks.load_card(block_id, home)
                    bname = bcard.get("name", block_id)
                except BlockError:
                    bcard = None
                    bname = block_id

                kind = item.get("how", {}).get("kind", "")
                if kind == "search":
                    model = None
                elif kind == "cli":
                    model = item.get("model") or (bcard.get("model") if bcard else None) or (blocks.BUILTIN.get(block_id, {}).get("model"))
                else:
                    model = item.get("model")

                status, status_reason = self._ability_status(item, home)
                recent = abilities.runs(item["id"], home, limit=1)
                last_time = recent[-1].get("time", "") if recent else ""

                items.append({
                    "id": item["id"],
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "block": block_id,
                    "block_name": bname,
                    "model": model,
                    "kind": kind,
                    "output_type": item.get("output", {}).get("type", ""),
                    "status": status,
                    "status_reason": status_reason,
                    "last_time": last_time,
                })
            self._json({"abilities": items, "broken": broken})
        elif path == "/api/abilities/detail":
            ability_id = query.get("id", "")
            if not blocks._ID.fullmatch(ability_id):
                self._json({"error": "编号不对"}, 400)
                return
            try:
                card = abilities.load_ability(ability_id, home)
            except AbilityError:
                self._json({"error": "能力层里没有这张能力卡片"}, 404)
                return
            block_id = card.get("block", "")
            try:
                bcard = blocks.load_card(block_id, home)
                bmodel = str(bcard.get("model") or "")
                binfo = {
                    "id": block_id,
                    "name": bcard.get("name", block_id),
                    "exists": True,
                    "kind": bcard.get("kind", ""),
                    "model": bmodel,
                    "command": bcard.get("command", ""),
                    "verified": blocks.is_verified(bcard),
                }
            except BlockError:
                bcard = None
                binfo = {"id": block_id, "name": block_id, "exists": False, "kind": "", "model": "", "command": "", "verified": False}

            status, status_reason = self._ability_status(card, home)
            models = []
            is_preset_cli = (bcard is not None and binfo["kind"] == "cli" and bcard.get("role") != "tool"
                             and block_id in blocks.BUILTIN
                             and bcard.get("command") == blocks.BUILTIN[block_id]["command"]
                             and blocks.is_verified(bcard))
            if binfo["kind"] == "llm_api" or is_preset_cli:
                last_check = blocks.last_record(block_id, home, type="check") or {}
                models = list(last_check.get("models") or [])

            all_runs = abilities.runs(ability_id, home)
            example = None
            if card.get("example_run"):
                for r in all_runs:
                    if r.get("run_id") == card.get("example_run"):
                        example = r
                        break

            recent_10 = list(reversed(all_runs))[:10]
            running = abilities_run.is_running(ability_id)
            job_info = self.server.abilities_jobs.get(ability_id)
            job_id = ""
            job_inputs = {}
            if job_info:
                if isinstance(job_info, dict):
                    jid = job_info.get("job", "")
                    jinp = job_info.get("inputs", {})
                else:
                    jid = str(job_info)
                    jinp = {}
                if jid:
                    job = self.server.blocks_jobs.get(jid)
                    if job and not job.get("done"):
                        job_id = jid
                        job_inputs = jinp

            self._json({
                "ability": card,
                "block": binfo,
                "status": status,
                "status_reason": status_reason,
                "models": models,
                "model_status": blocks.model_status(block_id, home),
                "example": example,
                "runs": recent_10,
                "running": running,
                "job": job_id,
                "job_inputs": job_inputs if job_id else {},
            })
        elif path == "/api/abilities/file":
            ability_id = query.get("id", "")
            run = query.get("run", "")
            name = query.get("name", "")
            if not blocks._ID.fullmatch(ability_id):
                self._json({"error": "编号不对"}, 400)
                return
            if not name or "/" in name or "\\" in name or name.startswith(".") or len(name) > 100:
                self._json({"error": "文件名不对"}, 400)
                return
            try:
                folder = self._safe_result_folder(home, ability_id, run)
            except AbilityError:
                self._json({"error": "运行编号不对"}, 400)
                return
            if not folder:
                self._json({"error": "没有这个成果文件"}, 404)
                return
            target = folder / name
            if target.is_symlink() or not target.is_file():
                self._json({"error": "没有这个成果文件"}, 404)
                return
            try:
                p1 = Path(home) / abilities.RESULT_DIR
                expected_file = (p1.resolve() / ability_id / run / name).resolve()
                if target.resolve() != expected_file:
                    self._json({"error": "没有这个成果文件"}, 404)
                    return
            except Exception:
                self._json({"error": "没有这个成果文件"}, 404)
                return

            ext = target.suffix.lower()
            if ext == ".png":
                ctype = "image/png"
            elif ext in (".jpg", ".jpeg"):
                ctype = "image/jpeg"
            elif ext == ".txt":
                ctype = "text/plain; charset=utf-8"
            elif ext == ".json":
                ctype = "application/json; charset=utf-8"
            else:
                ctype = "application/octet-stream"
            try:
                body = target.read_bytes()
            except OSError:
                self._json({"error": "积木库资料夹读写出错，请检查磁盘和权限"}, 500)
                return
            self._send(200, body, ctype)
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _abilities_post(self, path: str, body: dict) -> None:
        try:
            self._abilities_post_inner(path, body)
        except (AbilityError, BlockError) as exc:
            self._json({"error": str(exc)}, 409)
        except OSError:
            self._json({"error": "积木库资料夹读写出错，请检查磁盘和权限"}, 500)

    def _abilities_post_inner(self, path: str, body: dict) -> None:
        home = self._blocks_home()
        abilities.ensure_builtin(home)
        if path == "/api/abilities/run":
            ability_id = body.get("id", "")
            if not blocks._ID.fullmatch(ability_id):
                self._json({"error": "编号不对"}, 400)
                return
            try:
                card = abilities.load_ability(ability_id, home)
            except AbilityError:
                self._json({"error": "能力层里没有这张能力卡片"}, 404)
                return

            model = body.get("model")
            if model is not None and model != "":
                backup_models = card.get("backup_models") or []
                if model not in backup_models:
                    self._json({"error": "这个模型不是这张卡的备用模型"}, 400)
                    return
            else:
                model = None

            inputs = body.get("inputs")
            if inputs is None or not isinstance(inputs, dict):
                inputs = {}
            try:
                abilities.prepare(card, inputs)
            except (AbilityError, BlockError) as exc:
                self._json({"error": str(exc)}, 400)
                return

            if abilities_run.is_running(ability_id):
                self._json({"error": "这张能力卡片正在用，等这次跑完"}, 409)
                return

            def work(job):
                self._blocks_path()
                return abilities_run.run(
                    ability_id, inputs, home, model=model, kind="run",
                    keys=dict(self.server.blocks_saved_keys),
                    search_fn=self.server.abilities_search_fn,
                    client_factory=self.server.blocks_client_factory,
                    runner=self.server.abilities_runner,
                    path=os.environ.get("PATH", ""),
                    codex_images=self.server.abilities_codex_images,
                )

            job_id = self._blocks_job("ability", work)
            with self.server.blocks_lock:
                self.server.abilities_jobs[ability_id] = {"job": job_id, "inputs": dict(inputs)}
            self._json({"job": job_id})
        elif path == "/api/abilities/model":
            ability_id = body.get("id", "")
            if not blocks._ID.fullmatch(ability_id):
                self._json({"error": "编号不对"}, 400)
                return
            try:
                card = abilities.load_ability(ability_id, home)
            except AbilityError:
                self._json({"error": "能力层里没有这张能力卡片"}, 404)
                return
            kind = card.get("how", {}).get("kind")
            block_id = card.get("block", "")
            bcard = None
            if block_id:
                try:
                    bcard = blocks.load_card(block_id, home)
                    bname = bcard.get("name", block_id)
                except BlockError:
                    bname = block_id
            else:
                bname = ""
            is_preset_cli = (
                kind == "cli"
                and bcard is not None
                and bcard.get("role") != "tool"
                and block_id in blocks.BUILTIN
                and bcard.get("command") == blocks.BUILTIN[block_id]["command"]
                and blocks.is_verified(bcard)
            )
            if kind != "llm_api" and not is_preset_cli:
                self._json({"error": "这张卡这一版不能换模型"}, 400)
                return
            last_rec = blocks.last_record(block_id, home, type="check") or {}
            models = last_rec.get("models") or []
            if not models:
                self._json({"error": f"还没有模型名单：先去基础层检测「{bname}」"}, 409)
                return

            model = body.get("model")
            backup_models = body.get("backup_models")
            if not model or model not in models:
                self._json({"error": f"主模型「{model}」不在可用模型名单里"}, 400)
                return
            if not isinstance(backup_models, list):
                self._json({"error": "备用模型必须是列表"}, 400)
                return
            if len(backup_models) > 2:
                self._json({"error": "备用模型最多设置 2 个"}, 400)
                return
            if len(set(backup_models)) != len(backup_models):
                self._json({"error": "备用模型不能重复"}, 400)
                return
            if model in backup_models:
                self._json({"error": "备用模型不能包含主模型"}, 400)
                return
            for m in backup_models:
                if m not in models:
                    self._json({"error": f"备用模型「{m}」不在可用模型名单里"}, 400)
                    return

            card["model"] = model
            card["backup_models"] = list(backup_models)
            abilities.save_ability(card, home)
            self._json({"ok": True, "ability": card})
        elif path == "/api/abilities/remove":
            ability_id = body.get("id", "")
            if not blocks._ID.fullmatch(ability_id):
                self._json({"error": "编号不对"}, 400)
                return
            try:
                card = abilities.load_ability(ability_id, home)
            except AbilityError:
                self._json({"error": "能力层里没有这张能力卡片"}, 404)
                return
            if abilities_run.is_running(ability_id):
                self._json({"error": "这张能力卡片正在用，等这次跑完再移出"}, 409)
                return
            abilities.remove_ability(ability_id, home)
            self._json({"ok": True})
        elif path == "/api/abilities/reveal":
            ability_id = body.get("id", "")
            run = body.get("run", "")
            if not blocks._ID.fullmatch(ability_id):
                self._json({"error": "编号不对"}, 400)
                return
            try:
                folder = self._safe_result_folder(home, ability_id, run)
            except AbilityError:
                self._json({"error": "运行编号不对"}, 400)
                return
            if not folder:
                self._json({"error": "这次没有成果文件夹"}, 404)
                return
            opener = self.server.blocks_open
            if opener is not None:
                opener(str(folder))
            else:
                subprocess.run(["/usr/bin/open", str(folder)], check=False, timeout=10)
            self._json({"ok": True})
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _blocks_proposal(self, project: str, conversation: str, body: dict) -> None:
        """积木库的提议：确认后真的去安装或检测。整段排队做，两次确认同时到也不会乱。"""
        server, desk = self.server, self.server.desk
        pid, decision = str(body.get("id") or ""), str(body.get("decision") or "")
        with server.blocks_confirm_lock:
            proposal = clerk_tasks.load_proposal(project, pid)
            block = (proposal or {}).get("block") or {}
            pkind = (proposal or {}).get("kind")
            api_key = (self._blocks_key(body, str(block.get("key_account") or ""), str(block.get("provider") or ""),
                                        str(block.get("base_url") or ""))
                       if pkind == "connect_api" else self._blocks_search_key(body) if pkind == "connect_search" else "")
            keys = {}
            if pkind == "connect" and block.get("secrets"):
                try:
                    keys = self._blocks_tool_keys(blocks.validate_card(block), body)
                except BlockError:
                    keys = {}
            if decision == "confirm" and proposal and proposal.get("state") == "open":
                if pkind in ("connect_api", "connect_search") and not api_key:
                    self._json({"error": "请先在密钥框里填好密钥"}, 400)
                    return
                if pkind == "connect" and any(not v for v in keys.values()):
                    self._json({"error": "请先把每个密钥框都填好"}, 400)
                    return
                if pkind in ("install", "web_install") and (blocks_install.installing() or server.blocks_installing):
                    self._json({"error": "正在安装别的工具，等它装完再装"}, 409)
                    return
            try:
                answer = desk.decide_proposal(project, conversation, pid, decision)
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
                return
            except (RuntimeError, TaskError) as exc:
                self._json({"error": str(exc)}, 409)
                return
            reply = {"ok": True, "card": answer.get("card") or "", "state": answer.get("state")}
            kind = answer.get("kind")
            if answer.get("state") != "confirmed" or kind not in _BLOCK_KINDS:
                self._json(reply)
                return
            if answer.get("repeat"):
                reply["job"] = server.blocks_proposal_jobs.get(pid, "")
                self._json(reply)
                return
            home = str(blocks.home())
            replaces = bool(proposal and proposal.get("replaces"))
            if kind in ("install", "web_install"):
                with server.blocks_lock:
                    server.blocks_installing = "pending"
                work = (self._blocks_install_work(home, answer["install"]) if kind == "install"
                        else self._blocks_web_install_work(home, answer["spec"]))
                job = self._blocks_job("install", work)
                with server.blocks_lock:
                    if server.blocks_installing == "pending":
                        server.blocks_installing = job
            else:
                block = answer["block"]
                work = self._blocks_check_work(home, block, answer.get("approved"), api_key, keys)
                job = self._blocks_job("check", work, card=block, replaces=replaces)
            server.blocks_proposal_jobs[pid] = job
            reply["job"] = job
            self._json(reply)

    def do_GET(self):  # noqa: N802  名字由 http.server 规定
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        project = query.get("project", "")
        try:
            if url.path == "/api/health":
                self._json({"ok": True})
            elif url.path.startswith("/api/blocks/"):
                if self._block_allowed(query):
                    self._blocks_get(url.path, query)
            elif url.path.startswith("/api/abilities/"):
                if self._block_allowed(query):
                    self._abilities_get(url.path, query)
            elif url.path == "/api/view":
                if not project or not Path(project).is_dir():
                    self._json({"error": "项目文件夹不存在"}, 400)
                    return
                session = getattr(self.server, "hub", None) and self.server.hub.get(project)
                self._json(card_view(project, query.get("card") or None, dispatcher=query.get("dispatcher"),
                                     chat_session=session))
            elif url.path == "/api/cards":
                self._json(list_cards(project) if project and Path(project).is_dir() else [])
            elif url.path == "/api/plan":
                card_id = query.get("card", "")
                if not project or not Path(project).is_dir() or not card_id:
                    self._json({"error": "需要项目和卡片"}, 400)
                    return
                if not _CARD_ID.fullmatch(card_id):
                    self._json({"error": "卡片编号不对"}, 400)
                    return
                self._json(self._plan_payload(project, card_id))
            elif url.path == "/api/text":
                session = getattr(self.server, "hub", None) and self.server.hub.get(project)
                text = event_text(project, query.get("card", ""), int(query.get("seq", "0") or 0),
                                  chat_session=session)
                if text is None:
                    self._json({"error": "没有这条记录"}, 404)
                else:
                    self._json({"text": text})
            elif url.path in ("/", "/index.html"):
                self._static("panel.html")
            elif url.path.startswith("/static/"):
                self._static(url.path[len("/static/"):])
            elif url.path.startswith("/api/chat/"):
                if self._chat_allowed(query):
                    self._chat_get(url.path, query)
            elif url.path.startswith("/api/clerk/"):
                if self._chat_allowed(query):
                    self._clerk_get(url.path, query)
            elif url.path.startswith("/api/team"):
                if self._chat_allowed(query):
                    self._team_get(url.path, query)
            else:
                self._json({"error": "没有这个地址"}, 404)
        except (ValueError, OSError) as exc:
            self._json({"error": f"读取失败：{exc}"}, 500)

    # ---------- 聊天 ----------

    def _chat_allowed(self, query=None) -> bool:
        """聊天地址必须带上窗口给的一次性口令；没有口令的服务（比如手动启动的）不开放聊天。"""
        expected = getattr(self.server, "token", None)
        given = unquote(self.headers.get("X-Chat-Token") or (query or {}).get("token") or "")
        if expected and given and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            return True
        self._json({"error": "聊天需要从超级助手窗口里打开"}, 403)
        return False

    def _block_allowed(self, query=None) -> bool:
        """积木库接口也要校验口令，避免非法访问。"""
        return self._chat_allowed(query)

    def _chat_get(self, path: str, query: dict) -> None:
        hub = self.server.hub
        session = hub.get(query.get("project", ""))
        if path == "/api/chat/events":
            if session is None:
                self._json({"events": [], "open": False, "busy": False})
                return
            wait = min(float(query.get("wait", "20") or 20), 25)
            events = session.events_since(int(query.get("since", "0") or 0), timeout=wait)
            self._json({"events": events, "open": True, "busy": session.busy})
        elif path == "/api/chat/threads":
            self._json(hub.thread_list(query.get("project", "")))
        elif path == "/api/chat/thread":
            self._json({"events": session.read_thread(query.get("id", "")) if session is not None else []})
        elif path == "/api/chat/image":
            target = query.get("path", "")
            suffix = Path(target).suffix.lower()
            if session is None or suffix not in _IMAGE_TYPES or not session.image_allowed(target) \
                    or not Path(target).is_file():
                self._json({"error": "没有这张图"}, 404)
                return
            self._send(200, Path(target).read_bytes(), _IMAGE_TYPES[suffix])
        else:
            self._json({"error": "没有这个地址"}, 404)

    # ---------- 团队配置 ----------

    def _team_get(self, path: str, query: dict) -> None:
        project = query.get("project", "")
        if not project or not Path(project).is_dir():
            self._json({"error": "项目文件夹不存在"}, 400)
            return
        if path == "/api/team":
            try:
                team = load_team(project)
                models = load_models(project)
                role_models = load_role_models(project, team)
            except ConfigError as exc:
                team, models, problem = dict(DEFAULT_TEAM, dispatcher=None, configured=False), dict(DEFAULT_MODELS), str(exc)
                role_models = {}
            else:
                problem = ""
            self._json({"team": team, "models": models, "role_models": role_models, "problem": problem,
                        "git": (Path(project) / ".git").exists(),
                        "installed": (Path(project) / ".collab" / "cards").is_dir(), "options": _TEAM_OPTIONS,
                        "model_options": load_model_options(), "check_timeout": CHECK_TIMEOUT})
        elif path == "/api/team/draft":
            draft_path = Path(project) / ".collab" / "团队草稿.json"
            try:
                draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.is_file() else None
            except (OSError, ValueError):
                draft = None
            self._json({"draft": draft})
        elif path == "/api/team/check":
            state = self.server.checks.get((str(Path(project).resolve()), query.get("run", "")))
            if state is None:
                self._json({"results": [], "done": True, "started": False})
                return
            with state["lock"]:
                self._json({"results": list(state["results"]), "done": state["done"], "started": True})
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _team_post(self, path: str, body: dict) -> None:
        project = body.get("project", "")
        if not project or not Path(project).is_dir():
            self._json({"error": "项目文件夹不存在"}, 400)
            return
        if path == "/api/team/save":
            if not (Path(project) / ".collab" / "cards").is_dir():
                self._json({"error": "这个文件夹还没接入超级助手（不是 git 项目），不能保存执行员"}, 409)
                return
            try:
                if body.get("role_models") is not None:
                    clean_role_models(body.get("role_models") or {})  # 先查，有错什么都不存（复查第 1 轮）
                if body.get("models") is not None:
                    clean_models(body.get("models") or {})  # 整家模型也先查（复查第 2 轮）
                save_team(project, body.get("team") or {})
                if body.get("models") is not None:
                    save_models(project, body.get("models") or {})
                if body.get("role_models") is not None:
                    save_role_models(project, body.get("role_models") or {})  # 每个岗位各存各的（流程修理 ①）
            except ConfigError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"ok": True})
        elif path == "/api/team/draft":
            # 团队设置改了还没保存：暂存在项目里（应用每次换端口，页面自己存的会丢，复查第 1 轮）
            draft_path = Path(project) / ".collab" / "团队草稿.json"
            draft = body.get("draft")
            if not (Path(project) / ".collab").is_dir():
                self._json({"ok": False})
                return
            if draft is None:
                if draft_path.is_file():
                    draft_path.unlink()
            else:
                draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            self._json({"ok": True})
        elif path == "/api/team/check":
            team = body.get("team") or {}
            models = body.get("models")
            run_id = uuid.uuid4().hex  # 每次检测各记各的：几个岗位同时测，互不覆盖
            state = {"results": [], "done": False, "lock": threading.Lock()}
            self.server.checks[(str(Path(project).resolve()), run_id)] = state

            def collect(item):
                with state["lock"]:
                    state["results"].append(item)

            def run():
                try:
                    check_team(project, team=team, on_result=collect, roles=body.get("roles"), models=models,
                               role_models=body.get("role_models"))
                finally:
                    with state["lock"]:
                        state["done"] = True

            threading.Thread(target=run, daemon=True).start()
            self._json({"ok": True, "run": run_id})
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _clerk_get(self, path: str, query: dict) -> None:
        desk, project = self.server.desk, query.get("project", "")
        if blocks_tools.is_blocks_home(project):
            Path(project).mkdir(parents=True, exist_ok=True)
        if path == "/api/clerk/list":
            if project and Path(project).is_dir():
                self.server.watcher.ensure_watching(project)
            self._json(desk.list(project))
        elif path == "/api/clerk/events":
            wait = min(float(query.get("wait", "20") or 20), 25)
            self._json(desk.events(project, query.get("conversation", ""), int(query.get("since", "0") or 0), wait))
        elif path == "/api/clerk/providers":
            self._json({"providers": PROVIDERS})
        elif path == "/api/clerk/setting":
            project = query.get("project", "")
            if not project:
                self._json({"error": "需要项目"}, 400)
                return
            try:
                self._json({"setting": load_clerk(project)})
            except ConfigError as exc:
                self._json({"error": str(exc)}, 400)
        elif path == "/api/clerk/preview":
            project = query.get("project", "")
            card_id = query.get("card", "")
            if not project or not card_id:
                self._json({"error": "需要项目和卡片"}, 400)
                return
            if not _CARD_ID.fullmatch(card_id):
                self._json({"error": "卡片编号不对"}, 400)
                return
            self._json(self.server.preview.status(project, card_id))
        elif path == "/api/clerk/uncommitted":
            if not project:
                self._json({"error": "需要项目"}, 400)
                return
            self._json({"count": uncommitted_count(project)})
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _pin_clerk_setting(self, project: str) -> None:
        """第一次说话时固定这个项目用的服务商：之后别的项目换了，它也不跟着变。"""
        pending = getattr(self.server, "clerk_setting", None)
        try:
            if pending and self.server.clerk is not None and load_clerk(project) is None:
                save_clerk(project, pending)
        except (ConfigError, OSError):
            pass  # 固定不了也不耽误说话

    def _clerk_post(self, path: str, body: dict) -> None:
        """窗口把行政人员的密钥交给本机服务：只放内存，不写文件，也不回传。"""
        desk, project, conversation = self.server.desk, str(body.get("project") or ""), str(body.get("conversation") or "")
        if blocks_tools.is_blocks_home(project):
            Path(project).mkdir(parents=True, exist_ok=True)
            if path == "/api/clerk/send":  # 积木库的行政有自己的连接，不受项目窗口“属于另一个项目”的限制
                self._blocks_path()  # 行政查安装、出提议时看到的路径和之后检测用的一样
                try:
                    self._json({"ok": True, "conversation": desk.send(project, conversation, str(body.get("text") or ""))})
                except RuntimeError as exc:
                    self._json({"error": str(exc)}, 409)
                return
            if path == "/api/clerk/proposal":
                try:
                    self._blocks_proposal(project, conversation, body)
                except OSError:
                    self._json({"error": "没能保存，请检查磁盘和权限"}, 500)
                return
            if path in ("/api/clerk/setting", "/api/clerk/config", "/api/clerk/test"):
                self._json({"error": "积木库管家要在积木库窗口里绑定（会检测它会不会用工具）"}, 400)
                return
        if path == "/api/clerk/preview":
            card_id = str(body.get("card") or "").strip()
            action = str(body.get("action") or "").strip()
            if not project or not card_id or action not in ("start", "stop"):
                self._json({"error": "需要项目、卡片和操作"}, 400)
                return
            if not _CARD_ID.fullmatch(card_id):
                self._json({"error": "卡片编号不对"}, 400)
                return
            if action == "stop":
                self.server.preview.stop()
                self._json({"ok": True})
                return
            self._json(self.server.preview.start(project, card_id))
            return
        if path in ("/api/clerk/send", "/api/clerk/stop", "/api/clerk/focus"):
            if not project or not Path(project).is_dir():
                self._json({"error": "项目文件夹不存在"}, 400)
                return
            bound = getattr(self.server, "clerk_project", "") or ""
            if path == "/api/clerk/send" and bound and bound != project:
                self._json({"error": "这句话属于另一个项目，不能发给当前项目的行政。请先回到那个项目，或到设置里重新配置。"}, 409)
                return
            try:
                if path == "/api/clerk/send":
                    self._pin_clerk_setting(project)
                    self._json({"ok": True, "conversation": desk.send(project, conversation, str(body.get("text") or ""))})
                    return
                (desk.stop if path == "/api/clerk/stop" else desk.focus)(project, conversation)
                self._json({"ok": True})
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 409)
            return
        if path == "/api/clerk/delete":
            if not project or not Path(project).is_dir():
                self._json({"error": "项目文件夹不存在"}, 400)
                return
            try:
                desk.delete(project, conversation)
                self._json({"ok": True})
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 409)
            except OSError:
                self._json({"error": "没能删掉这段对话，请检查文件夹权限。"}, 409)
            return
        if path == "/api/clerk/proposal":
            if not project or not Path(project).is_dir():
                self._json({"error": "项目文件夹不存在"}, 400)
                return
            try:
                answer = desk.decide_proposal(project, conversation, str(body.get("id") or ""),
                                             str(body.get("decision") or ""))
                self._json({"ok": True, "card": answer.get("card") or "", "state": answer.get("state")})
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
            except (RuntimeError, TaskError) as exc:
                self._json({"error": str(exc)}, 409)
            return
        if path == "/api/clerk/card/open":
            card_id = str(body.get("card") or "").strip()
            if not project or not Path(project).is_dir() or not card_id:
                self._json({"error": "需要项目和卡片"}, 400)
                return
            if not _CARD_ID.fullmatch(card_id):
                self._json({"error": "卡片编号不对"}, 400)
                return
            try:
                self._json(self.server.desk.open_card(project, card_id))
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
            return
        if path == "/api/clerk/card/archive":
            card_id = str(body.get("card") or "").strip()
            if not project or not Path(project).is_dir():
                self._json({"error": "项目文件夹不存在"}, 400)
                return
            if not card_id or not _CARD_ID.fullmatch(card_id):
                self._json({"error": "卡片编号不对"}, 400)
                return
            try:
                set_archived(project, card_id, bool(body.get("archived")))
                self._json({"ok": True})
            except LookupError as exc:
                self._json({"error": str(exc)}, 404)
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 409)
            except OSError:
                self._json({"error": "没能保存，请检查文件夹权限。"}, 409)
            return
        if path == "/api/clerk/card":
            card_id = str(body.get("card") or "").strip()
            action = str(body.get("action") or "").strip()
            if not project or not Path(project).is_dir() or not card_id or not action:
                self._json({"error": "需要项目、卡片和操作"}, 400)
                return
            if not _CARD_ID.fullmatch(card_id):
                self._json({"error": "卡片编号不对"}, 400)
                return
            extra = str(body.get("text") or "")
            if action == "revise" and not extra.strip():
                self._json({"error": "请写上要改什么"}, 400)
                return
            self._do_card_action(project, card_id, action, extra)
            return
        if path == "/api/clerk/setting":
            if not project or not Path(project).is_dir():
                self._json({"error": "项目文件夹不存在"}, 400)
                return
            try:
                save_clerk(project, body)
                self._json({"ok": True, "setting": load_clerk(project)})
            except ConfigError as exc:
                self._json({"error": str(exc)}, 400)
            return
        if path == "/api/clerk/clear":
            self.server.clerk = None
            self.server.clerk_project = ""
            self.server.clerk_setting = None
            self._json({"ok": True})
            return
        provider = str(body.get("provider") or "").strip()
        if provider == "other" and not str(body.get("base_url") or "").strip():
            self._json({"error": "地址不对，要以 http:// 或 https:// 开头"}, 400)  # 不能落到默认地址：密钥会发给别家
            return
        item = find_provider(provider) if provider else None
        thinking_off = True if item is None else bool(item.get("thinking_off", True))
        client = ModelClient(str(body.get("base_url") or DEFAULT_BASE_URL), str(body.get("model") or DEFAULT_MODEL),
                             str(body.get("api_key") or ""), timeout=30, thinking_off=thinking_off)
        if path == "/api/clerk/test":
            try:
                client.chat([{"role": "user", "content": "只回答：好"}])
                self._json({"ok": True})
            except ModelError as exc:
                self._json({"ok": False, "error": str(exc)})
        elif path == "/api/clerk/config":
            self.server.clerk = client
            self.server.clerk_project = project
            self.server.clerk_setting = {"provider": provider, "base_url": str(body.get("base_url") or ""), "model": str(body.get("model") or "")} if provider else None
            self._json({"ok": True})
        else:
            self._json({"error": "没有这个地址"}, 404)

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        if not url.path.startswith(("/api/chat/", "/api/team/", "/api/clerk/", "/api/blocks/", "/api/abilities/")):
            self._json({"error": "没有这个地址"}, 404)
            return
        if not self._chat_allowed():  # 没口令：已经回了 403
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._json({"error": "消息太长"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"error": "看不懂的请求"}, 400)
            return
        if url.path.startswith("/api/team/"):
            self._team_post(url.path, body)
            return
        if url.path.startswith("/api/blocks/"):
            if self._block_allowed():
                self._blocks_post(url.path, body)
            return
        if url.path.startswith("/api/abilities/"):
            if self._block_allowed():
                self._abilities_post(url.path, body)
            return
        if url.path.startswith("/api/clerk/"):
            self._clerk_post(url.path, body)
            return
        hub, project = self.server.hub, body.get("project", "")
        try:
            if url.path == "/api/chat/open":
                if not project or not Path(project).is_dir():
                    self._json({"error": "项目文件夹不存在"}, 400)
                    return
                thread_id = (body.get("thread_id") or "").strip() or None
                hub.open(project, body.get("dispatcher", ""), body.get("mode", "continue"),
                         with_opening=body.get("opening", True) is not False, thread_id=thread_id)
                self._json({"ok": True})
                return
            if url.path == "/api/chat/close":
                hub.close(project)
                self._json({"ok": True})
                return
            if url.path == "/api/chat/delete":
                try:
                    hub.delete_thread(project, str(body.get("thread_id") or ""))
                    self._json({"ok": True})
                except LookupError as exc:
                    self._json({"error": str(exc)}, 404)
                except RuntimeError as exc:
                    self._json({"error": str(exc)}, 409)
                return
            session = hub.get(project)
            if session is None:
                self._json({"error": "这个项目的调度员还没连上"}, 409)
                return
            if url.path == "/api/chat/send":
                text = (body.get("text") or "").strip()
                if not text:
                    self._json({"error": "消息是空的"}, 400)
                    return
                session.send(text)
            elif url.path == "/api/chat/answer":
                session.answer(str(body.get("id", "")), body.get("decision", ""))
            elif url.path == "/api/chat/stop":
                session.stop()
            else:
                self._json({"error": "没有这个地址"}, 404)
                return
            self._json({"ok": True})
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except (KeyError, RuntimeError, TimeoutError) as exc:
            self._json({"error": str(exc).strip("'\"")}, 409)


class LocalServer(ThreadingHTTPServer):
    """只服务本机。标准做法启动时会查一次本机的网络名字（socket.getfqdn），
    Python 3.15 在有的网络下要等 35 秒，窗口会一直停在“正在启动”；这里用不到这个名字，直接跳过。"""

    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_server(port: int = 0, token=None, hub=None) -> ThreadingHTTPServer:
    server = LocalServer(("127.0.0.1", port), Handler)
    server.token = token          # 聊天口令；没有就不开放聊天
    server.hub = hub if hub is not None else ChatHub()
    server.clerk = None           # 行政人员的模型连接，密钥只在内存里
    server.blocks_clerk = None    # 积木库行政的模型连接
    server.blocks_saved_keys = {}  # Mac 窗口从钥匙串交来的密钥：账号 -> 密钥，只在内存里（60 号单）
    clerk_tasks.key_source = lambda account: server.blocks_saved_keys.get(account, "")  # 行政的联网搜索用
    server.blocks_client_factory = None
    server.blocks_models_fetcher = None  # 测试、走查时换成假的模型名单
    server.blocks_migrated = set()
    server.terminal_path = terminal_path
    server.blocks_jobs = {}            # 积木库后台任务：编号 -> 状态
    server.blocks_proposal_jobs = {}   # 提议编号 -> 任务编号：同一个提议点两次拿到同一个任务
    server.blocks_pending_jobs = {}    # 待接入的预置工具 -> 检测任务（64 号单）
    server.blocks_lock = threading.Lock()
    server.blocks_confirm_lock = threading.Lock()
    server.blocks_path_lock = threading.Lock()
    server.blocks_terminal = None      # (读到的时间, 终端路径)
    server.blocks_installing = None    # 正在装的那个任务编号（开始前先占住，两次确认不会都过）
    server.clerk_project = ""     # 这份连接属于哪个项目；空表示不拦（旧测试直接设 clerk）
    server.clerk_setting = None   # 这份连接用的服务商和模型，不含密钥
    server.desk = ClerkDesk(lambda: server.clerk, blocks_client=lambda: server.blocks_clerk)
    server.watcher = TaskWatcher(server.desk)
    server.preview = PreviewHub()
    server.checks = {}            # (项目, 第几次检测) -> 这次检测连通的进度
    server.abilities_search_fn = None
    server.abilities_runner = None
    server.abilities_codex_images = None
    server.abilities_jobs = {}
    server.blocks_open = None
    return server


def _exit_with_parent(server=None) -> None:
    parent = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != parent:  # 父进程没了（被系统接管），说明窗口已经关掉
            if server is not None:
                try:
                    server.preview.stop()
                except Exception:
                    pass
            os._exit(0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="超级助手流转面板的本机数据服务")
    parser.add_argument("--port", type=int, default=0, help="端口，0 表示自动挑一个空闲的")
    parser.add_argument("--watch-parent", action="store_true", help="启动它的程序退出后自己也退出")
    parser.add_argument("--token", default=os.environ.get("SUPERAGENT_CHAT_TOKEN"),
                        help="聊天口令（由桌面窗口生成；也可用环境变量 SUPERAGENT_CHAT_TOKEN）")
    args = parser.parse_args(argv)
    adopt_terminal_path()  # 双击打开时拿不到终端里的路径，Codex 等会被误报“没装”；从终端启动时这一步什么也不加
    server = make_server(args.port, token=args.token)
    print(f"PORT={server.server_address[1]}", flush=True)
    def _quit(*_):  # 窗口退出时发这个信号：不处理的话进程直接没了，预览会留下来
        try:
            server.preview.stop()
        finally:
            os._exit(0)

    signal.signal(signal.SIGTERM, _quit)
    if args.watch_parent:
        threading.Thread(target=_exit_with_parent, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.preview.stop()
        except Exception:
            pass
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
