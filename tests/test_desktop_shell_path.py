"""双击打开的窗口拿不到终端里的“找命令路径”：Codex 装在只有终端知道的地方，检测就误报“没装”
（2026-11-05 用户实测）。本机服务启动时向用户的终端要一份路径补上。全部用假终端，不碰真的配置。"""
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path

from desktop.shell_path import adopt_terminal_path, merged_path, terminal_path


def _script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class ShellPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.only_terminal = self.root / "npm-global" / "bin"  # 只有终端知道的地方
        self.only_terminal.mkdir(parents=True)
        _script(self.only_terminal / "codex", "exit 0\n")
        # 假终端：先说几句无关的话（像开场欢迎语），再在它自己的路径下执行交给它的那条命令
        self.shell = _script(self.root / "fake-zsh",
                             'echo "欢迎回来"\n'
                             f'PATH="{self.only_terminal}:/usr/bin:/bin"; export PATH\n'
                             'for last; do :; done\n'
                             'eval "$last"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_the_terminal_path_despite_chatter(self):
        self.assertEqual(terminal_path(self.shell), f"{self.only_terminal}:/usr/bin:/bin")

    def test_a_tool_only_the_terminal_knows_is_found_afterwards(self):
        env = {"PATH": "/usr/bin:/bin"}
        self.assertIsNone(shutil.which("codex", path=env["PATH"]))  # 补之前：误报“没装”的原样
        self.assertTrue(adopt_terminal_path(env, shell=self.shell))
        self.assertEqual(shutil.which("codex", path=env["PATH"]), str(self.only_terminal / "codex"))

    def test_existing_order_is_kept_so_python_and_others_do_not_change(self):
        self.assertEqual(merged_path("/usr/bin:/bin", "/a:/usr/bin::/b:/a"), "/usr/bin:/bin:/a:/b")

    def test_only_the_path_is_taken_not_secrets_from_the_terminal(self):
        shell = _script(self.root / "secret-zsh",
                        'SMTP_PASS=不该带走; export SMTP_PASS\nPATH="/x:/usr/bin"; export PATH\n'
                        'for last; do :; done\neval "$last"\n')
        env = {"PATH": "/usr/bin"}
        adopt_terminal_path(env, shell=shell)
        self.assertEqual(env, {"PATH": "/usr/bin:/x"})

    def test_a_hanging_terminal_gives_up_quickly_and_changes_nothing(self):
        shell = _script(self.root / "slow-zsh", "sleep 30\n")
        env = {"PATH": "/usr/bin"}
        started = time.monotonic()
        self.assertFalse(adopt_terminal_path(env, shell=shell, timeout=1))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(env, {"PATH": "/usr/bin"})

    def test_a_hanging_terminal_with_a_leftover_background_job_still_gives_up(self):
        shell = _script(self.root / "bg-zsh", "sleep 30 &\nsleep 30\n")
        started = time.monotonic()
        self.assertIsNone(terminal_path(shell, timeout=1))
        self.assertLess(time.monotonic() - started, 5)

    def test_missing_or_silent_terminal_changes_nothing(self):
        env = {"PATH": "/usr/bin"}
        self.assertFalse(adopt_terminal_path(env, shell=str(self.root / "没有这个终端")))
        self.assertFalse(adopt_terminal_path(env, shell=_script(self.root / "mute-zsh", "exit 0\n")))
        self.assertEqual(env, {"PATH": "/usr/bin"})


if __name__ == "__main__":
    unittest.main()
