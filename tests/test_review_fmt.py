"""老测试复查意见转换小工具（tests/review_fmt.py）本身的测试。"""
import inspect
import json
import re
import unittest

from collab.agents import AgentResult
from review_fmt import listed, listed_ask, to_list

FIELDS = ("级别", "来源", "类型", "现象", "位置", "证据")


def block(text):
    m = re.search(r"```json\n(.*?)\n```", text, re.S)
    return json.loads(m.group(1)) if m else None


class Convert(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(to_list("结论：通过\n改动符合需求"), {"结论": "通过", "问题": []})

    def test_tagged_items(self):
        data = to_list("结论：不通过\n1. 【轻微】【原来就有的】提示字小。位置：[a.js:3](/x/a.js:3)\n"
                       "2. 【测试的问题】【中等】测试把编号写成 tavily。\n3. 编号写错也能出草稿。")
        p = data["问题"]
        self.assertEqual([(i["级别"], i["来源"], i["类型"]) for i in p],
                         [("轻微", "原来就有的", "功能问题"), ("中等", "这次改的", "测试写错"), ("中等", "这次改的", "功能问题")])
        self.assertEqual(p[0]["位置"], "/x/a.js:3", "链接取地址，和 C1 的 _places 一致")
        for i in p:
            self.assertTrue(all(f in i for f in FIELDS))
            self.assertEqual(i["证据"]["方式"], "推演")
            self.assertRegex(i["证据"]["对照"], r"\.\w+:\d+", "推演要带 文件:行号")

    def test_unparsed_keeps_items_and_adds_whole_text_with_top_level(self):
        text = ("结论：不通过\n1. 【测试的问题】【中等】测试把编号写成 tavily。\n\n"
                "**【严重】【这次改的】保存后结果全丢。**")
        from collab.dispatch import _review_items, _unparsed_serious
        self.assertTrue(_unparsed_serious(text, _review_items(text)), "样例要真的进 C1 的整段退回分支")
        p = to_list(text)["问题"]
        self.assertEqual(p[0]["类型"], "测试写错", "认出来的测试问题照旧分流")
        self.assertEqual(p[-1]["级别"], "严重", "整段那条取原文里最重的级别")
        self.assertEqual((p[-1]["来源"], p[-1]["类型"]), ("这次改的", "功能问题"), "整段那条不继承开头那条的标签")
        self.assertIn("保存后结果全丢", p[-1]["现象"])

    def test_unnumbered_old_first_does_not_turn_whole_into_later(self):
        text = ("结论：不通过\n【轻微】【原来就有的】旧提示有错字。\n\n**【轻微】【这次改的】保存后草稿丢失。**\n\n"
                "结论页上的保存也失败，测试命令可复现。")
        from collab.dispatch import _review_items, _unparsed_serious
        self.assertTrue(_unparsed_serious(text, _review_items(text)), "样例要真的进整段退回分支")
        last = to_list(text)["问题"][-1]
        self.assertEqual(last["来源"], "这次改的", "整段退回那条必须是这次改的，不能变成以后再修")
        self.assertIn("保存后草稿丢失", last["现象"])
        self.assertIn("结论页上的保存也失败", last["现象"], "只去掉结论那一行，别的以“结论”开头的话要留着")

    def test_list_without_title_not_duplicated_and_title_word_does_not_skip(self):
        has = '结论：不通过\n```json\n{"结论": "不通过", "问题": [{"级别": "中等"}]}\n```'
        sample = '结论：不通过\n1. 【严重】保存失败\n配置示例：\n```json\n{"结论": "待确认"}\n```'
        self.assertIn('"级别": "严重"', listed(sample, force=True), "json 示例不是清单，照样补")
        self.assertEqual(listed(has, force=True), has, "末尾已经是清单（没写标题也算），不再追加")
        mention = '结论：通过\n复查清单写法参考：\n```json\n{"a": 1}\n```'
        self.assertIn('"结论": "通过"', listed(mention, force=True), "正文提到“复查清单”、但代码块不是清单：照样补")

    def test_whole_item_type_location_and_fenced_output(self):
        text = ("结论：不通过\n【测试的问题】测试编号写错。位置：old.py:3\n\n**【严重】【这次改的】保存后结果全丢 save.py:90**\n\n"
                "```\n结论：通过\n```")
        from collab.dispatch import _review_items, _unparsed_serious
        self.assertTrue(_unparsed_serious(text, _review_items(text)))
        p = to_list(text)["问题"]
        self.assertEqual(p[0]["类型"], "测试写错")
        self.assertEqual(p[-1]["类型"], "功能问题", "整段那条不继承无编号首条的类型")
        self.assertNotIn("old.py:3", p[-1]["位置"], "不拿首条的位置充数")
        self.assertIn("```\n结论：通过\n```", p[-1]["现象"], "代码块里引用的输出原样保留")

    def test_level_same_as_c1_for_double_tags(self):
        self.assertEqual(to_list("结论：不通过\n1. 【中等】【轻微】字小。")["问题"][0]["级别"], "轻微")

    def test_link_target_is_location(self):
        p = to_list("结论：不通过\n1. 【中等】卡住。位置：[chat.js:600](/x/chat.js:600)")["问题"][0]
        self.assertEqual(p["位置"], "/x/chat.js:600")

    def test_plain_json_example_still_converted(self):
        out = listed("结论：通过\n输出示例：\n```json\n{\"a\": 1}\n```", force=True)
        self.assertIn("复查清单", out)

    def test_no_verdict_untouched(self):
        self.assertIsNone(to_list("还没有结论"))
        self.assertEqual(listed("还没有结论", force=True), "还没有结论")

    def test_listed_keeps_original_and_appends_block(self):
        out = listed("结论：通过\n没问题", force=True)
        self.assertTrue(out.startswith("结论：通过\n没问题"))
        self.assertEqual(block(out), {"结论": "通过", "问题": []})
        self.assertEqual(listed(out, force=True), out, "已有清单不再加")


class Wrap(unittest.TestCase):
    def setUp(self):
        import review_fmt
        self._old = review_fmt._enabled
        review_fmt._enabled = lambda: True   # 模拟程序已升级（施工前 read_review_list 还不存在）
        self.addCleanup(setattr, review_fmt, "_enabled", self._old)

    def test_only_reviewer_reply_and_signature_kept(self):
        def ask(adapter, prompt, cwd, models=None, on_quiet=None):
            return AgentResult(ok=True, output="", attempts=1, final="结论：通过" if "只读复查" in prompt else "汇报：改了")
        w = listed_ask(ask)
        self.assertIn("on_quiet", inspect.signature(w).parameters, "程序看得到原函数认哪些参数")
        self.assertEqual(w("x", "写代码", ".").final, "汇报：改了")
        self.assertIsNotNone(block(w("x", "只读复查：看改动", ".").final))

    def test_reply_only_in_output_is_converted(self):
        agy = '{"conversation_id":"abc","status":"SUCCESS","response":"结论：通过\\n\\n- 无问题"}'
        for out in ("结论：通过\n符合需求", agy):
            w = listed_ask(lambda a, p, c, **kw: AgentResult(ok=True, output=out, attempts=1))
            self.assertEqual(block(w("agy", "只读复查", ".").final), {"结论": "通过", "问题": []}, out)


if __name__ == "__main__":
    unittest.main()
