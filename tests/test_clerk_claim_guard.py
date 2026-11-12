"""第 9 项整体验收发现（2026-11-09）：行政的模型有时说“提议卡已经弹出来了”，其实这一轮没调用出卡的工具，
用户什么都看不到（真实走查 7 次里 3 次）。用户定：程序兜底——回话里说出了卡、这一轮却没出，就自动提醒它一次，
让它真的出卡或如实说没出。只提醒一次，只在出错时多问一次。
复查后收窄：只在积木库里生效（项目窗口的卡片对话里会把同一条修改记两遍）；这段对话里还有没处理的卡时不提醒
（行政说的可能是上面那张）；出卡工具返回错误不算出过卡。
"""
import json
import unittest

from acceptance_helpers import ProjectCase, ScriptedClient, tool_call, wait_idle
from desktop.clerk import desk as desk_module
from test_acceptance_55_blocks_server import BlocksCase

CLAIM = "接法卡已经弹出来了，你点一下确认，程序就会把它接进基础层。"


class ClaimGuard(BlocksCase):
    def talk(self, steps):
        client = ScriptedClient(steps, default="好的。")
        self.server.blocks_clerk = client
        status, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": "",
                                                      "text": "我想装 Codex"})
        self.assertEqual(status, 200, data)
        events = wait_idle(self.server.desk, str(self.home), data["conversation"])
        return client, events

    def test_claim_without_card_gets_one_reminder(self):
        client, events = self.talk([{"text": CLAIM},
                                    {"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]},
                                    {"text": "请看确认框。"}])
        self.assertTrue([e for e in events if e.get("type") == "proposal"], "提醒后真的出了卡")
        last = client.calls[1][0][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("这是系统提示", last["content"], "第二次问的时候，最后一句是提醒本身")
        self.assertIsNotNone(client.calls[1][1], "提醒那一次要带着工具，它才能真的出卡")

    def test_reminds_only_once(self):
        client, events = self.talk([{"text": CLAIM}, {"text": "提议卡已经弹出来了。"}, {"text": "多余的一次"}])
        self.assertEqual(len(client.calls), 2, "只提醒一次")
        self.assertFalse([e for e in events if e.get("type") == "proposal"])

    def test_real_card_no_reminder(self):
        client, events = self.talk([{"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]},
                                    {"text": CLAIM}])
        self.assertEqual(len(client.calls), 1, "这一轮真出了卡：不提醒，出卡后这一轮就结束（2026-11-10）")
        self.assertTrue([e for e in events if e.get("type") == "proposal"])

    def test_plain_talk_no_reminder(self):
        for text in ("要不要我现在把它接进基础层？（我先把接法卡给你看，你点确认才会真正接。）",
                     "要我按这个思路出接法卡吗？",
                     "如果这次还是没看到，可能是弹窗被浏览器拦住了。"):
            with self.subTest(text=text):
                client, _ = self.talk([{"text": text}, {"text": "多余的一次"}])
                self.assertEqual(len(client.calls), 1, "没说出了卡，不提醒")

    def test_claim_patterns(self):
        for text in ("接大模型 API 的提议卡已经弹出来了，信息是：腾讯云",
                     "好，按已验证能用的模型名改。新提议卡出来了：腾讯云",
                     "确认框已经出好了，你看一下", "我已经弹出确认框了",
                     "提议卡已弹出，请点确认", "提议卡已经弹出，请点确认", "确认框已弹出",
                     "提议卡已发出", "提议卡已发送", "提议卡已给出", "新的接法卡已出",
                     "已为你生成提议卡", "我已经给你出了一张提议卡", "我给你出了接法卡，点确认就行",
                     "确认框已显示", "接法卡已经出了，就是页面上给 agy 用的那张。",
                     "让你久等了，接法卡已经出好了", "稍等，提议卡已经弹出来了", "要是没看到，提议卡其实已经弹出来了",
                     "Codex、Cursor 等工具的接法卡都已经出好了", "提议卡（含检测命令）已经弹出来了",
                     "提议卡已经弹出来了，你可以点确认。", "接法卡已经出好了，点确认之前可以先看看命令。",
                     "确认框已经出来了，有问题吗？", "提议卡已经弹出来了，能不能看到？", "我已经把接法卡发给你了"):
            with self.subTest(text=text):
                self.assertTrue(desk_module.claims_card(text))
        for text in ("我先把接法卡给你看，你点确认才会真正接。", "要我出接法卡吗？", "已确认接入「Codex」。", "",
                     "接法卡的检测结果出来了：通过", "接法卡检测已经出结果了", "接法卡里的命令已经出错了",
                     "上次的提议卡已经发过了", "如果确认框出来了，点确认就行", "确认框出来了以后程序会检测",
                     "如果这次还是没看到，可能是弹窗被浏览器拦住了。",
                     "要我给你出一张接法卡吗？", "要不要我为你生成一张提议卡？", "我可以给你出接法卡，你看行吗",
                     "给你出接法卡之前，我先查一下装没装", "我已经发现接法卡里的模型名写错了",
                     "刚才已经出错的那张接法卡，我改好模型名了", "已经出过三次接法卡了，都没通过",
                     "等确认框弹出了再点"):
            with self.subTest(text=text):
                self.assertFalse(desk_module.claims_card(text))

    def test_open_card_above_reminder_names_it(self):
        """上一轮出的卡还没处理，这一轮说“卡已经出来了，在上面”：提醒里点名上面那张，让它照实说；只多问一次"""
        client = ScriptedClient([{"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]},
                                 {"text": "提议卡已经出来了，在上面，点确认就行。"},
                                 {"text": "多余的一次"}])
        self.server.blocks_clerk = client
        _, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": "", "text": "接 Codex"})
        cid = data["conversation"]
        wait_idle(self.server.desk, str(self.home), cid)
        self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": cid, "text": "卡在哪"})
        wait_idle(self.server.desk, str(self.home), cid)
        self.assertEqual(len(client.calls), 3, "上面有没处理的卡也提醒一次，只一次（出卡那一轮只问一次模型）")
        last = client.calls[2][0][-1]["content"]
        self.assertIn("上面还有", last)
        self.assertIn("Codex", last, "提醒里点名上面那张卡")

    def test_second_card_claimed_while_first_open(self):
        """连着要两张卡：Codex 卡还没点，又说“Cursor 的接法卡已经弹出来了”却没出——照样提醒，提醒后真出卡"""
        client = ScriptedClient([{"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]},
                                 {"text": "Cursor 的接法卡已经弹出来了。"},
                                 {"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "cursor"},
                                                                       call_id="c2")]},
                                 {"text": "请看确认框。"}])
        self.server.blocks_clerk = client
        _, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": "", "text": "接 Codex"})
        cid = data["conversation"]
        wait_idle(self.server.desk, str(self.home), cid)
        self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": cid, "text": "再接 Cursor"})
        events = wait_idle(self.server.desk, str(self.home), cid)
        names = [e.get("name") for e in events if e.get("type") == "proposal"]
        self.assertIn("Cursor", names, "提醒后 Cursor 卡真的出来了")

    def same_conversation(self, *turns):
        """同一段对话里说几句，每句一组模型回话；返回全部事件"""
        cid, events = "", []
        for steps in turns:
            self.server.blocks_clerk = ScriptedClient(steps, default="好的。")
            _, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": cid, "text": "接"})
            cid = data["conversation"]
            events = wait_idle(self.server.desk, str(self.home), cid)
        return events

    def test_old_card_voided_on_screen(self):
        """同一段对话出新卡会作废上面没点的旧卡。出卡后这一轮就结束（2026-11-10），行政不再转述：
        旧卡在屏幕上自己变成“已换成新的提议”"""
        events = self.same_conversation(
            [{"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]}],
            [{"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "cursor"}, call_id="c2")]}])
        cards = [e for e in events if e.get("type") == "proposal"]
        codex = [c for c in cards if c.get("name") == "Codex"][0]
        done = [e for e in events if e.get("type") == "proposal_done" and e.get("id") == codex["id"]]
        self.assertEqual(done[-1]["state"], "replaced")

    def test_voided_install_card_on_screen(self):
        events = self.same_conversation(
            [{"text": "", "tool_calls": [tool_call("propose_install", {"install_id": "codex-npm"})]}],
            [{"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "cursor"}, call_id="c2")]}])
        install = [e for e in events if e.get("type") == "proposal" and e.get("proposalKind", e.get("kind")) == "install"]
        self.assertTrue(install, events)
        done = [e for e in events if e.get("type") == "proposal_done" and e.get("id") == install[0]["id"]]
        self.assertEqual(done[-1]["state"], "replaced")

    def test_replacement_on_card(self):
        """卡会替换基础层里的哪一块写在卡上（行政出卡后不再说话）"""
        from collab import blocks
        blocks.save_card(blocks.builtin_card("codex"))
        _, events = self.talk([{"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "codex"})]},
                               {"text": "请看确认框。"}])
        card = [e for e in events if e.get("type") == "proposal"][0]
        self.assertEqual(card.get("replaces") or (card.get("info") or {}).get("replaces"), "Codex", card)

    def test_failed_propose_still_counts_as_no_card(self):
        """出卡工具返回错误（缺字段）不算出过卡：接着说“卡出了”照样提醒"""
        client, events = self.talk([{"text": "", "tool_calls": [tool_call("propose_connect_cli", {})]},
                                    {"text": CLAIM},
                                    {"text": "", "tool_calls": [tool_call("propose_connect_cli", {"template": "codex"},
                                                                          call_id="c2")]},
                                    {"text": "请看确认框。"}])
        self.assertEqual(len(client.calls), 3, "出卡后这一轮就结束（2026-11-10）")
        self.assertTrue([e for e in events if e.get("type") == "proposal"])


class ProjectWindowUntouched(ProjectCase):
    """项目窗口的行政不做这个提醒（卡片对话里的修改会被记两遍；那边没走查过）"""

    def test_no_reminder_in_project_window(self):
        from desktop.clerk.desk import ClerkDesk
        client = ScriptedClient([{"text": "确认框已经出好了，你看一下"}, {"text": "多余的一次"}])
        desk = ClerkDesk(lambda: client)
        cid = desk.send(self.project, "", "我的修改呢")
        wait_idle(desk, self.project, cid)
        self.assertEqual(len(client.calls), 1)


class WordingFixes(unittest.TestCase):
    """同一次走查发现行政说错的三处，和积木库聊天框给读屏软件的名字"""

    def test_role_text(self):
        from pathlib import Path
        from desktop.clerk import blocks_tools  # 积木库管家方案：说明改名并拆成基本规矩 + 三本手册
        role = blocks_tools.role_text() + "".join(blocks_tools.manual_text(n) for n in blocks_tools.MANUALS)
        self.assertIn("不是云账号的 SecretId、SecretKey", role)
        self.assertIn("已经登录的，接进基础层时不用再登录", role)
        self.assertIn("编号相同才会替换", role)
        self.assertIn("提议上会写", role)  # 出提议后这一轮就结束，会替换哪个积木块由提议上写（2026-11-10；68b 改名）
        self.assertIn("查不了", role)
        self.assertIn("没有调用 propose_ 开头的工具，就不能说", role)

    def test_clerk_input_label(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "desktop" / "static" / "chat.js").read_text(encoding="utf-8")
        self.assertIn('setAttribute("aria-label", "给行政的消息")', js)
        self.assertIn('setAttribute("aria-label", "和行政的对话")', js)


if __name__ == "__main__":
    unittest.main()
