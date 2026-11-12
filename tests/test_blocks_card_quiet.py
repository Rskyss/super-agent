"""积木库：卡片前后的话（用户 2026-11-10 看截图提出，三问都选推荐）

1. 出卡后管家不再说话：卡一出来这一轮就结束（程序保证）。卡上写了会替换哪一块，旧卡显示已失效，信息不丢。
   出卡被程序拦下（没出成）时照常让管家说原因。项目窗口不变。
2. 卡前的话收短：基本规矩写明出卡前只说要接什么、准备什么、去哪拿；没有选择题时不写推荐理由。
3. 接大模型 API 的卡去掉“服务商”一行（标题已有名字），地址留着（见 chat_blocks_check.js）。
"""
import unittest

from acceptance_helpers import tool_call
from desktop.clerk import blocks_tools
from test_blocks_butler import ButlerCase

TASK = {"title": "按钮改蓝色", "do": "首页按钮改蓝", "not_do": "不改别的页面", "done_when": "打开首页按钮是蓝的"}
VOLC = {"provider": "other", "name": "火山引擎", "base_url": "https://ark.cn-beijing.volces.com/api/v3"}


class QuietAfterCard(ButlerCase):
    def said(self, events):
        return [e["text"] for e in events if e.get("type") == "message"]

    def test_turn_ends_when_card_shown(self):
        _, events = self.run_steps([
            {"text": "去火山方舟控制台建一把 API 密钥，填进下面的卡里点检测。",
             "tool_calls": [tool_call("propose_connect_api", VOLC)]},
            {"text": "卡已经出来了，在屏幕上等你填写。请注意两点……"},
        ], text="给我接上火山引擎的大模型")
        self.assertEqual(len(self.client.calls), 1, "出卡后不再问模型")
        self.assertTrue(self.proposals(events))
        words = "\n".join(self.said(events))
        self.assertIn("去火山方舟控制台", words, "卡上面的话照常显示")
        self.assertNotIn("卡已经出来了", words)
        self.assertNotIn("这次没有得到回复", words)
        self.assertTrue(any(e.get("type") == "turn_done" for e in events))

    def test_card_without_words_no_fallback(self):
        _, events = self.run_steps([{"tool_calls": [tool_call("propose_connect_search", {})]},
                                    {"text": "卡在下面"}])
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue(self.proposals(events))
        self.assertEqual(self.said(events), [], "只出卡不说话，也不补“没有得到回复”")

    def test_card_after_lookups(self):
        _, events = self.run_steps([
            {"tool_calls": [tool_call("scan_environment", {})]},
            {"text": "要一把火山方舟的 API 密钥。", "tool_calls": [tool_call("propose_connect_api", VOLC)]},
            {"text": "多余的话"},
        ])
        self.assertEqual(len(self.client.calls), 2)
        self.assertNotIn("多余的话", "\n".join(self.said(events)))

    def test_refused_card_still_explained(self):
        bad = {"provider": "other", "name": "火山引擎"}  # 没给地址：程序不出卡
        _, events = self.run_steps([{"tool_calls": [tool_call("propose_connect_api", bad)]},
                                    {"text": "地址没填对，我再查一下。"}])
        self.assertEqual(len(self.client.calls), 2, "没出成卡：管家要说原因")
        self.assertFalse(self.proposals(events))
        self.assertIn("地址没填对", "\n".join(self.said(events)))

    def test_rest_of_batch_skipped(self):
        """Codex 复查第 1 条：同一批里出卡之后的工具不再执行（不会马上被第二张卡顶掉，也不会卡后还在忙）"""
        _, events = self.run_steps([{"tool_calls": [
            tool_call("propose_connect_cli", {"template": "codex"}, call_id="a"),
            tool_call("propose_connect_cli", {"template": "cursor"}, call_id="b"),
            tool_call("scan_environment", {}, call_id="c")]}])
        names = [e.get("name") for e in self.proposals(events)]
        self.assertEqual(names, ["Codex"], "只出第一张")
        self.assertFalse([e for e in events if e.get("type") == "proposal_done"], "Codex 卡不能被马上顶掉")
        labels = [e.get("label") for e in events if e.get("type") == "activity"]
        self.assertEqual(labels, ["出提议"], labels)

    def test_tools_before_card_in_batch_still_run(self):
        _, events = self.run_steps([{"tool_calls": [
            tool_call("scan_environment", {}, call_id="a"),
            tool_call("propose_connect_api", VOLC, call_id="b")]}])
        labels = [e.get("label") for e in events if e.get("type") == "activity"]
        self.assertEqual(labels, ["查电脑", "出提议"], labels)

    def test_first_turn_with_card_gets_title(self):
        """Codex 复查第 2 条：第一句话就出了卡，也要起标题"""
        for steps in ([{"text": "要一把火山方舟的 API 密钥。", "tool_calls": [tool_call("propose_connect_api", VOLC)]}],
                      [{"tool_calls": [tool_call("propose_connect_search", {})]}]):
            with self.subTest(steps=steps):
                cid, _ = self.run_steps(steps, text="给我接上火山引擎的大模型")
                from acceptance_helpers import wait_until
                wait_until(lambda: [t for t in self.desk.list(str(self.home))["threads"]
                                    if t["id"] == cid and t["title"] == "标题"], message="没起标题")

    def test_project_window_unchanged(self):
        self.client.steps = [{"tool_calls": [tool_call("propose_task", TASK)]}, {"text": "确认框在下面"}]
        _, events = self.talk("把按钮改成蓝色", where=self.project)
        self.assertTrue(self.proposals(events))
        self.assertEqual(len(self.client.calls), 2, "项目窗口出卡后照旧让行政说话")


class HelperStrict(ButlerCase):
    def test_refused_card_reason_comes_from_model_messages(self):
        """Codex 复查第 3 条：没出成卡时，测试要看交给模型的那句，不能拿出卡工具的返回值顶替"""
        _, events, results = self.call("propose_connect_api", {"provider": "other", "name": "x"})
        self.assertFalse(self.proposals(events))
        self.assertEqual(results, self.tool_results(len(self.client.calls) - 1))
        self.assertIn("地址", results[0])


class Rules(unittest.TestCase):
    def test_words_before_card(self):
        role = blocks_tools.role_text()
        self.assertIn("出提议前", role)
        self.assertIn("出提议后", role)
        self.assertIn("没有要用户选的", role, "没有选择题时不写推荐理由")


if __name__ == "__main__":
    unittest.main()
