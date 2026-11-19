"""C1.8 验收测试（Claude 写）：复查意见重写后还看不懂就停下，不再从原话里猜。依据 docs/跑卡流程调整/方案-C1.8.md。"""
import unittest

from collab.flow import read_flow
from test_flowfix_c15_acceptance import NOW, Flow, review

RAW = "结论：不通过\n保存后草稿丢失，位置 app/save.py:12。另外按钮颜色不对。"


class StopWhenUnreadable(Flow):
    def test_bad_twice_reject_stops_instead_of_guessing(self):
        card, calls = self.run_flow([RAW, RAW])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(self.of(calls, "dev")), 1, "不再把整段原话退回给写代码的")
        self.assertEqual(len(self.of(calls, "reformat")), 1, "重写的机会照旧给一次")
        flow = read_flow(self.cdir)
        stop = [e for e in flow if e.get("kind") == "转人工"]
        self.assertTrue(stop)
        self.assertIn("看不懂", stop[-1].get("summary", ""))
        self.assertFalse([e for e in flow if e.get("kind") == "复查结论" and e.get("result") == "退回"],
                         "没有自动退回的结论")
        self.assertFalse([e for e in flow if e.get("kind") == "退回"])

    def test_original_words_are_kept_for_claude(self):
        self.run_flow([RAW, RAW])
        replies = [e for e in read_flow(self.cdir) if e.get("kind") == "回复" and e.get("src") == "复查员"]
        from collab.flow import text_of
        self.assertTrue(any("按钮颜色不对" in (text_of(self.cdir, e) or "") for e in replies))

    def test_bad_once_then_good_still_works(self):
        """重写一次就合格的照旧按清单走：第 1 轮这次改的中等问题要退回，和直接写对格式时一样。
        （C1.8 复查第 1 轮严重 2：原来原话 + 重写各算一次退回，第 1 轮就被当成退满，问题被记以后再修直接放行。）"""
        card, calls = self.run_flow([RAW, review("不通过", [NOW]), review("通过")])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.of(calls, "reformat")), 1)
        self.assertEqual(len(self.of(calls, "dev")), 2, "第 1 轮这次改的问题要退回写代码的")
        self.assertEqual(self.conclusions()[0].get("result"), "退回")

    def test_second_line_reject_cannot_be_flipped_by_rewrite(self):
        """开场白在第一行、第二行写“结论：不通过”，重写的清单说通过：不能放行（C1.8 复查第 1 轮中等 3）。"""
        text = "先说一句：这次改动范围不大。\n结论：不通过\n保存后草稿丢失。"
        card, calls = self.run_flow([text, review("通过")])
        self.assertNotEqual(card.state, "待验收")

    def test_guessing_code_is_gone(self):
        import collab.dispatch as d
        for name in ("_extract_whole_text_back", "_extract_prose_back_problems", "_whole_text_return_prompt"):
            self.assertFalse(hasattr(d, name), name)


class StopWordsDoNotGuess(Flow):
    """C1.8a 复查第 2 轮后定：停下说明不挑问题，只提示原话里写到了严重；完整原话存着。"""

    def stop(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]

    def test_severe_anywhere_is_flagged_and_words_are_kept(self):
        bits = "".join(f"【严重】第{i}件会删文件。" for i in range(1, 7))
        raw = "结论：不通过\n" + "前面一大段说明。" * 80 + "```\n" + bits   # 写坏的代码块里也算
        self.run_flow([raw, raw])
        from collab.flow import text_of
        self.assertIn("写到了“严重”", self.stop().get("say") or "")
        kept = text_of(self.cdir, self.stop()) or ""
        for i in range(1, 7):
            self.assertIn(f"第{i}件会删文件", kept)

    def test_no_severe_no_flag(self):
        self.run_flow([RAW, RAW])
        self.assertNotIn("严重", self.stop().get("say") or "")

if __name__ == "__main__":
    unittest.main()
