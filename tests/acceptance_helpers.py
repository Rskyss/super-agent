"""验收测试共用的工具：假模型、假命令、临时项目。全部不花额度。Cursor 不要修改本文件。"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COLLAB = ROOT / "collab"
STATIC = ROOT / "desktop" / "static"
SWIFT = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOOD_PLAN = {
    "summary": "把按钮改成蓝色", "steps": ["改样式"], "result": "按钮变蓝", "not_do": "不改别的页面",
    "questions": [], "allowed": ["src/app.py"], "test_command": "true", "profile": "",
}


class ScriptedClient:
    """按顺序说话的假模型。每一步是 {"text": 文字, "tool_calls": [{"id","name","arguments"}]}。"""

    def __init__(self, steps=None, default="好的。"):
        self.steps = list(steps or [])
        self.default = default
        self.calls = []  # 每次 (messages, tools)
        self.lock = threading.Lock()

    def chat(self, messages, on_text=None, tools=None):
        text_all = json.dumps(messages, ensure_ascii=False)
        if "用不超过 12 个字" in text_all:
            return {"text": "标题", "tool_calls": [], "tokens": 1}
        with self.lock:
            self.calls.append((json.loads(json.dumps(messages, ensure_ascii=False)), tools))
            step = self.steps.pop(0) if self.steps else {"text": self.default}
        if callable(step):
            step = step()
        text = step.get("text", "")
        if text and on_text:
            on_text(text)
        return {"text": text, "tool_calls": step.get("tool_calls", []), "tokens": 1}


def tool_call(name, arguments=None, call_id="c1"):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False)
    return {"id": call_id, "name": name, "arguments": raw}


def wait_until(check, timeout=10, message="等不到想要的结果"):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(message)


def wait_idle(desk, project, conversation, timeout=8):
    def done():
        answer = desk.events(project, conversation, 0, wait=0)
        return answer if answer["open"] and not answer["busy"] else None
    return wait_until(done, timeout, "行政一直没有回完")["events"]


def write_script(path, body):
    path = Path(path)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_CODEX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args, ensure_ascii=False) + "\n")
if "app-server" in args:
    sys.exit(1)
if "--output-schema" in args:
    sys.stdin.read()   # 不关闭输入就会卡在这里，和真实 Codex 一样
    out = args[args.index("-o") + 1]
    answer = os.environ.get("FAKE_PLAN")
    if answer is None:
        sys.exit(1)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(answer)
    sys.exit(0)
os.makedirs("tests", exist_ok=True)   # 假测试员真的交出一个测试文件
with open("tests/test_fake.py", "a", encoding="utf-8") as fh:
    fh.write("# 假测试\n")
print("写好了")
'''


class ProjectCase(unittest.TestCase):
    """临时 git 项目，加上一个只放假命令的 bin 目录，保证不会碰到真实工具。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = str(Path(self.tmp) / "项目")
        Path(self.project).mkdir()
        self.git("init", "-q")
        (Path(self.project) / "src").mkdir()
        (Path(self.project) / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        (Path(self.project) / ".gitignore").write_text(".collab/\n.env\n", encoding="utf-8")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self.codex_log = Path(self.tmp) / "codex.log"
        codex = self.bin / "codex"
        codex.write_text(FAKE_CODEX, encoding="utf-8")
        codex.chmod(0o755)
        write_script(self.bin / "agent", "echo 写好了\n")
        write_script(self.bin / "agy", "echo 结论：通过\n")
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ["PATH"] = f"{self.bin}:{os.environ['PATH']}"
        os.environ["FAKE_CODEX_LOG"] = str(self.codex_log)
        os.environ.pop("FAKE_PLAN", None)
        # 假 Codex 分不出“备交接资料”和别的活：这类整条流程的测试不备资料（交接资料自己的测试在 test_flowfix2_dispatch）
        os.environ["SUPERAGENT_NO_HANDOVER"] = "1"
        os.environ.pop("CODEX_SANDBOX", None)

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.project, check=True, capture_output=True, text=True)

    def make_copy(self, card_id="CARD-001"):
        """真实施工时的副本。待验收的卡没有它，会显示“改好的版本不在了”。"""
        folder = Path(self.project).parent / (Path(self.project).name + "-worktrees") / card_id
        if not folder.exists():
            folder.parent.mkdir(parents=True, exist_ok=True)
            self.git("worktree", "add", "-q", "-b", f"card/{card_id}", str(folder))
        return folder

    @property
    def collab(self):
        return Path(self.project) / ".collab"

    def make_card(self, card_id="CARD-001", state="待对齐", title="改按钮", allowed=True, plan=False,
                  source="", reported=None):
        from collab.flow import record
        from collab.state import Card
        from collab.workspace import init_collab, render_board
        init_collab(self.project)
        cdir = self.collab / "cards" / card_id
        cdir.mkdir(parents=True, exist_ok=True)
        text = f"# {title}\n\n## 要做什么\n改颜色\n"
        if allowed:
            text += "\n## 允许改动的范围\n- src/app.py\n"
        (cdir / "需求卡.md").write_text(text, encoding="utf-8")
        Card(card_id, state=state).save(cdir / "状态.json")
        record(cdir, "需求", src="你", dst="调度员", summary=title, text=text)
        if plan:
            (cdir / "方案.md").write_text(
                "# 拆解方案\n\n## 要做什么（一句话）\n改颜色\n\n## 拆成几步，每步做什么、产出什么\n1. 改\n\n"
                "## 你会看到什么结果\n变蓝\n\n## 明确不做什么\n无\n\n## 需要你确认的问题\n无\n",
                encoding="utf-8")
            (cdir / "开工.json").write_text(json.dumps({"test_command": "true"}), encoding="utf-8")
            record(cdir, "方案", src="Codex", dst="你", summary="改颜色")
        if source:
            (cdir / "来源.json").write_text(json.dumps({"conversation": source}), encoding="utf-8")
        if reported:
            (cdir / "已汇报.json").write_text(json.dumps({"code": reported}), encoding="utf-8")
        render_board(self.collab)
        return cdir

    def state(self, card_id="CARD-001"):
        path = self.collab / "cards" / card_id / "状态.json"
        return json.loads(path.read_text(encoding="utf-8"))["state"]

    def flow_kinds(self, card_id="CARD-001"):
        from collab.flow import read_flow
        return [e.get("kind") for e in read_flow(self.collab / "cards" / card_id)]

    def cli(self, *args, timeout=30):
        """跑 collab 命令。输入管道一直开着：命令里没关输入就会卡住。"""
        proc = subprocess.Popen([sys.executable, "-B", str(COLLAB), *args], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError("命令一直不结束，可能没有关闭输入")
        return proc.returncode, out


class ServerCase(ProjectCase):
    def setUp(self):
        super().setUp()
        from desktop.chat import ChatHub
        from desktop.server import make_server
        self.server = make_server(port=0, token="口令", hub=ChatHub(factories={}))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.closed = False
        self.addCleanup(self.close_server)

    def close_server(self):
        if not self.closed:
            self.closed = True
            self.server.shutdown()
            self.server.server_close()

    def post(self, path, body, token="口令"):
        headers = {"Content-Type": "application/json", "X-Chat-Token": urllib.parse.quote(token)}
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as answer:
                return answer.status, json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path, **query):
        request = urllib.request.Request(self.base + path + "?" + urllib.parse.urlencode(query))
        try:
            with urllib.request.urlopen(request, timeout=15) as answer:
                return answer.status, json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())


def run_node(script):
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, cwd=str(ROOT))


def needs_node():
    return unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
