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
from collab.config import (  # noqa: E402
    DEFAULT_MODELS,
    DEFAULT_TEAM,
    ConfigError,
    load_model_options,
    load_models,
    load_team,
    save_models,
    save_team,
)
from desktop.chat import ChatHub  # noqa: E402
from desktop.clerk.client import DEFAULT_BASE_URL, DEFAULT_MODEL, ModelClient, ModelError  # noqa: E402
from desktop.clerk.desk import ClerkDesk  # noqa: E402
from desktop.shell_path import adopt_terminal_path  # noqa: E402
from desktop.view import card_view, event_text, list_cards  # noqa: E402

_TOOLS = [{"value": "codex", "label": "Codex"}, {"value": "cursor", "label": "Cursor"}, {"value": "agy", "label": "agy"}]
_TEAM_OPTIONS = {"dispatcher": _TOOLS, "worker": _TOOLS,
                 "reviewer": _TOOLS + [{"value": "none", "label": "不复查"}]}

STATIC = Path(__file__).resolve().parent / "static"
_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".webp": "image/webp"}
_MAX_BODY = 1024 * 1024  # 聊天消息最多 1MB，够用了


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

    def _static(self, rel: str) -> None:
        target = (STATIC / rel).resolve()
        if STATIC not in target.parents or not target.is_file():
            self._json({"error": "没有这个文件"}, 404)
            return
        self._send(200, target.read_bytes(), _TYPES.get(target.suffix, "application/octet-stream"))

    def do_GET(self):  # noqa: N802  名字由 http.server 规定
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        project = query.get("project", "")
        try:
            if url.path == "/api/health":
                self._json({"ok": True})
            elif url.path == "/api/view":
                if not project or not Path(project).is_dir():
                    self._json({"error": "项目文件夹不存在"}, 400)
                    return
                session = getattr(self.server, "hub", None) and self.server.hub.get(project)
                self._json(card_view(project, query.get("card") or None, dispatcher=query.get("dispatcher"),
                                     chat_session=session))
            elif url.path == "/api/cards":
                self._json(list_cards(project) if project and Path(project).is_dir() else [])
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
                        "installed": (Path(project) / ".collab").is_dir(), "options": _TEAM_OPTIONS,
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
            if not (Path(project) / ".collab").is_dir():
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
        if path == "/api/clerk/list":
            self._json(desk.list(project))
        elif path == "/api/clerk/events":
            wait = min(float(query.get("wait", "20") or 20), 25)
            self._json(desk.events(project, query.get("conversation", ""), int(query.get("since", "0") or 0), wait))
        else:
            self._json({"error": "没有这个地址"}, 404)

    def _clerk_post(self, path: str, body: dict) -> None:
        """窗口把行政人员的密钥交给本机服务：只放内存，不写文件，也不回传。"""
        desk, project, conversation = self.server.desk, str(body.get("project") or ""), str(body.get("conversation") or "")
        if path in ("/api/clerk/send", "/api/clerk/stop", "/api/clerk/focus"):
            if not project or not Path(project).is_dir():
                self._json({"error": "项目文件夹不存在"}, 400)
                return
            try:
                if path == "/api/clerk/send":
                    self._json({"ok": True, "conversation": desk.send(project, conversation, str(body.get("text") or ""))})
                    return
                (desk.stop if path == "/api/clerk/stop" else desk.focus)(project, conversation)
                self._json({"ok": True})
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 409)
            return
        client = ModelClient(str(body.get("base_url") or DEFAULT_BASE_URL), str(body.get("model") or DEFAULT_MODEL),
                             str(body.get("api_key") or ""), timeout=30)
        if path == "/api/clerk/test":
            try:
                client.chat([{"role": "user", "content": "只回答：好"}])
                self._json({"ok": True})
            except ModelError as exc:
                self._json({"ok": False, "error": str(exc)})
        elif path == "/api/clerk/config":
            self.server.clerk = client
            self._json({"ok": True})
        else:
            self._json({"error": "没有这个地址"}, 404)

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        if not url.path.startswith(("/api/chat/", "/api/team/", "/api/clerk/")):
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
    server.desk = ClerkDesk(lambda: server.clerk)
    server.checks = {}            # (项目, 第几次检测) -> 这次检测连通的进度
    return server


def _exit_with_parent() -> None:
    parent = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != parent:  # 父进程没了（被系统接管），说明窗口已经关掉
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
    if args.watch_parent:
        threading.Thread(target=_exit_with_parent, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
