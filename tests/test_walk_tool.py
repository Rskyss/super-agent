"""开发助手走查工具（tests/walk_tool.py）的检查。P1 W0-1，Claude 写。

走查工具给改界面的卡用：起一份只在临时文件夹里的后台服务、在后台浏览器里按真实窗口三栏的宽度打开页面、
点、截图、写步骤清单。这里检查：真能截出图、清单写对；点不到东西、后台出错、页面脚本报错、要看的字没出现都会失败；
后台服务找不到真实的干活工具和钥匙串、不碰真实设置；走查被强停时后台服务跟着退出。
需要本机装 Chrome 和自动点页面的工具（playwright）；缺了就是环境问题，测试直接报错说明缺什么，不跳过。
"""
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import walk_tool

ROOT = Path(__file__).resolve().parents[1]
DESIGN = "tests/fixtures/walk_cards/设计图样例.png"   # 代码里真有的一张 png，当设计图用


def png_size(path):
    data = Path(path).read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} 不是 png"
    return struct.unpack(">II", data[16:24])


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class WalkToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.shots = Path(cls.tmp.name) / "shots"
        cls.project = walk_tool.make_project(Path(cls.tmp.name) / "proj", cards=["CARD-021"])
        cls.real_home_marker = Path.home() / ".config" / "super-agent"
        cls.before = cls.real_home_marker.stat().st_mtime if cls.real_home_marker.exists() else None
        os.environ["WALK_SHOTS_DIR"] = str(cls.shots)
        try:
            cls.walk = walk_tool.Walk(cls.project)
            cls.walk.start()
            cls.home_during = cls.walk.home
            cls.info = cls.walk.server_info()
            cls.panel = cls.walk.open("panel", card="CARD-021")
            cls.walk.shot(cls.panel, "右栏打开卡片", design=DESIGN, expect="右栏停下提示")
            cls.panel_text = cls.panel.inner_text("body")
            cls.nav = cls.walk.open("nav", card="CARD-021")
            cls.walk.shot(cls.nav, "左栏")
            cls.card_text = cls.walk.open_card("CARD-021").inner_text("body")
            cls.started = time.monotonic()
            cls.missing_error = None
            try:
                cls.panel.click("text=这个按钮根本不存在")
            except Exception as exc:  # noqa: BLE001 — 记下来给测试看
                cls.missing_error = exc
            cls.missing_seconds = time.monotonic() - cls.started
            cls.walk.finish()
        finally:
            os.environ.pop("WALK_SHOTS_DIR", None)
            if getattr(cls, "walk", None):
                cls.walk.stop()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def manifest(self):
        return json.loads((self.shots / walk_tool.MANIFEST).read_text(encoding="utf-8"))

    def test_shots_and_manifest(self):
        items = self.manifest()
        self.assertEqual([i["步骤"] for i in items], ["右栏打开卡片", "左栏"])
        self.assertEqual(items[0]["截图"], "01-右栏打开卡片.png")
        self.assertEqual(items[1]["截图"], "02-左栏.png")
        self.assertEqual(items[0]["设计图"], DESIGN)
        self.assertEqual(items[1]["设计图"], "")
        for item in items:
            self.assertTrue((self.shots / item["截图"]).is_file())

    def test_widths_follow_window(self):
        # 外壳默认：窗口 1280，左栏 264、右栏 372，中间再扣两条 1 像素分隔线 = 642
        self.assertEqual({k: v[0] for k, v in walk_tool.PANES.items()}, {"nav": 264, "chat": 642, "panel": 372})
        self.assertEqual(png_size(self.shots / "01-右栏打开卡片.png")[0], 372)
        self.assertEqual(png_size(self.shots / "02-左栏.png")[0], 264)

    def test_panel_shows_real_card(self):
        # 卡片标题来自卡片数据，不是页面上写死的字：画出来了才说明卡片真加载了
        self.assertIn("右栏停下提示", self.panel_text)

    def test_panes_open_like_window_with_clerk(self):
        self.assertIn("clerk=1", self.walk.url("panel", "CARD-021"))
        nav_url = self.walk.url("nav", "CARD-021")
        self.assertIn("mode=card", nav_url)
        self.assertIn("clerk=1", nav_url)
        self.assertIn("mode=main", self.walk.url("nav"))

    def test_chat_opens_card_like_window(self):
        self.assertIn("右栏停下提示", self.card_text)
        self.assertNotIn("调度员", self.card_text)

    def test_missing_button_fails_fast(self):
        self.assertIsNotNone(self.missing_error, "点不存在的按钮应该报错")
        self.assertLess(self.missing_seconds, walk_tool.ACTION_TIMEOUT_MS / 1000 + 5)

    def test_real_settings_untouched(self):
        after = self.real_home_marker.stat().st_mtime if self.real_home_marker.exists() else None
        self.assertEqual(after, self.before)
        self.assertTrue(self.home_during)
        self.assertNotEqual(Path(self.home_during).resolve(), Path.home().resolve())

    def test_server_cannot_find_real_tools_or_keychain(self):
        # 后台服务启动后不许从登录环境把 PATH 补回来；干活工具和钥匙串命令都只能找到假的
        env = self.walk.server_env
        fake = env["PATH"].split(":")[0]
        self.assertEqual(self.info["path"], env["PATH"])
        for name in walk_tool.BLOCKED:
            self.assertTrue(str(self.info["which"][name] or "").startswith(fake), f"{name}：{self.info['which'][name]}")
        self.assertTrue(env["SUPERAGENT_TENCENT_KEY_FILE"].startswith(self.home_during))
        self.assertTrue(env["SUPERAGENT_QWEN_KEY_FILE"].startswith(self.home_during))
        self.assertFalse([k for k in env if "TOKEN" in k.upper() or k.upper().endswith("_KEY")])


class BrokenPageTest(unittest.TestCase):
    def walk_and_expect_failure(self, act):
        with tempfile.TemporaryDirectory() as tmp:
            project = walk_tool.make_project(Path(tmp) / "proj", cards=["CARD-021"])
            with self.assertRaises(walk_tool.WalkError):
                with walk_tool.Walk(project, shots_dir=Path(tmp) / "shots") as w:
                    act(w)

    def test_backend_error_fails_walk(self):
        # 后台出错（右栏取卡片数据 500）：不能截一张“断开了”的图当成功
        def act(w):
            page = w.open("panel", card="CARD-021",
                          prepare=lambda p: p.route("**/api/view**", lambda r: r.fulfill(status=500, body="{}")))
            w.shot(page, "右栏")
        self.walk_and_expect_failure(act)

    def test_expected_text_missing_fails(self):
        def act(w):
            w.shot(w.open("panel", card="CARD-021"), "右栏", expect="页面上根本没有这句话")
        self.walk_and_expect_failure(act)

    def test_script_error_fails_walk(self):
        def act(w):
            page = w.open("panel", card="CARD-021")
            page.evaluate("setTimeout(() => { throw new Error('页面脚本坏了') }, 0)")
            w.shot(page, "右栏")
        self.walk_and_expect_failure(act)


class MoreBrokenPageTest(unittest.TestCase):
    walk_and_expect_failure = BrokenPageTest.walk_and_expect_failure

    def test_backend_gone_fails_walk(self):
        # 后台整个没了（连接被拒、没有回应）：页面显示“断开了”，旧内容还在，也不能算走成（W0 复查第 2 轮）
        def act(w):
            page = w.open("panel", card="CARD-021")
            w._server.kill()
            w._server.wait(timeout=5)
            page.wait_for_timeout(3000)
            w.shot(page, "右栏", expect="右栏停下提示")
        self.walk_and_expect_failure(act)

    def test_expect_text_clipped_by_fold_fails(self):
        # 卡片原文默认收起两行：被收起框裁掉、截图里看不到的字，不能算“看到了”
        def act(w):
            w.shot(w.open_card("CARD-021"), "中间打开卡片", expect="验收命令全部通过")
        self.walk_and_expect_failure(act)


class ExpectPreciseTest(unittest.TestCase):
    """expect 只认截图里真看得见的那几个字（W0 复查第 3 轮）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.walk = walk_tool.Walk(walk_tool.make_project(Path(cls.tmp.name) / "proj"), shots_dir=Path(cls.tmp.name) / "shots")
        cls.walk.start()
        try:
            cls._prepare()
        except BaseException:   # 准备失败也要把浏览器和后台收拾掉，不然连累后面的检查
            cls.walk.stop()
            cls.tmp.cleanup()
            raise

    @classmethod
    def _prepare(cls):
        cls.page = cls.walk.open("chat")
        # 一段长文字放进只有一行高、超出就裁掉的框里：开头看得见，末尾那句看不见
        cls.page.evaluate("""() => {
            const box = document.createElement("div");
            box.style.cssText = "position:fixed;top:0;left:0;width:300px;height:20px;overflow:hidden;background:#000;z-index:99";
            box.innerHTML = "<p style='margin:0'>开头这几个字看得见。" + "中间很长的一段话。".repeat(12) + "末尾这句被裁掉了</p>";
            document.body.appendChild(box);
            const hidden = document.createElement("div");
            hidden.innerHTML = "<span style='display:none'>同名按钮甲</span><span style='display:none'>同名按钮甲</span>";
            document.body.appendChild(hidden);
            const shown = document.createElement("div");
            shown.style.cssText = "position:fixed;top:200px;left:0;z-index:99;background:#000";
            shown.textContent = "同名按钮甲";
            document.body.appendChild(shown);
            // 一句话跨了几行，框只有一行高：第一行露出来，后面被裁掉
            const wrap = document.createElement("div");
            wrap.style.cssText = "position:fixed;top:300px;left:0;width:120px;height:20px;line-height:20px;overflow:hidden;font-size:14px;background:#000;z-index:99";
            wrap.textContent = "前面几个字跨行的这句验收语一直写到第三行才结束";
            document.body.appendChild(wrap);
            // 同一段文字里同一句出现两次：第一次滚出框外，第二次完整看得见
            const roll = document.createElement("div");
            roll.id = "roll";
            roll.style.cssText = "position:fixed;top:400px;left:0;width:300px;height:20px;line-height:20px;overflow:auto;white-space:pre-line;font-size:14px;background:#000;z-index:99";
            roll.textContent = ["重复出现的句子", "第二行", "第三行", "重复出现的句子"].join(String.fromCharCode(10));
            document.body.appendChild(roll);
            roll.scrollTop = roll.scrollHeight;
        }""")

    @classmethod
    def tearDownClass(cls):
        cls.walk.stop()
        cls.tmp.cleanup()

    def test_clipped_end_of_long_paragraph_not_seen(self):
        with self.assertRaises(walk_tool.WalkError):
            self.walk.shot(self.page, "裁掉的末句", expect="末尾这句被裁掉了")

    def test_visible_start_of_paragraph_seen(self):
        self.walk.shot(self.page, "看得见的开头", expect="开头这几个字看得见")

    def test_visible_one_among_hidden_duplicates_seen(self):
        self.walk.shot(self.page, "同名的可见按钮", expect="同名按钮甲")

    def test_text_straddling_fold_not_seen(self):
        with self.assertRaises(walk_tool.WalkError):
            self.walk.shot(self.page, "跨行被裁", expect="跨行的这句验收语一直写到第三行才结束")

    def test_second_occurrence_visible_seen(self):
        self.walk.shot(self.page, "第二次出现看得见", expect="重复出现的句子")

    def test_chat_with_card_must_use_open_card(self):
        with self.assertRaises(walk_tool.WalkError):
            self.walk.open("chat", card="CARD-021")


class WaitingCardTest(unittest.TestCase):
    def test_waiting_card_shows_accept_buttons(self):
        # 照手册复制卡片资料、把卡改成待验收：右栏要显示“等你确认”和两个按钮，不是“改好的版本不在了”
        with tempfile.TemporaryDirectory() as tmp:
            project = walk_tool.make_project(Path(tmp) / "proj", cards=["CARD-021"])
            state = project / ".collab" / "cards" / "CARD-021" / "状态.json"
            state.write_text(json.dumps({"card_id": "CARD-021", "state": "待验收", "rework": 0}), encoding="utf-8")
            with walk_tool.Walk(project, shots_dir=Path(tmp) / "shots") as w:
                page = w.open("panel", card="CARD-021")
                w.shot(page, "待验收的右栏", expect="确认没问题，放进项目")
                text = page.inner_text("body")
            self.assertNotIn("改好的版本不在了", text)
            self.assertIn("不满意，继续改", text)


class StaleHomeTest(unittest.TestCase):
    def test_old_walk_homes_are_cleaned(self):
        # 走查被强停时来不及删临时设置文件夹；下次起走查时把一小时前留下的删掉
        old = Path(tempfile.mkdtemp(prefix="walk-home-"))
        stamp = time.time() - 2 * 3600
        os.utime(old, (stamp, stamp))
        fresh = Path(tempfile.mkdtemp(prefix="walk-home-"))
        try:
            walk_tool.clean_stale_homes()
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
        finally:
            for d in (old, fresh):
                if d.exists():
                    os.rmdir(d)


class KilledWalkTest(unittest.TestCase):
    def test_server_exits_when_walk_is_killed(self):
        # 程序跑走查超时会强停走查命令那一组进程：后台服务不能留下来
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import sys, time; sys.path.insert(0, %r); import walk_tool; "
                    "w = walk_tool.Walk(walk_tool.make_project(%r), shots_dir=%r).start(); "
                    "print(w.server_pid, flush=True); time.sleep(120)") % (
                str(ROOT / "tests"), str(Path(tmp) / "proj"), str(Path(tmp) / "shots"))
            proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                                    start_new_session=True)
            pid = int(proc.stdout.readline().strip())
            self.assertTrue(alive(pid))
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            proc.stdout.close()
            deadline = time.monotonic() + 10
            while alive(pid) and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertFalse(alive(pid), "走查被强停后，后台服务还在")


class EnvErrorExitTest(unittest.TestCase):
    def test_environment_problem_exits_127(self):
        # 环境问题（缺 playwright、打不开 Chrome、本机禁止绑定端口）退出码 127：程序判“环境出错”，不怪写代码的
        code = "import sys; sys.path.insert(0, %r); import walk_tool; raise walk_tool.WalkEnvError('打不开本机 Chrome')" % (
            str(ROOT / "tests"))
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 127, proc.stderr)
        self.assertIn("打不开本机 Chrome", proc.stderr)

    def test_bind_permission_error_is_env_error_127(self):
        # 沙箱或系统禁止绑定 127.0.0.1：后台在 server_bind 报 EPERM/EACCES 时算环境出错（退出 127），不怪写代码的
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "副本"
            (src / "desktop").mkdir(parents=True)
            (src / "desktop" / "__init__.py").write_text("", encoding="utf-8")
            (src / "desktop" / "server.py").write_text(
                "import errno\n"
                "class LocalServer:\n"
                "    def server_bind(self):\n"
                "        raise PermissionError(errno.EPERM, 'Operation not permitted')\n"
                "def make_server(*a, **k):\n"
                "    s = LocalServer()\n"
                "    s.server_bind()\n"
                "    return s\n"
                "def main(argv=None):\n"
                "    make_server()\n"
                "    return 0\n",
                encoding="utf-8",
            )
            proj = walk_tool.make_project(Path(tmp) / "proj")
            code = (
                "import sys; sys.path.insert(0, %r); import walk_tool; "
                "walk_tool.Walk(%r, shots_dir=%r, source_root=%r).start()"
            ) % (str(ROOT / "tests"), str(proj), str(Path(tmp) / "shots"), str(src))
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 127, proc.stderr)
            self.assertIn("WalkEnvError", proc.stderr)
            self.assertIn("127.0.0.1", proc.stderr)

    def test_non_bind_permission_error_is_still_code_problem(self):
        # 副本业务代码自己报 PermissionError（不是绑定 127.0.0.1 端口被拒）：照旧算代码问题（退出 1）
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "副本"
            (src / "desktop").mkdir(parents=True)
            (src / "desktop" / "__init__.py").write_text("", encoding="utf-8")
            (src / "desktop" / "server.py").write_text(
                "import errno\n"
                "class LocalServer:\n"
                "    def server_bind(self): pass\n"
                "def make_server(*a, **k):\n"
                "    raise PermissionError(errno.EACCES, '业务文件没权限')\n"
                "def main(argv=None):\n"
                "    make_server()\n"
                "    return 0\n",
                encoding="utf-8",
            )
            walk = walk_tool.Walk(walk_tool.make_project(Path(tmp) / "proj"), shots_dir=Path(tmp) / "shots",
                                  source_root=src)
            with self.assertRaises(walk_tool.WalkError) as caught:
                walk.start()
            self.assertNotIsInstance(caught.exception, walk_tool.WalkEnvError)
            self.assertIn("业务文件没权限", str(caught.exception))

    def test_broken_server_is_code_problem_not_environment(self):
        # 卡片副本里的代码把后台改坏、起不来：算代码问题（退回写代码的），不是环境出错；报错要带出来
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "副本"
            (src / "desktop").mkdir(parents=True)
            (src / "desktop" / "__init__.py").write_text("", encoding="utf-8")
            (src / "desktop" / "server.py").write_text("from collab.state import 没有这个名字\n", encoding="utf-8")
            walk = walk_tool.Walk(walk_tool.make_project(Path(tmp) / "proj"), shots_dir=Path(tmp) / "shots",
                                  source_root=src)
            with self.assertRaises(walk_tool.WalkError) as caught:
                walk.start()
            self.assertNotIsInstance(caught.exception, walk_tool.WalkEnvError)
            self.assertIn("没有这个名字", str(caught.exception))

    def test_page_problem_still_exits_1(self):
        code = "import sys; sys.path.insert(0, %r); import walk_tool; raise walk_tool.WalkError('页面上没看到')" % (
            str(ROOT / "tests"))
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 1, proc.stderr)


class StepWrapperTest(unittest.TestCase):
    def test_step_wraps_missing_button_as_single_line_walk_error(self):
        # 用 with w.step(...) 包住点击：点不到按钮时退出码 1，最后一行非空行直接带单行步骤名，不丢步骤
        with tempfile.TemporaryDirectory() as tmp:
            proj = walk_tool.make_project(Path(tmp) / "proj", cards=["CARD-021"])
            code = (
                "import sys; sys.path.insert(0, %r); import walk_tool\n"
                "with walk_tool.Walk(%r, shots_dir=%r) as w:\n"
                "    page = w.open('panel', card='CARD-021')\n"
                "    with w.step('点不存在按钮'):\n"
                "        page.click('text=这个按钮根本不存在')\n"
                "        w.shot(page, '点不存在按钮')\n"
            ) % (str(ROOT / "tests"), str(proj), str(Path(tmp) / "shots"))
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            last_line = [ln for ln in proc.stderr.splitlines() if ln.strip()][-1]
            self.assertTrue(last_line.startswith("walk_tool.WalkError:"), last_line)
            self.assertIn("“点不存在按钮”这一步", last_line)

    def test_step_rejects_bad_names_and_preserves_env_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            walk = walk_tool.Walk(walk_tool.make_project(Path(tmp) / "proj"), shots_dir=Path(tmp) / "shots")
            for bad in ("", "   ", "pytest", "带 空格", "带/斜杠", "带“引号”", "很长很长很长很长很长很长很长很长超过二十个字"):
                with self.subTest(bad=bad):
                    with self.assertRaises(walk_tool.WalkError):
                        with walk.step(bad):
                            pass
            with self.assertRaises(walk_tool.WalkEnvError):
                with walk.step("正常步骤"):
                    raise walk_tool.WalkEnvError("环境坏了")


class DesignAndShotsTest(unittest.TestCase):
    def test_unknown_card_is_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(walk_tool.WalkError):
                walk_tool.make_project(Path(tmp) / "proj", cards=["CARD-999"])

    def test_design_is_png_in_code_tree_not_fake_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            walk = walk_tool.Walk(walk_tool.make_project(Path(tmp) / "proj"), shots_dir=Path(tmp) / "shots")
            # 设计图在项目代码里找（施工单写的就是项目里的相对路径），不在只放了卡片资料的假项目里找
            self.assertEqual(walk.check_design(DESIGN), DESIGN)
            for bad in ("../外面.png", "docs/没有这张.png", "tests/walk_tool.py"):
                with self.assertRaises(walk_tool.WalkError):
                    walk.check_design(bad)

    def test_default_shots_dir_survives(self):
        # 没给 WALK_SHOTS_DIR 时截图要留下来：打印出来的位置事后打得开
        old = os.environ.pop("WALK_SHOTS_DIR", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                walk = walk_tool.Walk(walk_tool.make_project(Path(tmp) / "proj"))
                where = walk.shots_dir
                del walk
            self.assertTrue(where.is_dir())
            os.rmdir(where)
        finally:
            if old is not None:
                os.environ["WALK_SHOTS_DIR"] = old


if __name__ == "__main__":
    unittest.main()
