"""47 号单：卡片归档。只是从列表收起来：不动项目、不动记录、排序不变，能找回。"""
import os
import subprocess
from pathlib import Path

from acceptance_helpers import STATIC, ProjectCase, ServerCase, needs_node, run_node
from desktop.view import card_view, list_cards


class Archive(ServerCase):
    def archive(self, card="CARD-001", on=True, token="口令"):
        return self.post("/api/clerk/card/archive", {"project": self.project, "card": card, "archived": on}, token=token)

    def row(self, card="CARD-001"):
        return next(c for c in list_cards(self.project) if c["id"] == card)

    def test_finished_cards_can_be_archived_and_brought_back(self):
        for number, state in enumerate(("已合并", "已放弃"), 1):
            card = f"CARD-00{number}"
            self.make_card(card, state=state)
            self.assertFalse(self.row(card)["archived"])
            self.assertEqual(self.archive(card), (200, {"ok": True}))
            self.assertTrue(self.row(card)["archived"])
            self.assertEqual(self.archive(card, on=False), (200, {"ok": True}))
            self.assertFalse(self.row(card)["archived"])

    def test_doing_it_twice_is_fine(self):
        self.make_card(state="已合并")
        self.assertEqual(self.archive()[0], 200)
        self.assertEqual(self.archive()[0], 200)
        self.assertEqual(self.archive(on=False)[0], 200)
        self.assertEqual(self.archive(on=False)[0], 200)

    def test_unfinished_cards_are_refused(self):
        for number, state in enumerate(("待对齐", "待派发", "开发中", "测试中", "待验收", "需Claude介入"), 1):
            card = f"CARD-00{number}"
            self.make_card(card, state=state, plan=True)
            status, answer = self.archive(card)
            self.assertEqual(status, 409, state)
            self.assertIn("结束", answer["error"])
            self.assertFalse(self.row(card)["archived"])

    def test_checks(self):
        self.make_card(state="已合并")
        self.assertEqual(self.archive(token="错的")[0], 403)
        self.assertEqual(self.archive("CARD-999")[0], 404)
        self.assertEqual(self.archive("../CARD-001")[0], 400)
        self.assertEqual(self.post("/api/clerk/card/archive", {"project": "/没有这个", "card": "CARD-001"})[0], 400)
        self.assertFalse(self.row()["archived"])

    def test_nothing_else_changes(self):
        cdir = self.make_card(state="已合并")
        (Path(self.project) / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
        before_files = {name: (cdir / name).read_bytes() for name in ("状态.json", "流转.jsonl", "需求卡.md")}
        before_row = self.row()
        ledger = Path(self.project) / "docs" / "开发进度跟踪.md"
        before_ledger = ledger.read_bytes() if ledger.exists() else None
        before_git = subprocess.run(["git", "status", "--porcelain"], cwd=self.project, capture_output=True, text=True).stdout
        self.archive()
        for name, data in before_files.items():
            self.assertEqual((cdir / name).read_bytes(), data, name)
        self.assertEqual(self.row()["updated"], before_row["updated"], "归档不算新动静")
        self.assertEqual(ledger.read_bytes() if ledger.exists() else None, before_ledger)
        after_git = subprocess.run(["git", "status", "--porcelain"], cwd=self.project, capture_output=True, text=True).stdout
        self.assertEqual(after_git, before_git)

    def test_order_does_not_change(self):
        older = self.make_card("CARD-001", state="已合并")
        newer = self.make_card("CARD-002", state="已合并")
        for number, cdir in enumerate((older, newer), 1):
            for name in ("状态.json", "流转.jsonl"):
                os.utime(cdir / name, (1000 + number, 1000 + number))
        order = [c["id"] for c in list_cards(self.project)]
        self.archive("CARD-001")
        self.assertEqual([c["id"] for c in list_cards(self.project)], order)
        self.assertEqual(order, ["CARD-002", "CARD-001"])

    def test_opening_the_app_skips_archived_cards(self):
        older = self.make_card("CARD-001", state="已合并", title="旧卡")
        newer = self.make_card("CARD-002", state="已合并", title="新卡")
        for number, cdir in enumerate((older, newer), 1):
            for name in ("状态.json", "流转.jsonl"):
                os.utime(cdir / name, (1000 + number, 1000 + number))
        self.assertEqual(card_view(self.project)["card"]["id"], "CARD-002")
        self.archive("CARD-002")
        self.assertEqual(card_view(self.project)["card"]["id"], "CARD-001")
        self.archive("CARD-001")
        self.assertEqual(card_view(self.project).get("card"), None)
        self.assertEqual(len(card_view(self.project)["cards"]), 2, "列表里还要有，左栏才能显示已归档")

    def test_archived_card_can_still_be_opened(self):
        self.make_card(state="已合并", title="旧卡")
        self.archive()
        self.assertEqual(card_view(self.project, "CARD-001")["card"]["id"], "CARD-001")

    def test_clerk_can_still_find_it(self):
        from desktop.clerk.tools import run_tool
        self.make_card(state="已合并", title="旧卡")
        self.archive()
        self.assertIn("CARD-001", run_tool(self.project, "list_cards", {}))

    def test_a_card_that_is_no_longer_finished_is_not_hidden(self):
        cdir = self.make_card(state="已合并")
        self.archive()
        (cdir / "状态.json").write_text((cdir / "状态.json").read_text(encoding="utf-8").replace("已合并", "待验收"), encoding="utf-8")
        self.assertFalse(self.row()["archived"], "要你处理的卡不能藏着")


@needs_node()
class PageRules(ProjectCase):
    def check(self, body):
        script = """
        const N = require('./desktop/static/nav.js'); const assert = require('assert');
        const done = { id: 'a', title: '做完的', phase: { tone: 'done', label: '已完成', needs_you: false } };
        const put = { id: 'b', title: '收起的', archived: true, phase: { tone: 'done', label: '已完成', needs_you: false } };
        const run = { id: 'c', title: '进行的', phase: { tone: 'run', label: '进行中', needs_you: false } };
        const need = { id: 'd', title: '等你的', phase: { tone: 'ok', label: '等你确认', needs_you: true } };
        const ids = l => l.map(c => c.id);
        """
        proc = run_node(script + body)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_groups_leave_archived_out(self):
        self.check("""
        const groups = N.cardGroups([done, put, run, need]);
        assert.deepStrictEqual(groups.map(g => g.title), ['等你处理', '进行中', '已完成']);
        assert.deepStrictEqual(ids(groups[2].cards), ['a']);
        assert.deepStrictEqual(ids(N.archivedCards([done, put, run, need])), ['b']);
        assert.deepStrictEqual(N.cardGroups([put]), [], '全归档了也不显示空组');
        assert.deepStrictEqual(ids(N.needsYou([done, put, run, need])), ['d']);
        """)

    def test_who_can_be_archived(self):
        self.check("""
        assert.strictEqual(N.canArchive(done), true);
        assert.strictEqual(N.canArchive(put), false, '已经收起了');
        assert.strictEqual(N.canArchive(run), false);
        assert.strictEqual(N.canArchive(need), false);
        assert.strictEqual(N.canArchive({ id: 'e', title: '读不出', phase: { tone: 'done', label: 'x', needs_you: false } }), true);
        """)

    def test_search_finds_archived_and_opens_the_group(self):
        self.check("""
        assert.deepStrictEqual(ids(N.archivedCards(N.filter([done, put], '收起'))), ['b']);
        assert.strictEqual(N.showArchived(false, ''), false);
        assert.strictEqual(N.showArchived(true, ''), true);
        assert.strictEqual(N.showArchived(false, '收起'), true, '搜索时自动展开');
        """)

    def test_cards_without_the_field_still_work(self):
        self.check("""
        assert.deepStrictEqual(N.archivedCards([done]), []);
        assert.deepStrictEqual(N.archivedCards([]), []);
        assert.deepStrictEqual(N.archivedCards(undefined), []);
        """)


class Wiring(ProjectCase):
    def test_nav(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        for words in ("/api/clerk/card/archive", "已归档", "取消归档", "data-archive", "c.archived", "NavCore.canArchive"):
            self.assertIn(words, js)

    def test_archive_button_is_not_inside_the_row_button(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        row = js[js.index("function cardRow"):]
        row = row[:row.index("function drawList")]
        self.assertGreater(row.index("data-archive"), row.index("</button>"), "按钮里不能再放按钮")

    def test_redraw_when_archive_changes(self):
        js = (STATIC / "nav.js").read_text(encoding="utf-8")
        key = js[js.index("JSON.stringify(cards.map"):]
        self.assertIn("c.archived", key[:key.index("})")], "只改了归档也要重画")
