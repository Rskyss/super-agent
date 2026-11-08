"""43 号单：打开改好的版本。用真的小网页服务测，不启动真实工具。"""
import json
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from acceptance_helpers import STATIC, SWIFT, ProjectCase, ServerCase
from desktop.preview import PreviewHub


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(url):
    try:
        urllib.request.urlopen(url, timeout=1).read()
        return True
    except OSError:
        return False


class Base:
    def prepared(self, command=None, url=None, how="看首页"):
        cdir = self.make_card(state="待验收", plan=True)
        self.port = free_port()
        self.url = url if url is not None else f"http://127.0.0.1:{self.port}/"
        if command is None:
            command = f"{sys.executable} -m http.server {self.port} --bind 127.0.0.1"
        (cdir / "开工.json").write_text(json.dumps(
            {"test_command": "true", "view_how": how, "view_command": command, "view_url": self.url}), encoding="utf-8")
        self.copy = Path(self.project).parent / (Path(self.project).name + "-worktrees") / "CARD-001"
        self.copy.mkdir(parents=True, exist_ok=True)
        (self.copy / "index.html").write_text("改好的版本", encoding="utf-8")
        return cdir


class Hub(Base, ProjectCase):
    def setUp(self):
        super().setUp()
        self.hub = PreviewHub(wait_seconds=8)
        self.addCleanup(self.hub.stop)

    def test_starts_in_the_copy_and_stops(self):
        self.prepared()
        answer = self.hub.start(self.project, "CARD-001")
        self.assertEqual((answer["ok"], answer["mode"], answer["url"]), (True, "web", self.url))
        self.assertIn("改好的版本", urllib.request.urlopen(self.url, timeout=2).read().decode("utf-8"))
        self.assertEqual(self.hub.status(self.project, "CARD-001"), {"running": True, "url": self.url})
        self.hub.stop()
        self.assertFalse(alive(self.url))
        self.assertEqual(self.hub.status(self.project, "CARD-001")["running"], False)

    def test_stop_ends_the_whole_group(self):
        marker = f"{self.tmp}/child.pid"
        script = (f"import os,subprocess,sys;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                  f"open({marker!r},'w').write(str(p.pid));os.execvp(sys.executable,[sys.executable,'-m','http.server','PORT','--bind','127.0.0.1'])")
        self.prepared()
        (self.collab / "cards" / "CARD-001" / "开工.json").write_text(json.dumps({
            "test_command": "true", "view_how": "", "view_url": self.url,
            "view_command": f"{sys.executable} run.py"}), encoding="utf-8")
        (self.copy / "run.py").write_text(script.replace("PORT", str(self.port)), encoding="utf-8")
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        child = int(Path(marker).read_text())
        self.hub.stop()
        time.sleep(0.5)
        import os
        with self.assertRaises(ProcessLookupError):
            for _ in range(20):
                os.kill(child, 0)
                time.sleep(0.2)

    def test_one_at_a_time(self):
        self.prepared()
        self.hub.start(self.project, "CARD-001")
        first = self.url
        self.prepared()
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        self.assertFalse(alive(first))
        self.assertTrue(alive(self.url))

    def test_no_command_opens_the_folder(self):
        self.prepared(command="", url="")
        answer = self.hub.start(self.project, "CARD-001")
        self.assertEqual((answer["ok"], answer["mode"]), (True, "folder"))
        self.assertEqual(Path(answer["path"]).resolve(), self.copy.resolve())

    def test_not_local_address_is_never_opened(self):
        self.prepared(url="https://example.com/")
        answer = self.hub.start(self.project, "CARD-001")
        self.assertEqual(answer["mode"], "folder")

    def test_start_failure_says_so_and_offers_the_folder(self):
        self.prepared(command=f"{sys.executable} -c 'raise SystemExit(3)'")
        answer = self.hub.start(self.project, "CARD-001")
        self.assertFalse(answer["ok"])
        self.assertTrue(answer["reason"])
        self.assertEqual(Path(answer["path"]).resolve(), self.copy.resolve())

    def test_address_already_in_use_is_not_mistaken_for_the_new_version(self):
        self.prepared()

        class Other(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write("别的程序".encode("utf-8"))

            def log_message(self, *args):
                pass

        other = HTTPServer(("127.0.0.1", self.port), Other)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        answer = self.hub.start(self.project, "CARD-001")
        self.assertFalse(answer["ok"])
        self.assertIn("占用", answer["reason"])

    def test_missing_copy(self):
        cdir = self.prepared()
        import shutil
        shutil.rmtree(self.copy)
        answer = self.hub.start(self.project, "CARD-001")
        self.assertFalse(answer["ok"])
        self.assertIn("不在了", answer["reason"])
        self.assertTrue(cdir.is_dir())

    def test_borrows_node_modules(self):
        self.prepared()
        Path(self.project, ".gitignore").write_text(".collab/\n.env\nnode_modules/\n", encoding="utf-8")
        self.git("init", "-q")  # 副本是假的文件夹时，这条测试只看链接；git 判断在项目里做
        real = Path(self.project) / "grove" / "node_modules"
        real.mkdir(parents=True)
        (self.copy / "grove").mkdir()
        Path(self.project, "skip", "node_modules").mkdir(parents=True)
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        link = self.copy / "grove" / "node_modules"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), real.resolve())
        self.assertFalse((self.copy / "skip").exists(), "副本里没有的文件夹不要造出来")

    def test_runs_only_what_is_in_the_record(self):
        source = (Path(__file__).parent.parent / "desktop" / "preview.py").read_text(encoding="utf-8")
        self.assertIn("开工.json", source)
        self.assertNotIn("shell=True", source)


class Api(Base, ServerCase):
    def test_start_status_stop_needs_token(self):
        self.prepared()
        self.addCleanup(self.server.preview.stop)
        body = {"project": self.project, "card": "CARD-001", "action": "start"}
        self.assertEqual(self.post("/api/clerk/preview", body, token="错的")[0], 403)
        status, answer = self.post("/api/clerk/preview", body)
        self.assertEqual((status, answer["ok"]), (200, True))
        status, answer = self.get("/api/clerk/preview", project=self.project, card="CARD-001", token="口令")
        self.assertEqual((status, answer["running"]), (200, True))
        status, _ = self.post("/api/clerk/preview", dict(body, action="stop"))
        self.assertEqual(status, 200)
        self.assertFalse(alive(self.url))

    def test_bad_card_name_is_refused(self):
        status, _ = self.post("/api/clerk/preview", {"project": self.project, "card": "../x", "action": "start"})
        self.assertEqual(status, 400)

    def test_giving_up_closes_the_preview(self):
        self.prepared()
        self.addCleanup(self.server.preview.stop)
        self.post("/api/clerk/preview", {"project": self.project, "card": "CARD-001", "action": "start"})
        self.assertTrue(alive(self.url))
        status, _ = self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": "discard"})
        self.assertEqual(status, 200)
        self.assertFalse(alive(self.url))


class Window(ProjectCase):
    def setUp(self):
        super().setUp()
        self.swift = SWIFT.read_text(encoding="utf-8")
        self.js = (STATIC / "panel.js").read_text(encoding="utf-8")

    def test_opening_goes_through_the_window_and_is_limited(self):
        self.assertIn("superagent://clerk-open?", self.js)
        body = self.swift[self.swift.index('case "clerk-open":'):]
        body = body[:body.index("case ", 20)]
        self.assertIn("127.0.0.1", body)
        self.assertIn("localhost", body)
        self.assertIn("-worktrees", body)
        self.assertIn("NSWorkspace.shared.open", body)

    def test_switching_project_closes_the_preview(self):
        body = self.swift[self.swift.index("func switchTo(_ url: URL)"):self.swift.index("func remember(_ url: URL)")]
        self.assertIn("/api/clerk/preview", body)

    def test_panel_has_the_review_area(self):
        html = (STATIC / "panel.html").read_text(encoding="utf-8")
        for name in ('id="acceptBox"', 'id="acceptResult"', 'id="acceptHow"', 'id="previewBtn"'):
            self.assertIn(name, html)
        for words in ("做完了，请你确认", "你应该看到", "在哪看", "打开改好的版本", "关闭预览", "/api/clerk/preview"):
            self.assertIn(words, html + self.js)
