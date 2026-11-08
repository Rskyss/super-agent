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

from collab.check import check_team  # noqa: E402
from collab.cli import cmd_approve, cmd_confirm_profile  # noqa: E402
from collab.dispatch import uncommitted_count  # noqa: E402
from collab.config import (  # noqa: E402
    DEFAULT_MODELS,
    DEFAULT_TEAM,
    ConfigError,
    load_clerk,
    load_model_options,
    load_models,
    load_team,
    save_clerk,
    save_models,
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
from collab import blocks, blocks_check, blocks_env, blocks_install
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

    def _blocks_get(self, path: str, query: dict) -> None:
        project = str(blocks.home())
        if not Path(project).is_dir():
            Path(project).mkdir(parents=True, exist_ok=True)
        
        if path == "/api/blocks/state":
            clerk = load_clerk(project)
            account = account_for(clerk["provider"], clerk.get("base_url", "")) if clerk else ""
            self._json({"setting": clerk, "key_account": account,
                        "connected": self.server.blocks_clerk is not None,
                        "installing": blocks_install.installing()})
        elif path == "/api/blocks/list":
            all_items = blocks.list_cards(project)
            valid, broken = [], []
            for item in all_items:
                if "problem" in item:
                    broken.append({"id": item["id"], "problem": item["problem"]})
                else:
                    last = blocks.last_record(item["id"], project, type="check")
                    status = "never" if not last else ("ok" if last.get("ok") else "fail")
                    valid.append({
                        "id": item["id"], "name": item["name"], "kind": item["kind"],
                        "verified": blocks.is_verified(item), "status": status,
                        "reason": last.get("reason") if last else "",
                        "time": last.get("time") if last else "",
                        "seconds": last.get("seconds") if last else 0
                    })
            self._json({"blocks": valid, "broken": broken})
        elif path == "/api/blocks/detail":
            card_id = query.get("id", "")
            if not blocks._ID.fullmatch(card_id):
                self._json({"error": "编号不对"}, 400); return
            try:
                card = blocks.load_card(card_id, project)
                verified = blocks.is_verified(card)
                t_path = self.server.terminal_path() or ""
                m_path = merged_path(os.environ.get("PATH", ""), t_path)
                preview = blocks_check.preview(card, m_path) if card["kind"] == "cli" else ""
                last_check = blocks.last_record(card_id, project, type="check")
                last_install = blocks.last_record(card_id, project, type="install")
                self._json({"card": card, "verified": verified, "key_account": card.get("key_account", ""),
                            "preview": preview, "last_check": last_check, "last_install": last_install})
            except BlockError:
                self._json({"error": "没有这张卡"}, 404)
        elif path == "/api/blocks/job":
            job_id = query.get("id", "")
            job = self.server.blocks_jobs.get(job_id)
            if not job:
                self._json({"error": "没有这个任务"}, 404); return
            res = {"kind": job["kind"], "done": job["done"], "result": job.get("result"), "error": job.get("error") or ""}
            if job["kind"] == "install":
                res["tail"] = blocks_install.output_tail()
            self._json(res)
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _blocks_post(self, path: str, body: dict) -> None:
        project = str(blocks.home())
        if not Path(project).is_dir():
            Path(project).mkdir(parents=True, exist_ok=True)
        factory = self.server.blocks_client_factory or blocks_check._default_client
            
        if path == "/api/blocks/bind":
            prov, model, key = str(body.get("provider") or "").strip(), str(body.get("model") or "").strip(), str(body.get("api_key") or "").strip()
            if not key or not model or not prov:
                self._json({"error": "缺失参数"}, 400); return
            item = find_provider(prov)
            if not item:
                self._json({"error": "没有这个服务商"}, 400); return
            base_url = str(item.get("base_url") or body.get("base_url") or "").strip()
            if not base_url.startswith("https://"):
                self._json({"error": "地址不对"}, 400); return
            try:
                account = account_for(prov, base_url)
            except ValueError:
                self._json({"error": "地址不对"}, 400); return
            
            card = {"id": "clerk", "kind": "llm_api", "name": "行政", "provider": prov, "base_url": base_url, "model": model, "key_account": account}
            try:
                res = blocks_check.check_api(card, key, need_tools=True, record=False, client_factory=self.server.blocks_client_factory)
                if not res.get("ok"):
                    self._json({"ok": False, "reason": res.get("reason") or "", "how": res.get("how") or ""}); return
                save_clerk(project, {"provider": prov, "model": model, "base_url": base_url})
                self.server.blocks_clerk = factory(base_url, model, key)
                self._json({"ok": True, "key_account": account, "reason": res.get("reason") or "", "how": res.get("how") or ""})
            except Exception as e:
                self._json({"ok": False, "reason": str(e), "how": "检查设置后重试"})
        elif path == "/api/blocks/connect":
            key = str(body.get("api_key") or "").strip()
            if not key:
                self._json({"error": "没有密钥"}, 400); return
            clerk = load_clerk(project)
            if not clerk:
                self._json({"error": "还没绑定"}, 409); return
            self.server.blocks_clerk = factory(clerk["base_url"], clerk["model"], key)
            self._json({"ok": True})
        elif path == "/api/blocks/remove":
            card_id = str(body.get("id") or "")
            if not blocks._ID.fullmatch(card_id):
                self._json({"error": "编号不对"}, 400); return
            if blocks_check.checking(card_id):
                self._json({"error": "正在检测"}, 409); return
            try:
                blocks.remove_card(card_id, project)
                self._json({"ok": True})
            except BlockError:
                self._json({"error": "没有这张卡"}, 404)
        elif path == "/api/blocks/scan":
            job_id = uuid.uuid4().hex
            self.server.blocks_jobs[job_id] = {"kind": "scan", "done": False, "result": None, "error": ""}
            def run():
                try:
                    t_path = self.server.terminal_path() or ""
                    res = blocks_env.scan(root=project, app_path=os.environ.get("PATH", ""), terminal_path=t_path, record=True)
                    self.server.blocks_jobs[job_id]["result"] = res
                except Exception as e:
                    self.server.blocks_jobs[job_id]["error"] = str(e)
                finally:
                    self.server.blocks_jobs[job_id]["done"] = True
            threading.Thread(target=run, daemon=True).start()
            self._json({"job": job_id})
        elif path == "/api/blocks/check":
            card_id = str(body.get("id") or "")
            if not blocks._ID.fullmatch(card_id):
                self._json({"error": "编号不对"}, 400); return
            try:
                card = blocks.load_card(card_id, project)
            except BlockError:
                self._json({"error": "没有这张卡"}, 404); return
            
            approved = body.get("approved")
            api_key = body.get("api_key")
            if card["kind"] == "cli" and not blocks.is_verified(card) and not approved:
                self._json({"error": "需要确认完整命令"}, 400); return
            if card["kind"] == "llm_api" and not api_key:
                self._json({"error": "需要密钥"}, 400); return
                
            job_id = uuid.uuid4().hex
            self.server.blocks_jobs[job_id] = {"kind": "check", "done": False, "result": None, "error": ""}
            def run():
                try:
                    t_path = self.server.terminal_path() or ""
                    m_path = merged_path(os.environ.get("PATH", ""), t_path)
                    if card["kind"] == "cli":
                        res = blocks_check.check_cli(card, root=project, approved=approved, path=m_path, terminal_path=t_path)
                    else:
                        res = blocks_check.check_api(card, api_key, root=project, client_factory=self.server.blocks_client_factory)
                    self.server.blocks_jobs[job_id]["result"] = res
                except BlockError as e:
                    if "确认" in str(e) or "对不上" in str(e):
                        self.server.blocks_jobs[job_id]["result"] = {"ok": False, "status": "changed", "reason": "要跑的命令和你确认时看到的不一样", "how": "跟行政说重新提议，确认新的完整命令"}
                    else:
                        self.server.blocks_jobs[job_id]["error"] = str(e)
                except Exception as e:
                    self.server.blocks_jobs[job_id]["error"] = str(e)
                finally:
                    self.server.blocks_jobs[job_id]["done"] = True
            threading.Thread(target=run, daemon=True).start()
            self._json({"job": job_id})
        elif path == "/api/blocks/save":
            job_id = str(body.get("job") or "")
            job = self.server.blocks_jobs.get(job_id)
            if not job:
                self._json({"error": "没有这个任务"}, 404); return
            if job.get("saved"):
                card = job["card"]
                self._json({"ok": True, "id": card["id"], "key_account": card.get("key_account")}); return
            if job["kind"] != "check" or not job["done"] or not (job.get("result") or {}).get("ok") or not job.get("card"):
                self._json({"error": "检测没通过或还没完"}, 409); return
            card = job["card"]
            if blocks._card_path(card["id"], project).is_file() and not job.get("replaces"):
                self._json({"error": "要重新提议"}, 409); return
            blocks.save_card(card, project)
            job["saved"] = True
            self._json({"ok": True, "id": card["id"], "key_account": card.get("key_account")})
        else:
            self._json({"error": "没有这个地址"}, 404)

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
            except ConfigError as exc:
                team, models, problem = dict(DEFAULT_TEAM, dispatcher=None, configured=False), dict(DEFAULT_MODELS), str(exc)
            else:
                problem = ""
            self._json({"team": team, "models": models, "problem": problem, "git": (Path(project) / ".git").exists(),
                        "installed": (Path(project) / ".collab" / "cards").is_dir(), "options": _TEAM_OPTIONS,
                        "model_options": load_model_options()})
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
                save_team(project, body.get("team") or {})
                if body.get("models") is not None:
                    save_models(project, body.get("models") or {})
            except ConfigError as exc:
                self._json({"error": str(exc)}, 400)
                return
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
                    check_team(project, team=team, on_result=collect, roles=body.get("roles"), models=models)
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
        is_blocks = blocks_tools.is_blocks_home(project)
        if is_blocks:
            Path(project).mkdir(parents=True, exist_ok=True)
            if path == "/api/clerk/send":
                try:
                    self._json({"ok": True, "conversation": desk.send(project, conversation, str(body.get("text") or ""))})
                except RuntimeError as exc:
                    self._json({"error": str(exc)}, 409)
                return
            if path == "/api/clerk/proposal":
                pid = str(body.get("id") or "")
                decision = str(body.get("decision") or "")
                proposal = clerk_tasks.load_proposal(project, pid)
                if decision == "confirm" and proposal and proposal.get("state") == "open":
                    if proposal.get("kind") == "connect_api" and not str(body.get("api_key") or "").strip():
                        self._json({"error": "请先在密钥框里填写密钥"}, 400)
                        return
                    if proposal.get("kind") == "install" and blocks_install.installing():
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
                if decision == "confirm" and answer.get("state") == "confirmed" and answer.get("kind") in ("install", "connect", "connect_api"):
                    if answer.get("repeat"):
                        job_id = self.server.blocks_proposal_jobs.get(pid, "")
                        self._json({"ok": True, "card": answer.get("card") or "", "state": answer.get("state"), "job": job_id})
                        return
                    kind = answer.get("kind")
                    job_id = uuid.uuid4().hex
                    self.server.blocks_proposal_jobs[pid] = job_id
                    replaces = bool(proposal and proposal.get("replaces"))
                    if kind == "install":
                        install_id = answer["install"]
                        self.server.blocks_jobs[job_id] = {"kind": "install", "done": False, "result": None, "error": ""}
                        def run_inst():
                            try:
                                t_path = self.server.terminal_path() or ""
                                m_path = merged_path(os.environ.get("PATH", ""), t_path)
                                res = blocks_install.run_install(install_id, root=project, path=m_path)
                                if res.get("ok"):
                                    os.environ["PATH"] = m_path
                                self.server.blocks_jobs[job_id]["result"] = res
                            except Exception as e:
                                self.server.blocks_jobs[job_id]["error"] = str(e)
                            finally:
                                self.server.blocks_jobs[job_id]["done"] = True
                        threading.Thread(target=run_inst, daemon=True).start()
                    elif kind == "connect":
                        block = answer["block"]
                        approved = answer.get("approved")
                        self.server.blocks_jobs[job_id] = {"kind": "check", "done": False, "result": None, "error": "", "card": block, "replaces": replaces}
                        def run_cli():
                            try:
                                t_path = self.server.terminal_path() or ""
                                m_path = merged_path(os.environ.get("PATH", ""), t_path)
                                res = blocks_check.check_cli(block, root=project, approved=approved, path=m_path, terminal_path=t_path)
                                self.server.blocks_jobs[job_id]["result"] = res
                            except BlockError as e:
                                if "确认" in str(e) or "对不上" in str(e):
                                    self.server.blocks_jobs[job_id]["result"] = {"ok": False, "status": "changed", "reason": "要跑的命令和你确认时看到的不一样", "how": "跟行政说重新提议，确认新的完整命令"}
                                else:
                                    self.server.blocks_jobs[job_id]["error"] = str(e)
                            except Exception as e:
                                self.server.blocks_jobs[job_id]["error"] = str(e)
                            finally:
                                self.server.blocks_jobs[job_id]["done"] = True
                        threading.Thread(target=run_cli, daemon=True).start()
                    elif kind == "connect_api":
                        block = answer["block"]
                        api_key = str(body.get("api_key") or "").strip()
                        self.server.blocks_jobs[job_id] = {"kind": "check", "done": False, "result": None, "error": "", "card": block, "replaces": replaces}
                        def run_api():
                            try:
                                res = blocks_check.check_api(block, api_key, root=project, client_factory=self.server.blocks_client_factory)
                                self.server.blocks_jobs[job_id]["result"] = res
                            except Exception as e:
                                self.server.blocks_jobs[job_id]["error"] = str(e)
                            finally:
                                self.server.blocks_jobs[job_id]["done"] = True
                        threading.Thread(target=run_api, daemon=True).start()
                    self._json({"ok": True, "card": answer.get("card") or "", "state": answer.get("state"), "job": job_id})
                    return
                self._json({"ok": True, "card": answer.get("card") or "", "state": answer.get("state")})
                return

        # 原有逻辑
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
        if not url.path.startswith(("/api/chat/", "/api/team/", "/api/clerk/", "/api/blocks/")):
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
    server.blocks_client_factory = None
    server.terminal_path = terminal_path
    server.blocks_jobs = {}
    server.blocks_proposal_jobs = {}
    server.clerk_project = ""     # 这份连接属于哪个项目；空表示不拦（旧测试直接设 clerk）
    server.clerk_setting = None   # 这份连接用的服务商和模型，不含密钥
    server.desk = ClerkDesk(lambda: server.clerk, blocks_client=lambda: server.blocks_clerk)
    server.watcher = TaskWatcher(server.desk)
    server.preview = PreviewHub()
    server.checks = {}            # (项目, 第几次检测) -> 这次检测连通的进度
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
