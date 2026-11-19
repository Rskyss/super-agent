"""第四轮复查（54）：保存失败时保留暂存内容，保存和放进项目时保留 Git 设置。"""

import subprocess
from pathlib import Path

from acceptance_helpers import ServerCase, write_script
from collab.merge import merge_card


class GitStateAfterCardActions(ServerCase):
    def git_output(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.project, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def save_pending(self):
        return self.post("/api/clerk/save_pending", {"project": self.project, "card": "CARD-001"})

    def save_with_rejecting_hook(self):
        hooks = Path(self.tmp) / "hooks"
        hooks.mkdir()
        write_script(hooks / "pre-commit", "echo '测试用提交检查拒绝保存' >&2\nexit 1\n")
        self.git("config", "--local", "core.hooksPath", str(hooks))
        head = self.git_output("rev-parse", "HEAD")

        status, answer = self.save_pending()

        self.assertEqual(status, 409, answer)
        self.assertIs(answer["ok"], False)
        self.assertEqual(self.git_output("rev-parse", "HEAD"), head)
        return answer

    def test_rejected_save_keeps_the_partially_staged_versions(self):
        self.make_card()
        project = Path(self.project)
        # Git 允许文件名包含回车，读取暂存记录时必须保留原来的路径。
        extra_path = "src/app\rnotes.py"
        (project / extra_path).write_text("x = 1\n", encoding="utf-8")
        self.git("add", "--", extra_path)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "加入测试文件")
        paths = ("src/app.py", extra_path)
        staged = "x = 2\n"
        working = "x = 2\ny = 3\n"
        for path in paths:
            (project / path).write_text(staged, encoding="utf-8")
            self.git("add", "--", path)
            (project / path).write_text(working, encoding="utf-8")

        answer = self.save_with_rejecting_hook()
        self.assertIn("测试用提交检查拒绝保存", answer["reason"])

        for path in paths:
            with self.subTest(path=repr(path)):
                self.assertEqual((project / path).read_text(encoding="utf-8"), working)
                self.assertEqual(self.git_output("show", f":{path}"), staged.strip(),
                                 "保存失败后应保留用户选中的暂存版本")

    def test_save_and_merge_leave_git_path_quoting_unset(self):
        def path_quoting():
            return [line for line in self.git_output("config", "--local", "--list").splitlines()
                    if line.partition("=")[0] == "core.quotepath"]

        def read_and_clear_path_quoting():
            settings = path_quoting()
            if settings:
                # 在下一次操作前恢复测试项目原来的设置。
                self.git("config", "--local", "--unset-all", "core.quotepath")
            return settings

        self.make_card(state="待验收", plan=True)
        copy = self.make_copy()
        self.assertEqual(path_quoting(), [])

        answer = merge_card(self.project, "CARD-001")
        self.assertFalse(answer["ok"], answer)
        self.assertIn("没有改动", answer["reason"])
        after_empty_merge = read_and_clear_path_quoting()

        (copy / "src/app.py").write_text("x = 2\n", encoding="utf-8")
        answer = merge_card(self.project, "CARD-001")
        self.assertTrue(answer["ok"], answer)
        after_merge = read_and_clear_path_quoting()

        (Path(self.project) / "src/app.py").write_text("x = 3\n", encoding="utf-8")
        self.assertEqual(self.save_pending(), (200, {"ok": True, "saved": 1}))
        self.assertEqual(
            {"没有改动时点放进项目": after_empty_merge, "有改动时点放进项目": after_merge, "保存": path_quoting()},
            {"没有改动时点放进项目": [], "有改动时点放进项目": [], "保存": []},
            "操作应保留项目原先未设置的中文文件名显示选项",
        )

    def test_rejected_save_keeps_the_deletion_staged(self):
        self.make_card()
        path = "src/app.py"
        self.git("rm", "--", path)
        self.assertEqual(self.git_output("diff", "--cached", "--name-status", "--", path), f"D\t{path}")
        self.assertEqual(self.get("/api/clerk/uncommitted", project=self.project, card="CARD-001", token="口令"),
                         (200, {"count": 1, "files": [path]}))

        # 若 Git 在提交检查之前就拒绝保存，失败后仍应保留暂存的删除。
        self.save_with_rejecting_hook()

        self.assertFalse((Path(self.project) / path).exists(), "保存失败后工作区仍应保留删除")
        self.assertEqual(self.git_output("diff", "--cached", "--name-status", "--", path), f"D\t{path}",
                         "保存失败后删除仍应留在暂存区")
