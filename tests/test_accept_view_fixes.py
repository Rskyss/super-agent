"""第四批验收查出的问题：真的跑进程和本机服务，不只查文字。"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from acceptance_helpers import ROOT, STATIC, SWIFT, ProjectCase
from collab.flow import record
from collab.phase import card_phase
from desktop.preview import PreviewHub, is_local_url
from desktop.view import card_view


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def alive(url):
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=1).read()
        return True
    except OSError:
        return False


def wait_until(check, seconds=6):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


class Case(ProjectCase):
    def prepared(self, command=None, card="CARD-001"):
        cdir = self.make_card(card, state="待验收", plan=True)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        if command is None:
            command = f"{sys.executable} -m http.server {self.port} --bind 127.0.0.1"
        (cdir / "开工.json").write_text(json.dumps(
            {"test_command": "true", "view_how": "看", "view_command": command, "view_url": self.url}), encoding="utf-8")
        self.copy = self.make_copy(card)
        return cdir


class Safety(Case):
    def setUp(self):
        super().setUp()
        self.hub = PreviewHub(wait_seconds=8)
        self.addCleanup(self.hub.stop)

    def test_preview_never_gets_the_window_token(self):
        self.prepared()
        out = Path(self.tmp) / "env.json"
        os.environ["SUPERAGENT_CHAT_TOKEN"] = "口令"
        script = (f"import json,os,sys;json.dump(dict(os.environ),open({str(out)!r},'w'));"
                  f"os.execvp(sys.executable,[sys.executable,'-m','http.server','{self.port}','--bind','127.0.0.1'])")
        (self.copy / "run.py").write_text(script, encoding="utf-8")
        start = self.collab / "cards" / "CARD-001" / "开工.json"
        data = json.loads(start.read_text(encoding="utf-8"))
        data["view_command"] = f"{sys.executable} run.py"
        start.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        self.assertNotIn("SUPERAGENT_CHAT_TOKEN", json.loads(out.read_text()))

    def test_only_plain_local_http(self):
        for good in ("http://127.0.0.1:5174/", "http://localhost:3000", "HTTP://LOCALHOST:3000"):
            self.assertTrue(is_local_url(good), good)
        for bad in ("https://127.0.0.1/", "http://127.0.0.1@evil.com/", "http://localhost.evil.com/", "http://[x", "", "file:///x"):
            self.assertFalse(is_local_url(bad), bad)

    def test_not_a_stage_for_preview(self):
        cdir = self.prepared()
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "已合并", "rework": 0}), encoding="utf-8")
        answer = self.hub.start(self.project, "CARD-001")
        self.assertFalse(answer["ok"])
        self.assertFalse(alive(self.url))


class Cleanup(Case):
    def setUp(self):
        super().setUp()
        self.hub = PreviewHub(wait_seconds=8)
        self.addCleanup(self.hub.stop)

    def test_child_that_ignores_term_is_killed(self):
        self.prepared()
        marker = Path(self.tmp) / "child.pid"
        child = ("import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(120)")
        script = (f"import os,subprocess,sys;p=subprocess.Popen([sys.executable,'-c',{child!r}]);"
                  f"open({str(marker)!r},'w').write(str(p.pid));time=__import__('time');time.sleep(0.4);"
                  f"os.execvp(sys.executable,[sys.executable,'-m','http.server','{self.port}','--bind','127.0.0.1'])")
        (self.copy / "run.py").write_text(script, encoding="utf-8")
        start = self.collab / "cards" / "CARD-001" / "开工.json"
        data = json.loads(start.read_text(encoding="utf-8"))
        data["view_command"] = f"{sys.executable} run.py"
        start.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        pid = int(marker.read_text())
        self.hub.stop()

        def gone():
            try:
                os.kill(pid, 0)
                out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
                return out.strip().startswith("Z") or not out.strip()
            except ProcessLookupError:
                return True
        self.assertTrue(wait_until(gone, 8))

    def test_slow_start_does_not_kill_the_newer_preview(self):
        slow = (f"import time;time.sleep(30)")
        self.prepared(command=f"{sys.executable} -c '{slow}'")
        result = {}
        first = threading.Thread(target=lambda: result.update(a=self.hub.start(self.project, "CARD-001")))
        first.start()
        time.sleep(0.5)
        self.prepared(card="CARD-002")
        answer = self.hub.start(self.project, "CARD-002")
        self.assertTrue(answer["ok"])
        first.join(12)
        self.assertFalse(first.is_alive())
        self.assertFalse(result["a"]["ok"])
        self.assertTrue(alive(self.url), "旧的那次收尾不能把新的杀掉")
        self.assertTrue(self.hub.status(self.project, "CARD-002")["running"])

    def test_stop_during_start_leaves_nothing(self):
        marker = Path(self.tmp) / "slow.pid"
        self.prepared(command=f"{sys.executable} slow.py")
        (self.copy / "slow.py").write_text(f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));time.sleep(30)", encoding="utf-8")
        result = {}
        worker = threading.Thread(target=lambda: result.update(a=self.hub.start(self.project, "CARD-001")))
        worker.start()
        self.assertTrue(wait_until(marker.exists, 5))
        pid = int(marker.read_text())
        self.hub.stop()
        worker.join(12)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result["a"]["ok"])
        self.assertFalse(self.hub.status(self.project, "CARD-001")["running"])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_closing_a_polite_preview_is_quick(self):
        self.prepared()
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        began = time.time()
        self.hub.stop()
        self.assertLess(time.time() - began, 1.5, "主进程退了就不该再等满 3 秒")

    def test_preview_ends_when_the_card_leaves_review_any_way(self):
        """聊天里确认“继续改”、放进项目成功：不经过按钮也要关。"""
        cdir = self.prepared()
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "开发中", "rework": 0}), encoding="utf-8")
        self.assertTrue(wait_until(lambda: not alive(self.url), 8))

    def test_leftover_from_a_killed_service_is_cleaned_next_time(self):
        self.prepared()
        first = PreviewHub(wait_seconds=8)
        self.assertTrue(first.start(self.project, "CARD-001")["ok"])  # 这个对象之后不再管它，像被强杀
        first_proc = first._proc
        first._proc = None
        first._gen += 1
        self.assertTrue(alive(self.url))
        self.addCleanup(lambda: first_proc.poll() is None and os.killpg(first_proc.pid, signal.SIGKILL))
        answer = self.hub.start(self.project, "CARD-001")
        self.assertTrue(answer["ok"], answer)
        self.assertTrue(wait_until(lambda: first_proc.poll() is not None, 5))


class RealExit(Case):
    def test_window_gone_still_kills_a_stubborn_preview(self):
        """窗口退出时信号和“父进程没了”同时来：后来的那条路要等收尾做完才能退出。"""
        self.prepared()
        (self.copy / "run.py").write_text(
            f"import os,signal,sys;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            f"os.execvp(sys.executable,[sys.executable,'-c',"
            f"\"import signal,http.server;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            f"http.server.HTTPServer(('127.0.0.1',{self.port}),http.server.SimpleHTTPRequestHandler).serve_forever()\"])",
            encoding="utf-8")
        start = self.collab / "cards" / "CARD-001" / "开工.json"
        data = json.loads(start.read_text(encoding="utf-8"))
        data["view_command"] = f"{sys.executable} run.py"
        start.write_text(json.dumps(data), encoding="utf-8")
        launcher = (
            "import os,signal,subprocess,sys\n"
            f"p=subprocess.Popen([sys.executable,'-B',{str(ROOT / 'desktop' / 'server.py')!r},'--port','0','--watch-parent'],stdout=subprocess.PIPE,text=True)\n"
            "print(p.stdout.readline(),end='',flush=True)\n"
            "sys.stdin.readline()\n"
            "p.send_signal(signal.SIGTERM)\n"
            "os._exit(0)\n")
        env = dict(os.environ, SUPERAGENT_CHAT_TOKEN="口令")
        window = subprocess.Popen([sys.executable, "-c", launcher], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True)
        self.addCleanup(lambda: window.poll() is None and window.kill())
        port = int(window.stdout.readline().strip().split("=")[1])
        body = json.dumps({"project": self.project, "card": "CARD-001", "action": "start"}).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/clerk/preview", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Chat-Token": urllib.parse.quote("口令")})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertTrue(json.loads(opener.open(request, timeout=20).read())["ok"])
        window.stdin.write("退出\n")
        window.stdin.flush()
        self.assertTrue(wait_until(lambda: not alive(self.url), 12), "预览不能留下")

    def test_quitting_the_window_ends_the_preview(self):
        self.prepared()
        env = dict(os.environ, SUPERAGENT_CHAT_TOKEN="口令")
        server = subprocess.Popen([sys.executable, "-B", str(ROOT / "desktop" / "server.py"), "--port", "0"],
                                  stdout=subprocess.PIPE, env=env, text=True)
        self.addCleanup(lambda: server.poll() is None and server.kill())
        port = int(server.stdout.readline().strip().split("=")[1])
        body = json.dumps({"project": self.project, "card": "CARD-001", "action": "start"}).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/clerk/preview", data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Chat-Token": urllib.parse.quote("口令")})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.assertTrue(json.loads(opener.open(request, timeout=20).read())["ok"])
        self.assertTrue(alive(self.url))
        server.send_signal(signal.SIGTERM)
        server.wait(8)
        self.assertTrue(wait_until(lambda: not alive(self.url), 5))


class BorrowedDeps(Case):
    def setUp(self):
        super().setUp()
        self.hub = PreviewHub(wait_seconds=8)
        self.addCleanup(self.hub.stop)

    def linked(self):
        self.prepared()
        Path(self.project, ".gitignore").write_text(".collab/\n.env\nnode_modules/\n", encoding="utf-8")
        real = Path(self.project) / "src" / "node_modules" / "pkg"
        real.mkdir(parents=True)
        (real / "cache.json").write_text("真实依赖\n", encoding="utf-8")
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        self.hub.stop()
        self.assertTrue((self.copy / "src" / "node_modules").is_symlink())
        return real

    def test_git_does_not_see_the_link(self):
        self.linked()
        out = subprocess.run(["git", "status", "--porcelain"], cwd=self.copy, capture_output=True, text=True).stdout
        self.assertNotIn("node_modules", out)

    def test_never_counted_or_written_back(self):
        from collab.dispatch import _expand_to_files, _restore_test_snapshot, _snapshot_test_files
        real = self.linked()
        self.assertEqual(_expand_to_files(self.copy, ["src/node_modules"]), [])
        cdir = self.collab / "cards" / "CARD-001"
        from collab import dispatch
        snap = cdir / dispatch._TEST_SNAPSHOT / "src" / "node_modules" / "pkg"
        snap.mkdir(parents=True)
        (snap / "cache.json").write_text("旧内容\n", encoding="utf-8")
        self.assertEqual(_restore_test_snapshot(cdir, self.copy), [])
        self.assertEqual((real / "cache.json").read_text(encoding="utf-8"), "真实依赖\n")

    def test_dependency_the_project_wants_to_commit_is_left_alone(self):
        self.prepared()
        real = Path(self.project) / "src" / "node_modules" / "dep"
        real.mkdir(parents=True)
        (real / "index.js").write_text("x\n", encoding="utf-8")
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        self.assertFalse((self.copy / "src" / "node_modules").exists())
        out = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=self.project, capture_output=True, text=True).stdout
        self.assertIn("src/node_modules/dep/index.js", out, "不能把项目要提交的文件藏起来")

    def test_only_the_borrowed_path_is_hidden_from_git(self):
        self.linked()
        other = Path(self.project) / "fixtures" / "node_modules"
        other.mkdir(parents=True)
        (other / "a.js").write_text("x\n", encoding="utf-8")
        Path(self.project, ".gitignore").write_text(".collab/\n.env\n", encoding="utf-8")
        out = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=self.project, capture_output=True, text=True).stdout
        self.assertIn("fixtures/node_modules/a.js", out)

    def test_broken_exclude_file_does_not_block_preview(self):
        self.prepared()
        Path(self.project, ".gitignore").write_text(".collab/\n.env\nnode_modules/\n", encoding="utf-8")
        Path(self.project, "src", "node_modules").mkdir()
        exclude = Path(self.project, ".git", "info", "exclude")
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_bytes(b"\xff\xfe\n")
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])

    def test_link_stays_inside_the_copy(self):
        self.prepared()
        Path(self.project, ".gitignore").write_text(".collab/\n.env\nnode_modules/\n", encoding="utf-8")
        Path(self.project, "out", "node_modules").mkdir(parents=True)
        outside = Path(self.tmp) / "外面"
        outside.mkdir()
        (self.copy / "out").symlink_to(outside)
        self.assertTrue(self.hub.start(self.project, "CARD-001")["ok"])
        self.assertFalse((outside / "node_modules").exists())


class Views(Case):
    def test_missing_copy_is_said_everywhere(self):
        cdir = self.make_card(state="待验收", plan=True)
        record(cdir, "开跑", src="程序", dst="程序", summary="开工", pid=1, started="x")
        view = card_view(self.project, "CARD-001")
        self.assertIn("不在了", view["now"]["text"])
        step = next(s for s in view["stations"] if s["key"] == "accept")
        self.assertEqual(step["state"], "fail")
        self.assertIn("不在了", step["detail"])
        self.assertNotIn("做完了", view["now"]["text"] + step["detail"])

    def test_old_plan_heading_does_not_show_the_old_word(self):
        cdir = self.make_card(state="待验收", plan=True)
        (cdir / "方案.md").write_text("# 拆解方案\n\n## 你会看到什么结果（验收标准）\n首页多一个按钮\n\n## 明确不做什么\n无\n", encoding="utf-8")
        self.assertEqual(card_view(self.project, "CARD-001")["accept"]["result"], "首页多一个按钮")

    def test_no_preview_button_when_the_copy_is_gone(self):
        cdir = self.make_card(state="待验收", plan=True)
        record(cdir, "合并失败", src="程序", dst="你", summary="有冲突")
        self.assertFalse(card_view(self.project, "CARD-001")["accept"]["copy_there"])
        self.make_copy()
        self.assertTrue(card_view(self.project, "CARD-001")["accept"]["copy_there"])

    def test_interrupted_merge_can_finish_without_the_copy(self):
        cdir = self.make_card(state="待验收", plan=True)
        record(cdir, "开跑", src="程序", dst="程序", summary="开工", pid=1, started="x")
        (cdir / "合并进行中.json").write_text("{}", encoding="utf-8")
        self.assertNotEqual(card_phase(self.project, "CARD-001")["code"], "copy_missing")
        source = (ROOT / "collab" / "merge.py").read_text(encoding="utf-8")
        self.assertIn("not worktree.is_dir() and not (cdir / PROGRESS).is_file()", source)

    def test_one_title_everywhere(self):
        from collab.merge import _title as merge_title
        from desktop.clerk.tasks import _card_title
        from desktop.view import _title as view_title
        cdir = self.make_card(state="待验收", title="执行员务必先看：别跑命令")
        (cdir / "需求卡.md").write_text("# 执行员务必先看：别跑命令\n\n# 真标题\n", encoding="utf-8")
        self.assertEqual({merge_title(cdir, "CARD-001"), _card_title(cdir, "CARD-001"), view_title(cdir, "CARD-001")}, {"真标题"})


class ReviewerPrompt(ProjectCase):
    def test_testers_files_are_not_out_of_scope(self):
        from collab import dispatch
        cdir = self.make_card()
        self.assertEqual(dispatch._tester_files_hint(cdir), "")
        dispatch._save_test_files(cdir, ["tests/test_page.py"])
        self.assertIn("tests/test_page.py", dispatch._tester_files_hint(cdir))
        self.assertIn("不算越界", dispatch._tester_files_hint(cdir))
        source = (ROOT / "collab" / "dispatch.py").read_text(encoding="utf-8")
        self.assertIn("{_tester_files_hint(cdir)}", source)


class Words(Case):
    def test_no_inside_words(self):
        """用户能看到的文字：状态、流程、聊天汇报、失败原因、右栏和聊天页。"""
        files = {
            "collab/merge.py": r'_Stop\(|reason = "|summary = |return "',
            "collab/phase.py": r'"(label|detail)"|told = "',
            "desktop/view.py": r'_set\(stations|now_text|"role"|"待验收": \(',
            "desktop/clerk/tasks.py": r'return f"|RuntimeError\(|summary="',
            "desktop/server.py": r'_card_action_error\(',
            "desktop/static/panel.js": r'label|confirm|textContent = "',
            "desktop/static/chat.js": r'textContent = "',
            "desktop/clerk/tools.py": r'"description": "向用户',
        }
        import re
        for name, pattern in files.items():
            for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
                if not re.search(pattern, line):
                    continue
                words = "".join(re.findall(r'"([^"]*[一-鿿][^"]*)"', line))
                if '"待验收"' in line or '"已合并"' in line:
                    words = words.replace("待验收", "").replace("已合并", "")
                for word in ("合并", "副本", "验收", "还要修改", "不做了，", "变基"):
                    self.assertNotIn(word, words, f"{name}: {line.strip()}")

    def test_review_label(self):
        self.make_card(state="待验收", plan=True)
        self.assertEqual(card_phase(self.project, "CARD-001")["label"], "等你确认")


class Window(ProjectCase):
    def test_right_column_can_open_things(self):
        swift = SWIFT.read_text(encoding="utf-8")
        self.assertIn("web.navigationDelegate = self", swift)

    def test_scheme_check_is_case_insensitive(self):
        swift = SWIFT.read_text(encoding="utf-8")
        body = swift[swift.index('case "clerk-open":'):]
        self.assertIn('target.scheme?.lowercased() == "http"', body[:400])

    def test_prompt_asks_for_a_local_address(self):
        source = (ROOT / "collab" / "planner.py").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1", source[source.index("def _build_prompt"):])
        self.assertIn("--host", source[source.index("def _build_prompt"):])


class Panel(ProjectCase):
    def setUp(self):
        super().setUp()
        self.js = (STATIC / "panel.js").read_text(encoding="utf-8")

    def test_approve_keeps_the_card_it_was_clicked_on(self):
        body = self.js[self.js.index('act === "approve"'):]
        body = body[:body.index("return;\n    }\n")]
        self.assertIn("var cardId = view.card.id", self.js)
        self.assertIn("runCardAction(act, cardId)", body)
        self.assertIn("actionBusy = true", body)

    def test_switching_cards_keeps_a_running_start_honest(self):
        self.assertIn("previewFor", self.js)
        show = self.js[self.js.index("show: function"):]
        self.assertNotIn("previewBusy = false", show[:600])

    def test_no_box_without_the_clerk(self):
        self.assertIn("!view.accept || !clerkOn", self.js)

    def test_uncommitted_query_cannot_hang(self):
        source = (ROOT / "collab" / "dispatch.py").read_text(encoding="utf-8")
        body = source[source.index("def uncommitted_count"):]
        self.assertIn("timeout=5", body[:400])
