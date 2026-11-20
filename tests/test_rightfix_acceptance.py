"""右栏显示小修三条（docs/右栏显示小修/方案.md，用户 2026-11-20 批准）验收测试：数据和命令这一半。

页面那一半（token 另起一行、停下时右栏只留按钮、中间那行收起展开、Claude 接手一格、完成时间）
在 tests/rightfix_page_check.js 里真的执行页面脚本检查，由本文件最后一条调用。

测试数据用 CARD-019（切换项目）真实的流转记录（tests/fixtures/card019/，只有进度记录，没有代码）：
它复查退回 4 件后停下，由 Claude 接手修好、放进项目——正是用户截图里复查格红叉的那张卡。
"""
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "card019"
MERGED_AT = 1795150840.8   # CARD-019 放进项目那条记录的时间（北京时间 11-20 13:00）


def station(view, key):
    return next((s for s in view["stations"] if s["key"] == key), None)


class Card019(unittest.TestCase):
    def setUp(self):
        from collab.workspace import init_collab
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name) / "项目"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        init_collab(str(self.project))
        self.cdir = self.project / ".collab" / "cards" / "CARD-019"
        shutil.copytree(FIXTURE, self.cdir)

    def view(self):
        from desktop.view import card_view
        return card_view(str(self.project), "CARD-019")

    def takeover(self, *extra):
        from collab.cli import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["takeover", str(self.project), "CARD-019", "--changed", "4", "--reviews", "2",
                         "--later", "2", "--note", "主对话没发的字看卡时单独保管；砍掉团队设置页恢复卡", *extra])
        return code, out.getvalue()


class TakeoverCommand(Card019):
    def test_records_a_takeover_event(self):
        from collab.flow import read_flow
        code, _ = self.takeover()
        self.assertIn(code, (0, None))
        events = [e for e in read_flow(self.cdir) if e.get("kind") == "接手"]
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e.get("src"), "Claude")
        self.assertEqual((e.get("changed"), e.get("reviews"), e.get("later")), (4, 2, 2))
        self.assertIn("单独保管", e.get("summary", ""))

    def test_unknown_card_is_refused(self):
        from collab.cli import main
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                code = main(["takeover", str(self.project), "CARD-404", "--changed", "1", "--reviews", "0",
                             "--later", "0", "--note", "x"])
            except SystemExit as stop:
                code = stop.code
        self.assertNotIn(code, (0, None), "没有这张卡不能记")


class ReviewCellAfterTakeover(Card019):
    def test_without_takeover_record_review_cell_stays_as_before(self):
        rev = station(self.view(), "rev")
        self.assertEqual(rev["state"], "fail", "没有接手记录的老卡照旧（不猜）")
        self.assertIsNone(station(self.view(), "takeover"))

    def test_review_cell_turns_green_with_note(self):
        self.takeover()
        rev = station(self.view(), "rev")
        self.assertEqual(rev["state"], "done")
        self.assertIn("退回 4 件", rev["detail"])
        self.assertIn("已由 Claude 接手修好", rev["detail"])


class TakeoverThenMore(Card019):
    """Codex 复查（11-20）：接手后又被复查退回不能还是绿勾；接手记两次按最后一条显示。"""

    def test_review_rejected_again_after_takeover_is_red_again(self):
        from collab.flow import record
        self.takeover()
        record(self.cdir, "复查结论", src="程序", result="退回", summary="复查退回 1 件")
        v = self.view()
        rev = station(v, "rev")
        self.assertEqual(rev["state"], "fail", "接手之后又退回，复查格回到红叉")
        self.assertNotIn("已由 Claude 接手修好", rev.get("detail", ""))
        self.assertIsNotNone(station(v, "takeover"), "Claude 接手那格照常显示")

    def test_second_takeover_wins(self):
        from collab.cli import main
        self.takeover()
        with contextlib.redirect_stdout(io.StringIO()):
            main(["takeover", str(self.project), "CARD-019", "--changed", "6", "--reviews", "3",
                  "--later", "1", "--note", "第二次接手"])
        st = station(self.view(), "takeover")
        for words in ("改了 6 处", "Codex 复查 3 轮", "1 条记进以后再修"):
            self.assertIn(words, st["detail"])


class Styles(unittest.TestCase):
    """页面检查不加载样式表，“另起一行”“收起成一行”的样子只能看样式规则（规则包《测试怎么写》：跑不起来的才逐字查）。"""

    def test_usage_line_takes_its_own_row(self):
        css = (ROOT / "desktop" / "static" / "panel.css").read_text(encoding="utf-8")
        import re
        rule = re.search(r"(^|[\s,}])\.u\s*[,{][^}]*}", css)
        self.assertTrue(rule, "右栏要有 .u 的样式")
        self.assertRegex(rule.group(0), r"grid-column:\s*2\s*/\s*-1", "用量那行和状态行一样占整行")

    def test_card_now_folds_to_one_line_and_opens_fully(self):
        import re
        css = (ROOT / "desktop" / "static" / "chat.css").read_text(encoding="utf-8")
        fold = re.search(r"#cardNow\.fold\s*{[^}]*}", css)
        self.assertTrue(fold and re.search(r"-webkit-line-clamp:\s*1\b", fold.group(0)), "收起时一行")
        opened = re.search(r"#cardNow\.open\s*{[^}]*}", css)
        self.assertTrue(opened and re.search(r"-webkit-line-clamp:\s*(unset|none)|display:\s*block", opened.group(0)),
                        "展开时不截断")


class TakeoverStation(Card019):
    def test_station_sits_between_review_and_confirm(self):
        self.takeover()
        keys = [s["key"] for s in self.view()["stations"]]
        self.assertEqual(keys, ["you", "disp", "test", "dev", "check", "rev", "takeover", "accept"])

    def test_station_says_what_was_done(self):
        self.takeover()
        st = station(self.view(), "takeover")
        self.assertEqual(st["role"], "Claude 接手")
        self.assertEqual(st["who"], "Claude")
        self.assertEqual(st["state"], "done")
        for words in ("改了 4 处", "Codex 复查 2 轮", "2 条记进以后再修"):
            self.assertIn(words, st["detail"])

    def test_cards_without_takeover_keep_seven_stations(self):
        self.assertEqual(len(self.view()["stations"]), 7, "老的 7 格顺序由 test_nodispatcher_06 守着")


class FinishedTime(Card019):
    def test_merged_card_carries_finish_time(self):
        self.assertAlmostEqual(self.view().get("finished_at"), MERGED_AT, places=0)

    def test_unfinished_card_has_no_finish_time(self):
        from collab.state import Card
        Card("CARD-019", state="需Claude介入").save(self.cdir / "状态.json")
        lines = (self.cdir / "流转.jsonl").read_text(encoding="utf-8").splitlines()
        (self.cdir / "流转.jsonl").write_text("\n".join(lines[:59]) + "\n", encoding="utf-8")   # 停在“转人工”那一刻
        self.assertIsNone(self.view().get("finished_at"))


class PagePart(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面实跑检查")
    def test_page_check(self):
        import os
        env = dict(os.environ, TZ="Asia/Shanghai")   # 完成时间按用户电脑（北京时间）显示
        proc = subprocess.run(["node", str(ROOT / "tests" / "rightfix_page_check.js")],
                              capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
