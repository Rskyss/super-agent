"""53 号单复查后的修改：安装执行。

去掉“额外环境设置”（能借 BASH_ENV、NODE_OPTIONS 这类设置跑任意代码），只留装进临时文件夹的 prefix；
Homebrew 不自动更新、不顺手清理别的软件；要管理员权限按 npm、brew 分开认；超时先请它自己退出；
长输出不卡死；先打码再截断；实时输出不出乱码、不撑爆；显示输出出错不影响安装；限时要是正数。
"""
import inspect
from pathlib import Path
import json
import threading
import time
import unittest

from collab import blocks, blocks_install
from collab.blocks import BlockError
from collab.blocks_check import _mask
from collab.blocks_env import _version_line
from test_acceptance_53_blocks_install import RunCase, script

SECRET = "sk-abcdefghijklmnopqrstuvwxyz"


class EnvTest(RunCase):
    def fake(self, name, body):
        return script(self.bin, name, f'echo "$@" > "{self.ran}"\n' + body)

    def test_no_extra_env(self):
        self.assertNotIn("env_extra", inspect.signature(blocks_install.run_install).parameters,
                         "额外环境设置能跑任意代码，去掉")

    def test_prefix_for_npm(self):
        prefix = self.base / "prefix"
        env = self.base / "env.txt"
        self.fake("npm", f'echo "$npm_config_prefix" > "{env}"; /bin/mkdir -p "{prefix}/bin"; '
                         f'printf "#!/bin/sh\\necho 1.0\\n" > "{prefix}/bin/codex"; /bin/chmod +x "{prefix}/bin/codex"')
        result = self.install(prefix=str(prefix))
        self.assertEqual(env.read_text().strip(), str(prefix))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["found"], "装进 prefix 的，在 prefix 里找")

    def test_prefix_must_be_absolute(self):
        self.fake("npm", "exit 0")
        for bad in ("prefix", "", "../x", 5):
            with self.subTest(bad=bad), self.assertRaises(BlockError):
                self.install(prefix=bad)
        self.assertFalse(self.ran.exists())

    def test_prefix_only_for_npm(self):
        self.fake("brew", "exit 0")
        with self.assertRaises(BlockError):
            self.install("node-brew", prefix=str(self.base / "prefix"))
        self.assertFalse(self.ran.exists())

    def test_brew_does_not_update_or_clean_up(self):
        env = self.base / "env.txt"
        self.fake("brew", f'echo "$HOMEBREW_NO_AUTO_UPDATE $HOMEBREW_NO_INSTALL_CLEANUP" > "{env}"; exit 0')
        self.install("node-brew")
        self.assertEqual(env.read_text().split(), ["1", "1"])

    def test_brew_old_catalog_hint(self):
        self.fake("brew", 'echo "Error: No available formula with the name \\"node\\"." >&2; exit 1')
        result = self.install("node-brew")
        self.assertEqual(result["status"], "fail")
        self.assertIn("brew update", result["how"], "关了自动更新，目录太旧时要提示手动更新一次")


class AdminTest(RunCase):
    def fake(self, name, out, code=1):
        return script(self.bin, name, f"echo {json.dumps(out)} >&2; exit {code}")

    def test_brew_sudo_warning_is_not_admin(self):
        self.fake("brew", "Warning: Do not run brew with sudo\nError: Download failed")
        self.assertEqual(self.install("node-brew")["status"], "fail")

    def test_npm_sudo_word_is_not_admin(self):
        self.fake("npm", "npm ERR! 404 Not Found - GET https://registry.npmjs.org/sudo-prompt")
        self.assertEqual(self.install()["status"], "fail")

    def test_npm_eacces(self):
        self.fake("npm", "npm ERR! Error: EACCES: permission denied, mkdir '/usr/local/lib/node_modules'")
        result = self.install()
        self.assertEqual(result["status"], "needs_admin")
        self.assertIn("Homebrew", result["how"])

    def test_brew_permission(self):
        self.fake("brew", "Error: Permission denied @ dir_s_mkdir - /opt/homebrew/Cellar")
        result = self.install("node-brew")
        self.assertEqual(result["status"], "needs_admin")
        self.assertNotIn("用 Homebrew", result["how"], "本来就是 Homebrew，不能让人改用 Homebrew")
        self.assertIn("终端", result["how"])


class StopTest(RunCase):
    def test_asks_to_quit_first(self):
        marker = self.base / "term.txt"
        script(self.bin, "npm", f"trap 'echo term > \"{marker}\"; exit 1' TERM\n/bin/sleep 30 &\nwait")
        result = self.install(timeout=2)  # 给够时间让假安装程序先设好“收到退出请求”的处理
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(marker.exists(), "先请它自己退出，让它收拾好")

    def test_force_after_grace(self):
        old = blocks_install.TERM_GRACE
        blocks_install.TERM_GRACE = 0.5
        try:
            script(self.bin, "npm", "trap '' TERM\nexec /bin/sleep 30")
            started = time.monotonic()
            result = self.install(timeout=1)
        finally:
            blocks_install.TERM_GRACE = old
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result["status"], "timeout")

    def test_timeout_must_be_positive(self):
        script(self.bin, "npm", f'touch "{self.ran}"')
        for bad in (0, -1, "5", True, None, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(BlockError):
                self.install(timeout=bad)
        self.assertFalse(self.ran.exists())


class OutputTest(RunCase):
    def test_mask_before_cut(self):
        script(self.bin, "npm", f'echo "{"x" * 105} {SECRET}"; exit 1')
        result = self.install()
        self.assertNotIn("sk-abcdefgh", json.dumps(result), "截在密钥中间，留下的半截认不出来")


class MaskTest(unittest.TestCase):
    def test_long_text_is_fast(self):
        for text in ("a" * 200000, "_" * 200000, "a." * 100000, ("bearer " + " " * 1000) * 100):
            with self.subTest(text=text[:10]):
                started = time.monotonic()
                _mask(text)
                _version_line(text)
                self.assertLess(time.monotonic() - started, 1)

    def test_email_still_masked(self):
        self.assertNotIn("someone@example.com", _mask("by someone@example.com"))
        self.assertNotIn("someone@example.com", _version_line("1.2.3 someone@example.com"))

    def test_npm_tokens(self):
        token = "npm_" + "Ab1" * 12
        for text in (f"//registry.npmjs.org/:_authToken={token}", f"token {token}"):
            with self.subTest(text=text):
                self.assertNotIn(token, _mask(text))
        self.assertIn("npm_config_prefix", _mask("npm_config_prefix=/tmp/x"))



class ReviewTwoTest(RunCase):
    """第二轮复查：输出很多时丢行、限时失效；长行分段打码漏出密钥；用 prefix 时找错地方。"""

    def test_prefix_found_only_in_prefix(self):
        script(self.bin, "codex", "echo 1.0")
        script(self.bin, "npm", "exit 0")
        result = self.install(prefix=str(self.base / "prefix"))
        self.assertEqual(result["status"], "ok", "装进 prefix 时，看的是 prefix 里有没有，不是全局")
        self.assertFalse(result["found"], "prefix 里什么都没装上，就不算找到")

    def test_prefix_without_path_separator(self):
        script(self.bin, "npm", "exit 0")
        with self.assertRaises(BlockError):
            self.install(prefix=str(self.base / "a") + ":" + str(self.bin))

    def test_brew_no_hints(self):
        env = self.base / "env.txt"
        script(self.bin, "brew", f'echo "$HOMEBREW_NO_ENV_HINTS" > "{env}"')
        self.install("node-brew")
        self.assertEqual(env.read_text().strip(), "1")

    def test_waits_before_force(self):
        old = blocks_install.TERM_GRACE
        blocks_install.TERM_GRACE = 1.5
        marker = self.base / "term.txt"
        try:
            script(self.bin, "npm", f"trap 'echo term >> \"{marker}\"' TERM\nwhile :; do /bin/sleep 0.1; done")
            started = time.monotonic()
            result = self.install(timeout=2)
            spent = time.monotonic() - started
        finally:
            blocks_install.TERM_GRACE = old
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(marker.exists(), "先请它退出")
        self.assertGreaterEqual(spent, 2 + 1.4, "不理会的，等够宽限时间才强关")
        self.assertLess(spent, 2 + 1.5 + 3)



class ReviewThreeTest(RunCase):
    """第三轮复查：显示慢时限时仍超出；最后处理不完时结尾丢掉、显示半截行；跳过时半行处理。"""

    def test_jump_drops_half_line_only(self):
        tail = blocks_install._tail_lines
        self.assertEqual(tail(b"abc\ndef\nghi\n", 0), ["abc", "def", "ghi"], "从头开始读，第一行是完整的")
        self.assertEqual(tail(b"c\ndef\nghi", 2, before=b"b"), ["def", "ghi"], "从一行中间开始，那半行不要")
        self.assertEqual(tail(b"def\nghi", 4, before=b"\n"), ["def", "ghi"], "正好从换行后开始，第一行是完整的")

    def test_judge_tail_skips_half_line(self):
        secret = "sk-" + "Q" * 70000
        script(self.bin, "npm", f"/usr/bin/printf 'start\\n{secret}'; exit 1")
        result = self.install()
        self.assertEqual(result["status"], "fail")
        self.assertNotIn("QQQQQQQQ", json.dumps(result), "最后 64KB 从密钥中间开始时，那半截不能写进说明")



class EmptyFolderTest(RunCase):
    """查版本、读帮助、安装都在新建的空文件夹里跑：工具不会把文件写进超级助手所在的文件夹，
    npm 也不会读到那个文件夹里的设置（比如改下载来源的 .npmrc）。"""

    def test_install_runs_in_empty_folder(self):
        where = self.base / "where.txt"
        script(self.bin, "npm", f'pwd > "{where}"; /bin/ls -A >> "{where}"; /usr/bin/touch left.txt')
        import os
        before = set(os.listdir(os.getcwd()))
        self.install()
        lines = where.read_text().split()
        self.assertNotEqual(Path(lines[0]).resolve(), Path(os.getcwd()).resolve())
        self.assertEqual(lines[1:], [], "文件夹是空的")
        self.assertFalse(Path(lines[0]).exists(), "跑完就删")
        self.assertEqual(set(os.listdir(os.getcwd())), before)

    def test_probe_runs_in_empty_folder(self):
        import os
        from collab import blocks_env
        tool = script(self.bin, "probe", "pwd; /usr/bin/touch left.txt && echo touched")
        before = set(os.listdir(os.getcwd()))
        _, out = blocks_env.run_command([str(tool)], str(self.bin), 5)
        folder, touched = out.split()
        self.assertEqual(touched, "touched")
        self.assertNotEqual(Path(folder).resolve(), Path(os.getcwd()).resolve())
        self.assertFalse(Path(folder).exists(), "跑完就删")
        self.assertEqual(set(os.listdir(os.getcwd())), before)



class ReviewFourTest(RunCase):
    """第四轮复查：临时文件夹删不掉时丢结果；Basic 写法的密钥。"""

    # 另开一组、不停往当前文件夹写文件的后台小程序：整组关不到它，删临时文件夹时会撞上
    DAEMON = ("/usr/bin/python3 -c 'import os,time\nif os.fork(): os._exit(0)\nos.setsid()\n"
              "if os.fork(): os._exit(0)\nend = time.time() + 1.5\ni = 0\n"
              "while time.time() < end:\n    try:\n        open(\"f%d\" % i, \"w\").close(); i += 1\n"
              "    except OSError:\n        break'")

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp_root = Path(tempfile.gettempdir())
        self.before = set(self.tmp_root.glob("积木库*"))

    def tearDown(self):
        import os
        import shutil
        for leftover in set(self.tmp_root.glob("积木库*")) - self.before:  # 后台小程序写完后留下的临时文件夹
            try:
                names = os.listdir(leftover)
            except OSError:  # 别的测试刚好把它删了
                continue
            if all(name.startswith("f") and name[1:].isdigit() for name in names):  # 只删后台小程序写的
                shutil.rmtree(leftover, ignore_errors=True)
        super().tearDown()

    def test_leftover_writer_does_not_break_install(self):
        script(self.bin, "npm", self.DAEMON + "\necho added")
        result = self.install()
        self.assertTrue(result["ok"], result)
        self.assertIsNotNone(blocks.last_record("codex", root=self.root, type="install"))
        time.sleep(1.8)

    def test_leftover_writer_does_not_break_probe(self):
        from collab import blocks_env
        tool = script(self.bin, "probe", self.DAEMON + "\necho v1.0")
        code, out = blocks_env.run_command([str(tool)], str(self.bin), 5)
        self.assertEqual(out.strip(), "v1.0")
        time.sleep(1.8)

    def test_basic_auth_masked(self):
        self.assertNotIn("dXNlcjpwYXNz", _mask("Authorization: Basic dXNlcjpwYXNz"))



class TailTest(RunCase):
    """页面隔一会儿取最后几行（第四轮复查后改的做法：不再把每一行推给页面）。"""

    def tail_after(self, body, **over):
        script(self.bin, "npm", body)
        result = self.install(**over)
        return result, blocks_install.output_tail()

    def test_last_lines_after_install(self):
        result, lines = self.tail_after("/usr/bin/seq 1 100000; echo done", timeout=60)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(lines), blocks_install.SHOW_TAIL_LINES)
        self.assertEqual(lines[-1], "done")
        self.assertEqual(lines[:-1], [str(i) for i in range(100001 - (len(lines) - 1), 100001)], "不丢行、不串行")

    def test_tail_while_running(self):
        marker = self.base / "go"
        half = self.base / "half"
        body = (f'/usr/bin/seq 1 20; printf "token sk-abcdefghij"; /usr/bin/touch "{half}"; '
                f'while [ ! -e "{marker}" ]; do /bin/sleep 0.05; done; printf "klmnopqrstuvwxyz\\n"; echo end')
        script(self.bin, "npm", body)
        results = []
        worker = threading.Thread(target=lambda: results.append(self.install(timeout=20)))
        worker.start()
        seen = []
        for _ in range(100):
            seen = blocks_install.output_tail()
            if half.exists():  # 半截密钥已经写出去了再取
                seen = blocks_install.output_tail()
                break
            time.sleep(0.05)
        marker.write_text("x")
        worker.join()
        self.assertIn("20", seen, "装的过程中就能取到")
        self.assertNotIn("sk-abcdefghij", " ".join(seen), "还没写完的那行可能是半截密钥，先不显示")
        after = blocks_install.output_tail()
        self.assertEqual(after[-1], "end")
        self.assertNotIn("sk-abcdefghijklmnop", " ".join(after))

    def test_last_line_without_newline(self):
        _, lines = self.tail_after("printf 'a\\nlast'")
        self.assertEqual(lines, ["a", "last"])

    def test_progress_lines(self):
        _, lines = self.tail_after("printf 'progress 10%%\\rprogress 100%%\\n'")
        self.assertEqual(lines, ["progress 100%"], "原地刷新的进度只留最后一帧，不占满 50 行")

    def test_chinese_written_in_two_parts(self):
        _, lines = self.tail_after("printf '\\345\\256'; /bin/sleep 0.3; "
                                   "printf '\\211\\350\\243\\205\\345\\256\\214\\346\\210\\220\\n'")
        self.assertEqual(lines, ["安装完成"])

    def test_long_lines_cut_and_masked(self):
        early = " ".join("sk-" + "E" * 60 for _ in range(5))  # 前面的密钥打码后会变短
        for secret in (SECRET, "api_key=SuperSecretValue123", "npm_" + "Ab1" * 12, "ghp_" + "A1" * 20):
            for offset in (1990, 1996, 2186, 2195, 2198):
                with self.subTest(secret=secret[:6], offset=offset):
                    for prefix in ("", early + " "):
                        data = self.base / "out.txt"
                        data.write_text(prefix + "x" * (offset - len(prefix)) + secret + "y" * 3000 + "\nend\n")
                        _, lines = self.tail_after(f'/bin/cat "{data}"')
                        joined = " ".join(lines)
                        self.assertNotIn(secret[:10], joined)
                        self.assertNotIn(secret[-12:], joined)
                        self.assertEqual(lines[-1], "end")
                        self.assertLessEqual(max(map(len, lines)), blocks_install.LINE_LIMIT)

    def test_very_long_last_line(self):
        _, lines = self.tail_after("/usr/bin/head -c 30000 /dev/zero | /usr/bin/tr '\\0' a")
        self.assertEqual(lines, [blocks_install.LONG_LINE])

    def test_spam_still_times_out(self):
        script(self.bin, "npm", "while :; do echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; done")
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                blocks_install.output_tail()
                time.sleep(0.01)
        poller = threading.Thread(target=poll)
        poller.start()
        started = time.monotonic()
        try:
            result = self.install(timeout=2)
        finally:
            stop.set()
            poller.join()
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.monotonic() - started, 2 + blocks_install.TERM_GRACE + 2)

    def test_previous_output_not_shown(self):
        self.tail_after('echo "上一次的输出"')
        (self.bin / "npm").unlink()
        result = self.install()
        self.assertEqual(result["status"], "missing_prereq")
        self.assertEqual(blocks_install.output_tail(), [])

    def test_long_line_without_spaces(self):
        _, lines = self.tail_after("/usr/bin/head -c 3000 /dev/zero | /usr/bin/tr '\\0' a; echo; "
                                   "/usr/bin/printf '%.0s汉' $(/usr/bin/seq 1 2500); echo; echo end")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "…", "一长串英文截过后不能变成空白行")
        self.assertTrue(lines[1].startswith("汉汉汉") and lines[1].endswith("…"), "汉字不是密钥，不用去掉")
        self.assertEqual(lines[2], "end")

    def test_nothing_printed(self):
        _, lines = self.tail_after("exit 0")
        self.assertEqual(lines, [])


class LongTextTest(unittest.TestCase):
    def test_token_like_repeats_are_fast(self):
        for text in ("eyJ" * 70000, "eyJ" + "a" * 200000 + ".", "AKID" * 50000, "bce-v3/" * 30000):
            with self.subTest(text=text[:8]):
                started = time.monotonic()
                _mask(text)
                _version_line(text)
                self.assertLess(time.monotonic() - started, 1)

    def test_read_help_long_output_fast(self):
        import os
        import tempfile
        from desktop.clerk import blocks_tools
        with tempfile.TemporaryDirectory() as tmp:
            script(Path(tmp), "longhelp", "printf 'eyJ%.0s' $(/usr/bin/seq 1 60000)")
            old = os.environ["PATH"]
            os.environ["PATH"] = tmp + os.pathsep + old
            try:
                started = time.monotonic()
                text = blocks_tools.run(None, "read_help", {"command": "longhelp"})
            finally:
                os.environ["PATH"] = old
        self.assertLess(time.monotonic() - started, 3)
        self.assertLessEqual(len(text), blocks_tools._HELP_LIMIT)


if __name__ == "__main__":
    unittest.main()
