"""54 号单验收后复查出的问题：先写测试（修前不通过），再修。

- 查安装不许编版本号；找不到要说“找不到”；核实名单前不加多余的行
- 安装提议的编号不能被安装方式编号盖掉：重开对话还在，两段对话互不干扰，确认过的不能再确认一遍
- 接法卡放在提议的 block 字段，card 字段仍只表示项目窗口的任务卡编号
- 重复确认返回同样的结果；确认后的回话不再说“已生成卡片”
- 认得的服务商地址不能被行政改；不认得的按“其他”处理；钥匙串账号按地址分开；编号不撞车
- 预置编号不能被行政起草的卡占用；会替换基础层里已有的卡要说出来
- “连续 3 次就停”按一段对话里行政起草的卡算，换编号绕不过去；预置模板不受影响
- 聊天里更多种密钥要打码；密钥提醒要存下来，重开对话还在
"""
import json
import time
from pathlib import Path

from acceptance_helpers import write_script
from collab import blocks
from collab.providers import TENCENT_BASE_URL
from desktop.clerk import tasks as clerk_tasks
from desktop.clerk.desk import ClerkDesk
from test_acceptance_54_blocks_clerk import BlocksCase


def custom(card_id, command="demo-cli"):
    return {"id": card_id, "kind": "cli", "name": f"演示{card_id}", "command": command, "run": ["-p", "{prompt}"]}


class ReadToolFixes(BlocksCase):
    def test_missing_command_says_not_found(self):
        _, _, results = self.call("check_installed", {"command": "not-here-cli"})
        self.assertIn("找不到", results[0])

    def test_no_made_up_version(self):
        write_script(self.bin / "demo-cli", "exit 3\n")
        _, _, results = self.call("check_installed", {"command": "demo-cli"})
        self.assertNotIn("1.2.3", results[0], "查不出版本就说查不出，不能编")
        self.assertIn("查不出", results[0])

    def test_listed_options_have_no_stray_line(self):
        _, _, results = self.call("install_options", {"tool": "codex"})
        listed = results[0].split("（你还没翻")[0]  # 积木库管家方案：第一次查安装方式会附上手册，只看名单本身
        self.assertNotIn("官网", listed)


class InstallProposalFixes(BlocksCase):
    def install(self, cid=""):
        self.client.steps = [{"tool_calls": [{"id": "c1", "name": "propose_install",
                                              "arguments": json.dumps({"install_id": "codex-npm"})}]},
                             {"text": "好的"}]
        cid, events = self.talk("帮我装", cid=cid)
        return cid, self.proposals(events)[-1]

    def test_proposal_id_is_not_install_id(self):
        _, prop = self.install()
        self.assertNotEqual(prop["id"], "codex-npm")
        self.assertEqual(prop["install"], "codex-npm")

    def test_survives_reopen(self):
        cid, prop = self.install()
        reopened = ClerkDesk(lambda: self.client)._conversation(str(self.home), cid)
        ids = [e["id"] for e in reopened.events if e.get("type") == "proposal"]
        self.assertEqual(ids, [prop["id"]], "关掉再打开，安装卡片还在")

    def test_two_conversations_do_not_clash(self):
        cid1, prop1 = self.install()
        _, prop2 = self.install()
        self.assertNotEqual(prop1["id"], prop2["id"])
        answer = clerk_tasks.decide(str(self.home), cid1, prop1["id"], "confirm")
        self.assertEqual(answer["install"], "codex-npm")

    def test_confirmed_install_runs_once(self):
        cid, prop = self.install()
        first = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        self.assertFalse(first["repeat"])
        self.assertEqual(first["card"], "")
        _, prop2 = self.install(cid)
        again = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        self.assertTrue(again["repeat"], "确认过的再点一次只是重复，不能再装一遍")
        self.assertEqual(again["install"], "codex-npm")
        self.assertEqual(clerk_tasks.load_proposal(str(self.home), prop["id"])["state"], "confirmed")


class ConfirmFixes(BlocksCase):
    def test_block_field_and_repeat_same_result(self):
        write_script(self.bin / "demo-cli", "exit 0\n")
        cid, events, _ = self.call("propose_connect_cli", {"card": custom("demo")})
        prop = self.proposals(events)[0]
        self.assertEqual(prop["card"], "", "card 只表示项目窗口的任务卡")
        self.assertEqual(prop["block"]["id"], "demo")
        first = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        again = clerk_tasks.decide(str(self.home), cid, prop["id"], "confirm")
        self.assertTrue(again["repeat"])
        self.assertEqual(dict(first, repeat=True), again, "重复确认返回同样的结果")

    def test_cancel_and_replace_send_plain_card_field(self):
        cid, events, _ = self.call("propose_connect_cli", {"template": "cursor"})
        first = self.proposals(events)[0]
        self.client.steps = [{"tool_calls": [{"id": "c2", "name": "propose_connect_cli",
                                              "arguments": json.dumps({"template": "agy"})}]}, {"text": "好"}]
        _, events = self.talk("换一个", cid=cid)
        replaced = [e for e in events if e.get("type") == "proposal_done" and e.get("id") == first["id"]]
        self.assertEqual(replaced[0]["card"], "")
        second = self.proposals(events)[-1]
        answer = clerk_tasks.decide(str(self.home), cid, second["id"], "cancel")
        self.assertEqual(answer["card"], "")

    def test_confirm_reply_is_not_task_card_text(self):
        cid, events, _ = self.call("propose_install", {"install_id": "codex-npm"})
        self.desk.decide_proposal(str(self.home), cid, self.proposals(events)[0]["id"], "confirm")
        cid2, events2, _ = self.call("propose_connect_cli", {"template": "cursor"})
        self.desk.decide_proposal(str(self.home), cid2, self.proposals(events2)[0]["id"], "confirm")
        for c in (cid, cid2):
            texts = [e.get("text", "") for e in self.desk._conversation(str(self.home), c).events
                     if e.get("type") == "message"]
            self.assertFalse(any("已生成卡片" in t for t in texts), texts)
            self.assertFalse(any("{" in t or "None" in t for t in texts), texts)


class ConnectApiFixes(BlocksCase):
    def api(self, **args):
        cid, events, results = self.call("propose_connect_api", dict({"name": "某家"}, **args))
        props = self.proposals(events)
        return cid, (props[0] if props else None), results[0]

    def test_known_provider_address_cannot_be_changed(self):
        _, prop, _ = self.api(provider="tencent", model="deepseek/deepseek-flash", base_url="https://evil.example/v1")
        self.assertEqual(prop["block"]["base_url"], TENCENT_BASE_URL, "密钥不能被引到别的地址")

    def test_unknown_provider_treated_as_other(self):
        _, prop, result = self.api(provider="deepseek", model="deepseek-chat")
        self.assertIsNone(prop)
        self.assertIn("地址", result)
        _, prop, _ = self.api(provider="deepseek", model="deepseek-chat", base_url="https://api.deepseek.com/v1")
        self.assertEqual(prop["block"]["provider"], "other")

    def test_key_account_split_by_address(self):
        _, a, _ = self.api(provider="other", model="m-1", base_url="https://a.example/v1")
        _, b, _ = self.api(provider="other", model="m-1", base_url="https://b.example/v1")
        self.assertEqual(a["block"]["key_account"], "other:a.example")
        self.assertEqual(b["block"]["key_account"], "other:b.example")

    def test_ids_do_not_collide_with_saved_cards(self):
        _, a, _ = self.api(provider="bailian", model="qwen-plus")
        blocks.save_card(a["block"], root=self.home)
        _, b, _ = self.api(provider="other", model="m-1", base_url="https://a.example/v1")
        _, c, _ = self.api(provider="other", model="m-1", base_url="https://a-example.com/v1")
        self.assertEqual(len({a["block"]["id"], b["block"]["id"], c["block"]["id"]}), 3, "不同的平台不能用同一个编号")
        blocks.validate_card(b["block"])
        _, again, _ = self.api(provider="bailian", model="qwen.plus")
        self.assertEqual(again["block"]["id"], a["block"]["id"], "按整家接（2026-11-10）：同一家再接一次，就是替换原来那块")
        self.assertTrue(again["replaces"])


class ConnectApiEdgeFixes(BlocksCase):
    def test_long_model_without_name(self):
        model = "vendor/" + "x" * 50
        _, events, results = self.call("propose_connect_api", {"provider": "other", "model": model, "name": "",
                                                              "base_url": "https://a.example/v1"})
        self.assertEqual(len(self.proposals(events)), 1, results[0])

    def test_bad_address_refused(self):
        _, events, results = self.call("propose_connect_api", {"provider": "other", "model": "m", "name": "x",
                                                              "base_url": "https://a b/v1"})
        self.assertEqual(self.proposals(events), [])
        self.assertIn("地址", results[0])


class ConnectCliFixes(BlocksCase):
    def test_builtin_id_reserved(self):
        _, events, results = self.call("propose_connect_cli", {"card": custom("codex")})
        self.assertEqual(self.proposals(events), [])
        self.assertIn("预置", results[0])

    def test_replacing_saved_card_is_shown(self):
        write_script(self.bin / "demo-cli", "exit 0\n")
        blocks.save_card(dict(custom("demo"), name="旧的演示"), root=self.home)
        _, events, _ = self.call("propose_connect_cli", {"card": custom("demo")})
        self.assertEqual(self.proposals(events)[0]["replaces"], "旧的演示")
        _, events, _ = self.call("propose_connect_cli", {"card": custom("fresh")})
        self.assertFalse(self.proposals(events)[0].get("replaces"))


class ThreeStrikesFixes(BlocksCase):
    def fail_once(self, cid, card_id):
        self.client.steps = [{"tool_calls": [{"id": "c1", "name": "propose_connect_cli",
                                              "arguments": json.dumps({"card": custom(card_id)})}]},
                             {"text": "好"}]
        before = len(self.proposals(self.desk.events(str(self.home), cid, 0, wait=0)["events"])) if cid else 0
        cid, events = self.talk("接这个", cid=cid)
        props = self.proposals(events)[before:]  # 事件是整段对话的，只看这一轮新出的提议
        if not props:
            return cid, None
        clerk_tasks.decide(str(self.home), cid, props[-1]["id"], "confirm")
        blocks.add_record(card_id, {"type": "check", "ok": False, "reason": "没建出文件"}, root=self.home)
        return cid, props[-1]

    def test_renaming_does_not_bypass(self):
        cid = ""
        for name in ("demo1", "demo2", "demo3"):
            cid, prop = self.fail_once(cid, name)
            self.assertIsNotNone(prop)
        _, prop = self.fail_once(cid, "demo4")
        self.assertIsNone(prop, "换个编号也不行")
        self.assertIn("3 次", self.tool_results(-1)[0])

    def test_template_never_locked(self):
        for _ in range(5):
            blocks.add_record("codex", {"type": "check", "ok": False, "reason": "没登录"}, root=self.home)
        _, events, _ = self.call("propose_connect_cli", {"template": "codex"})
        self.assertEqual(len(self.proposals(events)), 1, "登录好了还要能再接")

    def test_new_conversation_starts_fresh(self):
        cid = ""
        for name in ("demo1", "demo2", "demo3"):
            cid, _ = self.fail_once(cid, name)
        _, prop = self.fail_once("", "demo4")
        self.assertIsNotNone(prop)

    def test_same_id_counted_once(self):
        """改好卡、用同一个编号再提议是最常见的流程：后一张的失败不能被前一张再算一次（第二轮复查）。"""
        self.client.steps = [{"tool_calls": [{"id": "c1", "name": "propose_connect_cli",
                                              "arguments": json.dumps({"card": custom("demo")})}]}, {"text": "好"}]
        cid, events = self.talk("接这个")
        clerk_tasks.decide(str(self.home), cid, self.proposals(events)[-1]["id"], "confirm")  # 确认了但检测没跑起来
        for _ in range(2):
            cid, prop = self.fail_once(cid, "demo")
            self.assertIsNotNone(prop)
        cid, prop = self.fail_once(cid, "demo")
        self.assertIsNotNone(prop, "实际只失败了 2 次，还能再提议")

    def test_pass_resets(self):
        cid = ""
        for name in ("demo1", "demo2"):
            cid, _ = self.fail_once(cid, name)
        cid, _ = self.fail_once(cid, "demo3")
        time.sleep(0.01)
        blocks.add_record("demo3", {"type": "check", "ok": True, "reason": "通了"}, root=self.home)
        _, prop = self.fail_once(cid, "demo5")
        self.assertIsNotNone(prop)


class PastedKeyFixes(BlocksCase):
    KEYS = (
        "0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOp",  # 智谱
        "3f2a9c1e-7b4d-4e8a-9c2f-1a2b3c4d5e6f",  # 火山方舟
        "AKID" + "Fake1234" * 4,  # 腾讯云
        "api_key: Zx9Yw8Vu7Ts6Rq5P",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEFghiJKLmnoPQRstu",  # 令牌
        "bce-v3/ALTAK-abcdefghij1234567890/abcdef",  # 百度千帆
    )

    def test_more_key_shapes_masked(self):
        for key in self.KEYS:
            self.client.calls.clear()
            cid, _ = self.talk(f"给你 {key} 用")
            sent = json.dumps(self.client.calls, ensure_ascii=False)
            self.assertNotIn(key.split(" ")[-1], sent, key)

    def test_notice_kept_after_reopen(self):
        cid, _ = self.talk("我的密钥 sk-abcdefghijklmnopqrstuvwxyz0123456789")
        reopened = ClerkDesk(lambda: self.client)._conversation(str(self.home), cid)
        texts = [e.get("text", "") for e in reopened.events if e.get("history")]
        self.assertTrue(any("作废" in t for t in texts), "重开对话还要看得到作废提醒")
        sent = json.dumps(self.client.calls, ensure_ascii=False)
        self.assertNotIn("作废", sent, "提醒只给用户看，不进模型的对话")

    def test_project_window_text_untouched(self):
        cid = self.desk.send(self.project, "", "0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOp")
        from acceptance_helpers import wait_idle
        wait_idle(self.desk, self.project, cid)
        stored = "".join(p.read_text(encoding="utf-8")
                         for p in (Path(self.project) / ".collab" / "行政对话").glob("*.jsonl"))
        self.assertIn("AbCdEfGhIjKlMnOp", stored, "项目窗口不变")


if __name__ == "__main__":
    import unittest
    unittest.main()
