import io
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from collab import blocks, blocks_check, blocks_env, blocks_install, cli
from collab.workspace import ensure_ignored
from desktop.chat import ChatSession
from desktop.clerk import blocks_tools, desk
from desktop.dispatchers.codex import CodexDispatcher


class UserFacingWordsTest(unittest.TestCase):
    def test_tool_installed_but_missing_in_app(self):
        card = {"id": "oldname-extra-tool", "name": "测试工具", "command": "ls", "role": "tool"}
        result = blocks_check._check_tool(
            card, None, [], None, False, {}, None, None, "/bin", False
        )
        self.assertEqual(
            result["how"],
            "关掉这个应用再重新打开，然后再检测一次",
        )

    def test_cli_installed_but_missing_in_app(self):
        with tempfile.TemporaryDirectory() as folder:
            command = Path(folder) / "codex"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            result = blocks_check.check_cli(
                blocks.builtin_card("codex"),
                path="",
                terminal_path=folder,
                record=False,
            )
        self.assertEqual(result["reason"], "Codex 装好了，但这个应用还没认到")
        self.assertEqual(
            result["how"],
            "关掉这个应用再重新打开，然后再检测一次",
        )

    def test_environment_hint(self):
        self.assertEqual(
            blocks_env._fix({"status": "找不到", "kind": "tool", "label": "Codex"}),
            "终端里已经装好，但这个应用还没认到：关掉这个应用再重新打开",
        )

    def test_clerk_installed_lookup_hint(self):
        with patch.dict(os.environ, {"PATH": ""}):
            text = blocks_tools._check_installed({"command": "not-here"})
        self.assertEqual(
            text,
            "找不到 not-here：这台电脑上没装，或者装在这个应用找不到的地方。",
        )

    def test_install_admin_messages(self):
        item = {"tool": "Codex", "command": "codex", "needs": "script"}
        result = blocks_install._judge(item, 1, "Permission denied", "")
        self.assertEqual(
            result["reason"], "要管理员权限才能装，这个应用不会自动提权"
        )
        self.assertEqual(
            result["how"],
            "安装脚本要电脑密码或管理员权限，这个应用不替你输：按官网说明在终端里自己装",
        )

    def test_install_finished_but_not_found(self):
        item = {"tool": "Codex", "command": "codex", "needs": "npm"}
        result = blocks_install._judge(item, 0, "", "")
        self.assertEqual(
            result["how"],
            "装好了，但这个应用还没认到：关掉这个应用再重新打开，然后检测一次",
        )

    def test_cli_install_message(self):
        args = types.SimpleNamespace(project="demo", update=False)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(cli, "install_into", return_value=Path(folder) / ".collab"):
                with patch.object(cli, "find_old_package", return_value=None):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        cli.cmd_install(args)
        self.assertIn("直接用这个应用里的那一份", output.getvalue())
        self.assertNotIn("超级助手", output.getvalue())

    def test_codex_unknown_method_error(self):
        sent = []
        with tempfile.TemporaryDirectory() as folder:
            dispatcher = CodexDispatcher(
                folder, lambda event: None, command=("test",)
            )
        dispatcher._write = sent.append
        dispatcher._on_ask(1, "unknown/method", {})
        self.assertEqual(
            sent[0]["error"]["message"], "这个应用不支持：unknown/method"
        )


class RoleAndHandoffTest(unittest.TestCase):
    def test_blocks_clerk_role(self):
        role_file = blocks_tools.ROLE_FILE   # 规则包第③步搬到 agents/角色/积木库管家.md
        text = role_file.read_text(encoding="utf-8")
        self.assertIn("你是这个应用的积木库管家", text.splitlines()[0])
        self.assertIn("这个应用自己不破解、不模拟点击", text)
        self.assertNotIn("超级助手", text)

    def test_desk_clerk_role(self):
        self.assertIn("你是这个应用的行政人员", desk.ROLE)
        self.assertNotIn("超级助手", desk.ROLE)

    def test_handoff_initiator_is_program(self):
        with tempfile.TemporaryDirectory() as folder:
            session = ChatSession(folder, "codex", lambda *args, **kwargs: None)
        session.emit({"type": "opening", "text": "自动交代", "history": False})
        summary = session.live_summary()
        self.assertEqual(summary["handoffs"][0]["from"], "程序")


class WrittenProjectTest(unittest.TestCase):
    def test_gitignore_comment_is_nameless(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".git").mkdir()
            self.assertTrue(ensure_ignored(folder))
            lines = Path(folder, ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("# 施工过程记录，只留在本机", lines)
        self.assertNotIn("超级助手", lines)


class InternalNamesStayTest(unittest.TestCase):
    def test_data_names_and_signatures_are_untouched(self):
        root = Path(__file__).resolve().parent.parent
        blocks_text = (root / "collab" / "blocks.py").read_text(encoding="utf-8")
        merge_text = (root / "collab" / "merge.py").read_text(encoding="utf-8")
        check_text = (root / "collab" / "check.py").read_text(encoding="utf-8")
        codex_text = (root / "desktop" / "dispatchers" / "codex.py").read_text(encoding="utf-8")
        self.assertIn("超级助手", blocks_text)
        self.assertIn("user.name=超级助手", merge_text)
        self.assertIn("超级助手检测-", check_text)
        self.assertIn("超级助手版本-", check_text)
        self.assertIn("【超级助手自动交代】", codex_text)


if __name__ == "__main__":
    unittest.main()
