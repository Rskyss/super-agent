"""68b 补充验收：新叫法经过真实对话兜底和聊天记账后仍保持原有行为。

文字扫描、基本识别词例和内部名字由 test_acceptance_68b_naming.py 覆盖。
这里只补完整提醒流程、成功/失败混合计数、历史动作重放；全用临时资料和假模型。
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from acceptance_helpers import tool_call
from test_acceptance_54_blocks_clerk import BlocksCase


class ProposalReminderFlow(BlocksCase):
    def test_new_claim_can_recover_by_really_publishing_proposal(self):
        self.client.steps = [
            {"text": "安装提议已经弹出来了，请点确认。"},
            {"tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]},
            {"text": "不该在提议之后继续问模型。"},
        ]
        _, events = self.talk("接入 Codex")
        self.assertEqual([p["name"] for p in self.proposals(events)], ["Codex"])
        self.assertEqual(len(self.client.calls), 2, "提醒后出提议，本轮立即结束")
        messages, tools = self.client.calls[1]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("这是系统提示", messages[-1]["content"])
        self.assertIn("没有出提议", messages[-1]["content"])
        self.assertNotRegex(messages[-1]["content"], r"提议卡|出卡")
        self.assertIn("propose_connect_cli", {t["function"]["name"] for t in tools or []})

    def test_new_pointer_without_open_proposal_gets_reminder_and_honest_reply(self):
        correction = "刚才没有出提议，还需要先核实安装方式。"
        self.client.steps = [{"text": "把密钥填进提议里。"}, {"text": correction},
                             {"text": "不应该第三次问模型。"}]
        _, events = self.talk()
        self.assertEqual(len(self.client.calls), 2)
        self.assertFalse(self.proposals(events))
        self.assertTrue(any(correction in e.get("text", "") for e in events
                            if e.get("type") == "message"), events)

    def test_repeating_new_claim_gets_only_one_reminder(self):
        self.client.steps = [{"text": "接入提议出好了，点确认就行。"},
                             {"text": "新的提议已经弹出来了。"},
                             {"tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]}]
        _, events = self.talk()
        self.assertEqual(len(self.client.calls), 2, "连续声称已出提议也只提醒一次")
        self.assertFalse(self.proposals(events), "第三步不能被执行")

    def test_new_claim_reminder_allowance_resets_on_next_user_turn(self):
        self.client.steps = [{"text": "安装提议已经弹出来了。"}, {"text": "刚才没有出提议。"}]
        cid, _ = self.talk()
        self.client.steps = [{"text": "接入提议出好了，点确认就行。"}, {"text": "这次也没有出提议。"}]
        _, events = self.talk("再试一次", cid)
        self.assertEqual(len(self.client.calls), 4, "每个用户回合各有一次提醒机会")
        self.assertFalse(self.proposals(events))
        for index in (1, 3):
            self.assertIn("这是系统提示", self.client.calls[index][0][-1]["content"])

    def test_new_claim_while_proposal_open_names_it_and_can_publish_replacement(self):
        self.client.steps = [{"tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]}]
        cid, first_events = self.talk("接入 Codex")
        first_id = self.proposals(first_events)[0]["id"]
        self.client.steps = [
            {"text": "Cursor 的接入提议已经弹出来了。"},
            {"tool_calls": [tool_call("propose_connect_cli", {"template": "cursor"}, call_id="cursor")]},
            {"text": "提议之后不再说话。"},
        ]
        _, events = self.talk("再接入 Cursor", cid)
        self.assertEqual([p["name"] for p in self.proposals(events)], ["Codex", "Cursor"])
        self.assertEqual(len(self.client.calls), 3)
        reminder = self.client.calls[2][0][-1]["content"]
        self.assertIn("上面还有", reminder)
        self.assertIn("Codex", reminder, "要点名已有的待处理提议")
        self.assertIn("提议", reminder)
        self.assertNotRegex(reminder, r"提议卡|出卡|新的卡")
        self.assertTrue(any(e.get("type") == "proposal_done" and e.get("id") == first_id
                            and e.get("state") == "replaced" for e in events))

    def test_new_pointer_to_existing_proposal_does_not_add_reminder(self):
        self.client.steps = [{"tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]}]
        cid, _ = self.talk("接入 Codex")
        self.client.steps = [{"text": "提议就在上面。"}, {"text": "不该再问。"}]
        _, events = self.talk("刚才的提议在哪里", cid)
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(len(self.proposals(events)), 1, "仅指向已有提议，不重复出提议")

    def test_failed_tool_then_new_claim_is_reminded_and_failure_keeps_separate_label(self):
        self.client.steps = [
            {"tool_calls": [tool_call("propose_connect_cli", {}, call_id="bad")]},
            {"text": "安装提议已经弹出来了。"},
            {"tool_calls": [tool_call("propose_connect_cli", {"template": "codex"}, call_id="good")]},
            {"text": "提议之后不再问。"},
        ]
        _, events = self.talk("接入 Codex")
        self.assertEqual([p["name"] for p in self.proposals(events)], ["Codex"])
        self.assertEqual(len(self.client.calls), 3, "失败的工具调用不能算本轮已出提议")
        actions = [e for e in events if e.get("type") == "activity"]
        self.assertEqual([e["label"] for e in actions], ["没出提议", "出提议"])
        results = self.tool_results(1)
        self.assertTrue(any("要填 template" in result for result in results), results)
        self.assertIn("这是系统提示", self.client.calls[2][0][-1]["content"])

    def test_questions_future_content_and_verb_do_not_start_reminder_flow(self):
        for words in ("要不要我为你生成一个安装提议？", "如果安装提议出来了，点确认就行。",
                      "接入提议出来了以后程序会检测。", "等安装提议弹出了再点确认。",
                      "提议的检测结果出来了：通过。", "我提议给你换个模型。"):
            with self.subTest(words=words):
                before = len(self.client.calls)
                self.client.steps = [{"text": words},
                                     {"tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]}]
                _, events = self.talk()
                self.assertEqual(len(self.client.calls) - before, 1)
                self.assertFalse(self.proposals(events))

    def test_new_claim_and_pointer_do_not_enable_guard_in_project_window(self):
        for words in ("任务的提议已经弹出来了，请点确认。", "提议就在上面。"):
            with self.subTest(words=words):
                before = len(self.client.calls)
                self.client.steps = [{"text": words}, {"text": "不该被调用。"}]
                _, events = self.talk(where=self.project)
                self.assertEqual(len(self.client.calls) - before, 1)
                self.assertFalse(self.proposals(events))


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class MixedActionSummaries(unittest.TestCase):
    def summarize_events(self, events):
        """用真实 ChatCore 重放事件，避免只检查源码或另写一份摘要算法。"""
        script = r"""
const fs = require('fs');
const C = require(process.argv[1]);
const model = new C.Model();
JSON.parse(fs.readFileSync(0, 'utf8')).forEach(event => model.apply(event));
const groups = model.blocks.filter(block => block.kind === 'ai')
  .flatMap(block => block.parts.filter(part => part.type === 'acts'));
process.stdout.write(JSON.stringify(groups.map(group => C.actsSummary(group.list))));
"""
        proc = subprocess.run(
            ["node", "-e", script, str(Path(__file__).resolve().parents[1] / "desktop/static/chat.js")],
            input=json.dumps(events, ensure_ascii=False), capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_replayed_mixed_labels_keep_success_and_failure_counts_separate(self):
        events = [
            {"type": "activity", "seq": 1, "history": True, "item": "old-ok",
             "label": "出卡：已向用户出示确认框", "status": "done"},
            {"type": "activity", "seq": 2, "history": True, "item": "new-ok",
             "label": "出提议：已向用户出示确认框", "status": "done"},
            {"type": "activity", "seq": 3, "history": True, "item": "old-no",
             "label": "没出卡：缺字段", "status": "done"},
            {"type": "activity", "seq": 4, "history": True, "item": "new-no",
             "label": "没出提议：缺字段", "status": "done"},
        ]
        self.assertEqual(self.summarize_events(events), ["出了 2 个提议 · 2 次没出成提议"])

    def test_activity_update_and_duplicate_event_do_not_count_proposal_twice(self):
        failed = {"type": "activity", "seq": 3, "item": "bad", "label": "没出提议", "status": "done"}
        events = [
            {"type": "activity", "seq": 1, "item": "good", "label": "出卡", "status": "started"},
            {"type": "activity", "seq": 2, "item": "good", "label": "出提议", "status": "done"},
            failed, failed,
            {"type": "activity", "seq": 4, "item": "old-bad", "label": "没出卡", "status": "done"},
        ]
        self.assertEqual(self.summarize_events(events), ["出了 1 个提议 · 2 次没出成提议"])


if __name__ == "__main__":
    unittest.main()
