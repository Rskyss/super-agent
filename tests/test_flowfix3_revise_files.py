"""流程修理第三批 ㉟：“继续改”框里可以写上还要改的文件，写了就加进允许改动的范围（写了就算用户点头），卡片里记一笔。

68c 里要改的文件不在允许范围里，“继续改”加不了，只能 Claude 手动改卡片。
"""
import tempfile
import unittest
from pathlib import Path

from collab.flow import read_flow
from collab.scope import read_allowed
from collab.state import Card
from collab.tasks import TaskError, revise_card
from collab.workspace import init_collab

ROOT = Path(__file__).resolve().parent.parent


class ReviseAddsFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        init_collab(self.project)
        self.cdir = self.project / ".collab" / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n\n## 允许改动的范围\n- desktop/view.py\n\n## 明确不做什么\n- 不改页面\n",
                                          encoding="utf-8")
        Card("CARD-001", state="需Claude介入").save(self.cdir / "状态.json")

    def tearDown(self):
        self.tmp.cleanup()

    def allowed(self):
        return read_allowed((self.cdir / "需求卡.md").read_text(encoding="utf-8"))

    def test_files_go_into_allowed_scope(self):
        revise_card(self.project, "CARD-001", "把试做也改了", files=["collab/abilities_run.py", "desktop/view.py"])
        self.assertEqual(self.allowed(), ["desktop/view.py", "collab/abilities_run.py"])
        body = (self.cdir / "需求卡.md").read_text(encoding="utf-8")
        self.assertIn("## 明确不做什么\n- 不改页面", body, "别的节不动")
        added = [e for e in read_flow(self.cdir) if e.get("kind") == "追加范围"]
        self.assertTrue(added)
        self.assertIn("collab/abilities_run.py", added[0]["summary"])
        self.assertEqual(added[0]["src"], "你")

    def test_bad_paths_rejected_and_nothing_changed(self):
        before = (self.cdir / "需求卡.md").read_text(encoding="utf-8")
        for bad in ("/etc/passwd", "../x.py", ".collab/配置.json", "a/../../b.py", ""):
            with self.assertRaises(TaskError, msg=bad):
                revise_card(self.project, "CARD-001", "改", files=[bad])
        self.assertEqual((self.cdir / "需求卡.md").read_text(encoding="utf-8"), before)

    def test_without_files_nothing_changes_in_scope(self):
        revise_card(self.project, "CARD-001", "再改改")
        self.assertEqual(self.allowed(), ["desktop/view.py"])
        self.assertFalse([e for e in read_flow(self.cdir) if e.get("kind") == "追加范围"])

    def test_page_and_server_pass_files(self):
        html = (ROOT / "desktop" / "static" / "panel.html").read_text(encoding="utf-8")
        js = (ROOT / "desktop" / "static" / "panel.js").read_text(encoding="utf-8")
        server = (ROOT / "desktop" / "server.py").read_text(encoding="utf-8")
        self.assertIn('id="reviseFiles"', html)
        self.assertIn("reviseFiles", js)
        self.assertIn('body.get("files")', server)


if __name__ == "__main__":
    unittest.main()
