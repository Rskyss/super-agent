"""68c 收尾（复查第 6 轮剩下的两条，Claude 接手修）。

1. 检测进行中切到别的积木块再切回来：页面上“正在检测”的记号没人清，检测早做完了按钮还一直灰着（API 积木块也一样）。
   改法：详情接口带上“这一块此刻是不是正在检测”（程序里一次一个的那把锁），页面按它定：没在检测就清掉记号；
   还在检测就显示“正在检测…”并过一会儿再看。
2. 接法说明有问题的积木块：先写了“接法说明有问题：……”又被统一隐藏，用户看不到原因。
"""
import re
import unittest
from pathlib import Path

from collab import blocks, blocks_check
from test_acceptance_55_blocks_server import BlocksCase

JS = Path(__file__).resolve().parents[1] / "desktop" / "static" / "blocks.js"


class DetailSaysChecking(BlocksCase):
    def setUp(self):
        super().setUp()
        blocks.save_card(blocks.builtin_card("agy"), self.home)

    def test_not_checking(self):
        status, d = self.bget("/api/blocks/detail", id="agy")
        self.assertEqual(status, 200, d)
        self.assertIs(d["checking"], False)

    def test_checking_while_lock_held(self):
        with blocks_check._OneAtATime("agy"):
            status, d = self.bget("/api/blocks/detail", id="agy")
        self.assertEqual(status, 200, d)
        self.assertIs(d["checking"], True)


class PageUsesServerTruth(unittest.TestCase):
    def setUp(self):
        self.js = JS.read_text(encoding="utf-8")

    def _open_detail_body(self):
        start = self.js.index("function openDetail(")
        return self.js[start:self.js.index("function drawDetailSecrets(", start)]

    def test_stale_check_mark_is_cleared_from_detail(self):
        body = self._open_detail_body()
        self.assertIn("d.checking", body, "详情回来时要按程序说的“是不是在检测”定按钮")
        self.assertRegex(body, r"delete activeChecks\[id\]", "没在检测就清掉页面上的记号")

    def test_still_checking_looks_again_later(self):
        body = self._open_detail_body()
        self.assertIn("正在检测…", body)
        self.assertRegex(body, r"setTimeout\(", "还在检测时过一会儿再看，做完自动恢复")

    def test_resumed_check_clears_running_text_when_done(self):
        """独立复查指出：按钮恢复了，结果区却一直写着“正在检测…”。"""
        body = self._open_detail_body()
        done = body[body.index("if (!d.checking)"):body.index("} else if (!activeTrials[id]")]
        self.assertIn("resumedChecks[id]", done)
        self.assertIn('$("detailResult").textContent = ""', done)

    def test_broken_card_keeps_its_reason_visible(self):
        start = self.js.index("function openBrokenDetail(")
        body = self.js[start:self.js.index("\n  }\n", start)]
        hide = body.index("hideBlockDetailContent()")
        shown = body.index('$("detailStatus").hidden = false')
        self.assertLess(hide, shown, "先统一隐藏，再把“接法说明有问题”那句显示出来")


if __name__ == "__main__":
    unittest.main()
