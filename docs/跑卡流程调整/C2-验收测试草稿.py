"""卡 C2 验收测试：归档、写方案和开工（需求共识-第二批 A7、B1、B3；观察第 36 条）。

口径见 docs/跑卡流程调整/方案-第二批.md 第一节 7、9、10 条，施工单见 docs/跑卡流程调整/施工单-C2.md。
施工方不许改这个文件；补充测试写进 tests/test_c2_extra*.py。
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from acceptance_helpers import ROOT, STATIC, ServerCase
from collab.flow import read_flow, record
from test_acceptance_20_merge import MergeCase
from test_acceptance_43_preview import Base as PreviewBase, alive
from acceptance_helpers import ProjectCase
from desktop.preview import PreviewHub

ORDER = "docs/功能甲/施工单-甲.md"


def _need(title="改按钮", order=""):
    text = f"# {title}\n\n## 要做什么\n改颜色\n"
    if order:
        text += f"\n照 {order} 做。\n"
    return text + "\n## 允许改动的范围\n- src/app.py\n"


class ArchiveCase(ProjectCase):
    def card(self, order=""):
        cdir = self.make_card(state="待验收", plan=True)
        (cdir / "需求卡.md").write_text(_need(order=order), encoding="utf-8")
        (cdir / "以后再修.md").write_text("- 切换对话时偶尔卡一下\n", encoding="utf-8")
        (cdir / "复查结果.md").write_text("\n## 复查\n结论：不通过\n", encoding="utf-8")
        return cdir

    def order(self):
        path = Path(self.project) / ORDER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 施工单 甲\n", encoding="utf-8")
        return path


class A7Placement(ArchiveCase):
    def test_card_with_order_goes_next_to_the_order(self):
        from collab.archive import archive_card
        self.order()
        self.card(order=ORDER)
        target = archive_card(self.project, "CARD-001", "改按钮")
        expect = Path(self.project) / "docs" / "功能甲" / "卡片记录" / "CARD-001"
        self.assertEqual(Path(target), expect)
        self.assertTrue((expect / "卡片原文.md").is_file())
        self.assertIn("改颜色", (expect / "卡片原文.md").read_text(encoding="utf-8"))
        self.assertFalse((expect / "需求共识.md").exists(), "卡片原文不能冒充需求共识")
        self.assertTrue((expect / "验收记录.md").is_file())
        self.assertFalse((Path(self.project) / "docs" / "改按钮").exists(), "有施工单就不再另建功能名文件夹")

    def test_card_without_order_keeps_the_feature_folder(self):
        from collab.archive import archive_card
        self.card()
        target = archive_card(self.project, "CARD-001", "改按钮")
        self.assertEqual(Path(target), Path(self.project) / "docs" / "改按钮")
        self.assertTrue((Path(target) / "卡片原文.md").is_file())
        self.assertFalse((Path(target) / "需求共识.md").exists())

    def test_later_list_goes_into_process_records(self):
        from collab.archive import archive_card
        self.card()
        target = Path(archive_card(self.project, "CARD-001", "改按钮"))
        self.assertIn("切换对话", (target / "过程记录" / "以后再修.md").read_text(encoding="utf-8"))

    def test_review_line_reads_the_formal_conclusion(self):
        from collab.archive import archive_card
        cdir = self.card()
        record(cdir, "复查结论", src="程序", result="通过", summary="复查通过")
        target = Path(archive_card(self.project, "CARD-001", "改按钮"))
        text = (target / "验收记录.md").read_text(encoding="utf-8")
        self.assertIn("独立复查：通过", text, "以“复查结论”这一条为准，不再去猜复查原话")

    def test_review_line_for_a_returned_card(self):
        from collab.archive import archive_card
        cdir = self.card()
        record(cdir, "复查结论", src="程序", result="通过", summary="复查通过")
        record(cdir, "复查结论", src="程序", result="退回", summary="复查退回 1 件")
        target = Path(archive_card(self.project, "CARD-001", "改按钮"))
        self.assertIn("独立复查：不通过", (target / "验收记录.md").read_text(encoding="utf-8"), "取最后一条复查结论")

    def test_writing_into_a_copy_twice_does_not_make_duplicates(self):
        from collab.archive import archive_card
        self.card()
        copy = Path(self.tmp) / "副本"
        copy.mkdir()
        archive_card(self.project, "CARD-001", "改按钮", root=copy)
        archive_card(self.project, "CARD-001", "改按钮", root=copy)
        names = sorted(p.name for p in (copy / "docs" / "改按钮").rglob("*") if p.is_file())
        self.assertFalse([n for n in names if "（CARD-001）" in n], f"重放一次不能多出带卡号的重复文件：{names}")
        self.assertFalse((Path(self.project) / "docs" / "改按钮").exists(), "写进副本时不碰项目")


class A7SameCommit(MergeCase):
    def test_records_ride_in_the_same_commit_as_the_code(self):
        self.prepared()
        self.assertTrue(self.merge()["ok"])
        names = self.out("show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertIn("src/app.py", names)
        self.assertIn("docs/改按钮/验收记录.md", names, "记录要和代码进同一次提交")
        self.assertIn("docs/改按钮/卡片原文.md", names)
        left = self.out("status", "--porcelain", "--", "docs/改按钮")
        self.assertEqual(left, "", "放进项目后不能留下没提交的记录文件夹")
        text = self.main_file("docs/改按钮/验收记录.md")
        self.assertIn("已放进项目", text)

    def test_records_follow_the_order_folder_when_merging(self):
        order = Path(self.project) / ORDER
        order.parent.mkdir(parents=True)
        order.write_text("# 施工单 甲\n", encoding="utf-8")
        self.git("add", ORDER)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "施工单")
        cdir = self.prepared()
        (cdir / "需求卡.md").write_text(_need(order=ORDER), encoding="utf-8")
        self.assertTrue(self.merge()["ok"])
        names = self.out("show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertIn("docs/功能甲/卡片记录/CARD-001/卡片原文.md", names)
        self.assertFalse((Path(self.project) / "docs" / "改按钮").exists())

    def test_ledger_and_flow_after_merge(self):
        self.prepared()
        self.assertTrue(self.merge()["ok"])
        kinds = self.flow_kinds()
        self.assertIn("归档", kinds)
        ledger = (Path(self.project) / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
        self.assertIn("docs/改按钮/", ledger)

    def test_rolled_back_merge_leaves_no_records(self):
        self.prepared('test "$(basename "$PWD")" != 项目')
        before = self.head()
        self.assertFalse(self.merge()["ok"])
        self.assertEqual(self.head(), before)
        self.assertFalse((Path(self.project) / "docs" / "改按钮").exists(), "退回时记录跟着退回")
        self.assertNotIn("归档", self.flow_kinds())

    def test_nothing_to_merge_still_says_so(self):
        self.prepared(change="")
        before = self.head()
        self.assertFalse(self.merge()["ok"])
        self.assertEqual(self.head(), before, "只有记录、没有代码改动时不能提交")


class Obs36LeftBehind(MergeCase):
    def test_files_left_in_the_copy_are_named(self):
        self.prepared()
        (self.worktree / "tests").mkdir()
        (self.worktree / "tests" / "test_claude_extra.py").write_text("# Claude 补的\n", encoding="utf-8")
        self.assertTrue(self.merge()["ok"])
        notes = [e for e in read_flow(self.collab / "cards" / "CARD-001") if e.get("kind") == "提醒"]
        self.assertTrue(notes, "副本里有没带进项目的文件，要记一条提醒")
        self.assertIn("tests/test_claude_extra.py", notes[-1].get("summary", "") + notes[-1].get("text", ""))
        self.assertIn("没带进项目", notes[-1].get("summary", ""))

    def test_no_note_when_everything_came_along(self):
        self.prepared()
        self.assertTrue(self.merge()["ok"])
        self.assertNotIn("提醒", self.flow_kinds())


class B1PlannerWords(ProjectCase):
    def test_prompt_forbids_guessing_the_address(self):
        from collab.planner import _build_prompt
        cdir = self.make_card()
        prompt = _build_prompt(cdir, (cdir / "需求卡.md").read_text(encoding="utf-8"), False, self.project)
        self.assertIn("不要猜", prompt)
        self.assertIn("查不到就都填空字符串", prompt)


class B1PreviewNotFound(PreviewBase, ProjectCase):
    def setUp(self):
        super().setUp()
        self.hub = PreviewHub(wait_seconds=8)
        self.addCleanup(self.hub.stop)

    def test_page_not_found_says_so_and_offers_the_folder(self):
        self.prepared(url=None)
        cdir = self.collab / "cards" / "CARD-001"
        data = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        data["view_url"] = f"http://127.0.0.1:{self.port}/panel-不存在"
        (cdir / "开工.json").write_text(json.dumps(data), encoding="utf-8")
        answer = self.hub.start(self.project, "CARD-001")
        self.assertFalse(answer["ok"], "地址返回“找不到”不能当成打开了")
        self.assertEqual(answer.get("mode"), "folder")
        self.assertIn("预览地址打不开", answer["reason"])
        self.assertIn("打开文件夹", answer["reason"])
        self.assertFalse(self.hub.status(self.project, "CARD-001")["running"])
        self.assertFalse(alive(f"http://127.0.0.1:{self.port}/"), "打不开就把起来的预览程序关掉")

    def test_good_address_still_opens(self):
        self.prepared()
        answer = self.hub.start(self.project, "CARD-001")
        self.assertEqual((answer["ok"], answer["mode"]), (True, "web"))


class B3Files(ServerCase):
    def card_with_order(self, order_text_committed=False):
        order = Path(self.project) / ORDER
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text("# 施工单 甲\n", encoding="utf-8")
        if order_text_committed:
            self.git("add", ORDER)
            self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "施工单")
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text(_need(order=ORDER), encoding="utf-8")
        return cdir

    def files(self, card="CARD-001"):
        status, answer = self.get("/api/clerk/uncommitted", project=self.project, card=card, token="口令")
        self.assertEqual(status, 200, answer)
        return answer

    def test_only_documents_means_nothing_to_save(self):
        self.make_card()
        (Path(self.project) / "docs").mkdir()
        (Path(self.project) / "docs" / "笔记.md").write_text("记一笔\n", encoding="utf-8")
        (Path(self.project) / "README.md").write_text("说明\n", encoding="utf-8")
        self.assertEqual(self.files(), {"count": 0, "files": []})

    def test_code_is_listed_documents_are_not(self):
        self.make_card()
        Path(self.project, "src", "app.py").write_text("x = 2\n", encoding="utf-8")
        (Path(self.project) / "docs").mkdir()
        (Path(self.project) / "docs" / "笔记.md").write_text("记一笔\n", encoding="utf-8")
        self.assertEqual(self.files(), {"count": 1, "files": ["src/app.py"]})

    def test_new_files_in_new_folders_are_listed_one_by_one(self):
        self.make_card()
        (Path(self.project) / "lib").mkdir()
        (Path(self.project) / "lib" / "a.py").write_text("a\n", encoding="utf-8")
        (Path(self.project) / "lib" / "b.py").write_text("b\n", encoding="utf-8")
        self.assertEqual(self.files()["files"], ["lib/a.py", "lib/b.py"])

    def test_unsaved_order_of_this_card_counts(self):
        self.card_with_order()
        self.assertEqual(self.files()["files"], [ORDER], "施工单没存进项目，施工方会看不到它")

    def test_saved_order_does_not_count(self):
        self.card_with_order(order_text_committed=True)
        self.assertEqual(self.files()["files"], [])

    def test_save_pending_commits_exactly_the_listed_files(self):
        self.card_with_order()
        Path(self.project, "src", "app.py").write_text("x = 2\n", encoding="utf-8")
        (Path(self.project) / "docs" / "笔记.md").write_text("记一笔\n", encoding="utf-8")
        status, answer = self.post("/api/clerk/save_pending", {"project": self.project, "card": "CARD-001"})
        self.assertEqual((status, answer.get("ok")), (200, True), answer)
        names = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=self.project,
                               capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(names), sorted(["src/app.py", ORDER]))
        self.assertEqual(self.files()["files"], [])
        left = subprocess.run(["git", "status", "--porcelain", "--", "docs/笔记.md"], cwd=self.project,
                              capture_output=True, text=True).stdout
        self.assertTrue(left.strip(), "没列出来的文档不能顺手存进去")

    def test_save_pending_needs_token(self):
        self.make_card()
        Path(self.project, "src", "app.py").write_text("x = 2\n", encoding="utf-8")
        status, _ = self.post("/api/clerk/save_pending", {"project": self.project, "card": "CARD-001"}, token="错的")
        self.assertEqual(status, 403)

    def test_save_failure_is_reported_and_nothing_starts(self):
        self.make_card()
        Path(self.project, "src", "app.py").write_text("x = 2\n", encoding="utf-8")
        hook = Path(self.project) / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho 不让存\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, capture_output=True,
                                text=True).stdout.strip()
        status, answer = self.post("/api/clerk/save_pending", {"project": self.project, "card": "CARD-001"})
        self.assertFalse(answer.get("ok"))
        self.assertIn("没存成", answer.get("reason", ""))
        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, capture_output=True,
                               text=True).stdout.strip()
        self.assertEqual(before, after)
        self.assertEqual(self.state(), "待对齐", "没存成就不能开工")


class B3Panel(unittest.TestCase):
    def test_dialog_has_three_plain_buttons(self):
        html = (STATIC / "panel.html").read_text(encoding="utf-8")
        for ident in ('id="startBox"', 'id="startFiles"', 'id="startSave"', 'id="startGo"', 'id="startCancel"'):
            self.assertIn(ident, html)
        for label in ("先存再开工", "直接开工", "先不开工"):
            self.assertIn(label, html)

    def test_browser_confirm_for_uncommitted_is_gone(self):
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertNotIn("个文件还没提交", js, "不再用浏览器自带弹窗")
        self.assertIn("/api/clerk/save_pending", js)
        self.assertIn("card=", js[js.index("/api/clerk/uncommitted"):js.index("/api/clerk/uncommitted") + 200],
                      "开工检查要带上卡号（施工单算不算要存，看这张卡）")

    @unittest.skipUnless(shutil.which("node"), "需要 Node")
    def test_dialog_words(self):
        script = ("const P=require('./desktop/static/panel.js');"
                  "if(P.startDialogText(0)!==null)process.exit(1);"
                  "const t=P.startDialogText(2);"
                  "if(t.indexOf('有 2 处改动还没存进项目')<0||t.indexOf('施工会看不到它们')<0)process.exit(2);")
        done = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
