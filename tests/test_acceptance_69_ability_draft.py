"""施工单 69：管家起草能力卡片（后台）的验收契约。

只使用临时积木库、剧本管家、假 API / 搜索 / CLI；不调用真实外部工具。
规则测试保留真实校验、提议文件和事件；HTTP 测试保留真实服务、后台任务和成果。
"""
import copy
import inspect
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from acceptance_helpers import ScriptedClient, tool_call, wait_idle
import test_acceptance_68_abilities_page as page68
from collab import abilities, abilities_run, blocks, blocks_check
from desktop.clerk import blocks_tools, tasks
from desktop.clerk.client import ModelError
from desktop.clerk.desk import ClerkDesk


# 手工抄自施工单第 6 节，不从被测常量生成预期卡片。
TRANSLATE = {
    "id": "translate-en", "title": "翻译成英文",
    "description": "把一段中文翻译成英文，适合翻译说明、邮件",
    "block": "bailian", "model": "qwen3.7-plus",
    "inputSchema": {"type": "object", "properties": {
        "text": {"type": "string", "title": "要翻译的中文", "maxLength": 4000,
                 "examples": ["今天天气很好，我们去公园散步吧。"]}}, "required": ["text"]},
    "how": {"kind": "llm_api", "system": "你是翻译。只输出译文，不要解释，不要开场白。",
            "prompt": "把下面这段中文翻译成英文：\n\n{text}"},
    "output": {"type": "text", "check": {"min_chars": 1, "max_chars": 20000}},
}
INPUTS = {"text": "今天天气很好，我们去公园散步吧。"}
ENGLISH = "The weather is lovely today. Let's go for a walk in the park."
# 纯测试占位串，用来检验拒绝和打码，不读取任何真实凭据。
SECRET = "sk-card69FAKEcredentialabcdefghijklmnopqrstuvwxyz"


def translation(**changes):
    card = copy.deepcopy(TRANSLATE)
    card.update(changes)
    return card


class ProposalHelpers:
    def require_function(self, owner, name):
        fn = getattr(owner, name, None)
        self.assertTrue(callable(fn), f"施工单 69 要求实现 {owner.__name__}.{name}")
        return fn

    def conv(self, cid="conversation69"):
        events = []
        return SimpleNamespace(project=str(self.home), id=cid, emit=events.append, events=events)

    def proposal_files(self):
        return sorted((self.home / tasks.FOLDER).glob("*.json"))

    def draft(self, conv=None, card=None):
        conv = conv or self.conv()
        reply = self.require_function(tasks, "propose_ability")(
            conv, {"card": copy.deepcopy(card if card is not None else TRANSLATE)})
        self.assertTrue(reply.startswith("已向用户出示确认框"), reply)
        self.assertIn("试做", reply)
        events = [e for e in conv.events if e.get("type") == "proposal"]
        self.assertTrue(events, reply)
        return conv, events[-1]["id"]

    def load(self, pid):
        data = tasks.load_proposal(str(self.home), pid)
        self.assertIsNotNone(data, pid)
        return data

    def layer_snapshot(self):
        folder = self.home / abilities.ABILITY_DIR
        return folder.exists(), {str(p.relative_to(folder)): p.read_bytes()
                                 for p in folder.rglob("*") if p.is_file()}

    def record(self, pid, ok=False, **extra):
        result = dict(ok=ok, status="ok" if ok else "format", reason="成功完成" if ok else "字数不够",
                      run_id=abilities.new_run_id(), seconds=0.125, model="qwen3.7-plus")
        result.update(extra)
        self.require_function(tasks, "record_trial")(str(self.home), pid, result)
        return result


class DraftRules(ProposalHelpers, unittest.TestCase):
    """防止不合格草稿落盘、错误计数、丢失历史状态和只读工具泄露副作用。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name) / "积木库"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"SUPER_AGENT_BLOCKS_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        blocks.save_card({"id": "bailian", "kind": "llm_api", "name": "阿里百炼", "provider": "bailian",
                          "base_url": page68.BAILIAN_URL, "key_account": "bailian"}, self.home)
        blocks.add_record("bailian", {"type": "check", "ok": True, "status": "ok", "reason": "通了",
                                      "models": ["qwen3.7-plus", "qwen-flash"]}, self.home)

    def rejected(self, value, reason):
        conv = self.conv()
        before = {p.name: p.read_bytes() for p in self.proposal_files()}
        reply = self.require_function(tasks, "propose_ability")(conv, {"card": value})
        self.assertTrue(reply.startswith("没有出草稿："), reply)
        self.assertIn(reason, reply)
        self.assertEqual(conv.events, [], "拒绝草稿不能发事件或顶掉旧提议")
        self.assertEqual({p.name: p.read_bytes() for p in self.proposal_files()}, before)
        return reply

    def test_card_must_be_object(self):
        for value in (None, [], "{}", 1):
            with self.subTest(value=value):
                self.rejected(value, "card 要写成一组字段")

    def test_unknown_field_is_rejected_with_valid_repair_example(self):
        reply = self.rejected(translation(unknown="多余项"), "不认识")
        self.assertIn("照这个样子改了再出：", reply)
        example = getattr(tasks, "ABILITY_EXAMPLE", None)
        self.assertIsNotNone(example, "错误反馈需要施工单指定的 ABILITY_EXAMPLE")
        if isinstance(example, str):
            example = json.loads(example)
        self.assertEqual(example["id"], "translate-en")
        abilities.validate_ability(example)

    def test_undeclared_prompt_placeholder_is_rejected(self):
        card = translation()
        card["how"]["prompt"] += "{undeclared}"
        self.rejected(card, "undeclared")

    def test_api_cannot_return_list(self):
        self.rejected(translation(output={"type": "list", "check": {"min_items": 1}}), "文字")

    def test_unknown_primary_and_backup_models_are_rejected(self):
        for changes in ({"model": "invented-model"}, {"backup_models": ["invented-model"]}):
            with self.subTest(changes=changes):
                reply = self.rejected(translation(**changes), "invented-model")
                self.assertIn("模型只能从 read_block 给的名单里原样照抄", reply)

    def test_missing_block_is_rejected(self):
        self.rejected(translation(block="missing"), "基础层里没有积木块 missing")

    def test_ordinary_tool_cannot_be_used_for_cli_ability(self):
        blocks.save_card({"id": "ordinary", "kind": "cli", "name": "普通工具", "command": "ordinary",
                          "role": "tool", "probe": ["--version"]}, self.home)
        card = translation(block="ordinary", how={"kind": "cli", "prompt": "写 {text}",
                                                  "output_file": "结果.md", "timeout": 300})
        self.rejected(card, "普通工具")

    def test_secrets_are_rejected_before_schema_validation(self):
        # 连格式也错的卡，仍先报告密钥，不把密钥带进错误示例。
        reply = self.rejected({"description": SECRET, "unknown": True}, "密钥只存在钥匙串里")
        self.assertNotIn(SECRET, reply)
        self.rejected(translation(how={"kind": "llm_api", "system": SECRET, "prompt": "{text}"}), "密钥")

    def test_source_and_example_are_owned_by_program(self):
        _, pid = self.draft(card=translation(source="builtin", example_run="20261111-120000-abcd"))
        data = self.load(pid)
        self.assertEqual((data["kind"], data["state"], data["name"], data["block_name"]),
                         ("ability", "open", "翻译成英文", "阿里百炼"))
        self.assertEqual(data["ability"]["source"], "clerk")
        self.assertIsNone(data["ability"]["example_run"])
        self.assertEqual(data["warns"], [])
        self.assertFalse((self.home / abilities.ABILITY_DIR / "translate-en.json").exists())

    def test_latest_check_models_and_incomplete_warning(self):
        blocks.add_record("bailian", {"type": "check", "ok": True, "status": "ok",
                                      "models": ["new-model"]}, self.home)
        self.rejected(translation(), "模型")
        blocks.add_record("bailian", {"type": "check", "ok": True, "status": "ok",
                                      "models": [f"model-{i}" for i in range(blocks_check.MODELS_LIMIT)]}, self.home)
        _, pid = self.draft()
        self.assertTrue(self.load(pid)["warns"], "名单未拿全时允许出草稿，但保留警告")

    def test_no_model_list_does_not_invent_a_rejection(self):
        blocks.add_record("bailian", {"type": "check", "ok": True, "status": "ok"}, self.home)
        _, pid = self.draft()
        self.assertEqual(self.load(pid)["warns"], [])

    def test_only_one_pending_draft_per_conversation(self):
        conv, first = self.draft()
        _, second = self.draft(conv, translation(id="translate-again"))
        self.assertNotEqual(first, second)
        self.assertEqual(self.load(first)["state"], "replaced")
        self.assertEqual(self.load(second)["state"], "open")
        self.assertTrue(any(e.get("type") == "proposal_done" and e["id"] == first for e in conv.events))

    def test_confirm_repeat_and_cancel_keep_ability_out_of_project_tasks(self):
        conv, pid = self.draft()
        first = tasks.decide(str(self.home), conv.id, pid, "confirm")
        again = tasks.decide(str(self.home), conv.id, pid, "confirm")
        for reply, repeat in ((first, False), (again, True)):
            self.assertEqual({k: reply[k] for k in ("ok", "state", "repeat", "kind", "card")},
                             {"ok": True, "state": "confirmed", "repeat": repeat, "kind": "ability", "card": ""})
            self.assertEqual(reply["ability"]["id"], "translate-en")
        self.assertIsInstance(self.load(pid)["decided"], (int, float))
        _, cancelled = self.draft(conv)
        self.assertEqual(tasks.decide(str(self.home), conv.id, cancelled, "cancel")["state"], "cancelled")
        self.assertFalse((self.home / ".collab" / "cards").exists())

    def test_trial_fields_and_saved_flag_are_persisted_in_history_event(self):
        conv, pid = self.draft()
        tasks.decide(str(self.home), conv.id, pid, "confirm")
        result = self.record(pid, True, output="这项不应该写进 trial", files=["结果.txt"])
        self.require_function(tasks, "mark_saved")(str(self.home), pid)
        data = self.load(pid)
        expected = {k: result[k] for k in ("ok", "status", "reason", "run_id", "seconds", "model")}
        self.assertEqual(data["trial"], expected)
        event = tasks.event_from_proposal(data)
        self.assertEqual(event["trial"], expected)
        self.assertTrue(event["saved"])
        for key in ("ability", "name", "block_name", "warns"):
            self.assertEqual(event[key], data[key])

    def test_record_trial_ignores_missing_and_other_kind_proposal(self):
        self.record("missing")
        path = self.home / tasks.FOLDER / "other.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": "other", "kind": "connect", "state": "confirmed"}), encoding="utf-8")
        before = path.read_bytes()
        self.record("other")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in self.proposal_files()], ["other.json"])

    def tried(self, conv, ok):
        _, pid = self.draft(conv)
        tasks.decide(str(self.home), conv.id, pid, "confirm")
        self.record(pid, ok)
        return pid

    def test_three_failures_block_fourth_draft_even_with_different_id(self):
        conv = self.conv()
        for _ in range(3):
            self.tried(conv, False)
        before = len(conv.events)
        reply = self.require_function(tasks, "propose_ability")(conv, {"card": translation(id="another-id")})
        self.assertTrue(reply.startswith("没有出草稿："), reply)
        self.assertIn("连续 3 次", reply)
        self.assertEqual(len(conv.events), before)
        self.assertEqual(len(self.proposal_files()), 3)
        self.draft(self.conv("another-conversation"))

    def test_success_resets_failures_and_unfinished_confirmations_do_not_count(self):
        conv = self.conv()
        for ok in (False, False, True, False, False):
            self.tried(conv, ok)
        for _ in range(2):
            _, pid = self.draft(conv)
            tasks.decide(str(self.home), conv.id, pid, "confirm")
        self.draft(conv)  # 总失败已超过三次，但连续完成的失败只有两次。

    def test_existing_good_or_broken_card_sets_replaces(self):
        abilities.save_ability(translation(title="原来的翻译"), self.home)
        _, pid = self.draft()
        self.assertEqual(self.load(pid)["replaces"], "原来的翻译")
        self.assertEqual(tasks.event_from_proposal(self.load(pid))["replaces"], "原来的翻译")
        (self.home / abilities.ABILITY_DIR / "translate-en.json").write_text("{坏卡", encoding="utf-8")
        _, pid = self.draft()
        self.assertEqual(self.load(pid)["replaces"], "translate-en")

    def test_read_block_uses_saved_check_and_related_abilities(self):
        abilities.save_ability(translation(), self.home)
        before = {str(p): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        text = blocks_tools.run(str(self.home), "read_block", {"block": "bailian"})
        self.assertIn("阿里百炼（bailian）", text.splitlines()[0])
        self.assertIn("大模型 API", text.splitlines()[0])
        self.assertIn("通了", text.splitlines()[0])
        for word in ("qwen3.7-plus", "qwen-flash", "翻译成英文（translate-en）", "只是名字", "原样照抄"):
            self.assertIn(word, text)
        self.assertLessEqual(len(text), 6000)
        filtered = blocks_tools.run(str(self.home), "read_block", {"block": "bailian", "keyword": "flash"})
        self.assertIn("qwen-flash", filtered)
        self.assertNotIn("qwen3.7-plus", filtered)
        self.assertEqual({str(p): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}, before)

    def test_read_block_model_list_is_bounded_and_names_not_cut_in_half(self):
        models = [f"model-{i:03d}-" + "x" * 70 for i in range(150)]
        blocks.add_record("bailian", {"type": "check", "ok": True, "status": "ok", "models": models}, self.home)
        text = blocks_tools.run(str(self.home), "read_block", {"block": "bailian"})
        self.assertLessEqual(len(text), 6000)
        self.assertRegex(text, r"只列了前\s*\d+\s*个")
        shown = int(re.search(r"只列了前\s*(\d+)\s*个", text).group(1))
        self.assertGreater(shown, 0)
        self.assertLess(shown, len(models))
        for model in models[:shown]:
            self.assertIn(model, text, "模型名要完整装入，不能切半截")
        self.assertNotIn(models[shown][:10], text, "装不下的下一项不能留下半截名字")
        self.assertIn("keyword", text)
        self.assertIn(models[0], text)
        self.assertNotIn(models[-1], text)
        self.assertIn("只是名字", text, "截断名单也要保留名字不是指示的提醒")

    def test_read_block_missing_invalid_tool_search_and_missing_models(self):
        for bid in ("missing", "../outside", "bad/id"):
            with self.subTest(block=bid):
                text = blocks_tools.run(str(self.home), "read_block", {"block": bid})
                self.assertIn("基础层里没有积木块", text)
                self.assertIn("list_blocks", text)
        blocks.save_card({"id": "ordinary", "kind": "cli", "name": "普通工具", "command": "ordinary",
                          "role": "tool", "probe": ["--version"]}, self.home)
        text = blocks_tools.run(str(self.home), "read_block", {"block": "ordinary"})
        self.assertIn("这一版提取不了能力卡片", text)
        self.assertIn("还没有模型名单", text)
        blocks.save_card(blocks.search_card("tavily"), self.home)
        text = blocks_tools.run(str(self.home), "read_block", {"block": "web-search"})
        self.assertIn("联网搜索", text)
        self.assertNotIn("模型名单", text)
        self.assertIn("能力层里还没有用它的卡片", text)

    def test_read_cli_models_includes_each_probe_status(self):
        blocks.save_card(dict(blocks.BUILTIN["codex"]), self.home)
        models = ["model-ok", "model-quota", "model-fail", "model-never"]
        blocks.add_record("codex", {"type": "check", "ok": True, "status": "ok", "models": models}, self.home)
        for model, status in zip(models, ("ok", "quota", "fail")):
            blocks.add_record("codex", {"type": "model_check", "model": model, "status": status,
                                        "ok": status == "ok"}, self.home)
        text = blocks_tools.run(str(self.home), "read_block", {"block": "codex"})
        for index, (model, status) in enumerate(zip(models, ("能用", "额度不足", "不能用", "没试过"))):
            self.assertIn(model, text)
            own_entry = text.split(model, 1)[1]
            if index + 1 < len(models):
                own_entry = own_entry.split(models[index + 1], 1)[0]
            self.assertIn(status, own_entry)

    def test_list_abilities_empty_good_and_broken(self):
        self.assertIn("能力层里还没有能力卡片", blocks_tools.run(str(self.home), "list_abilities", {}))
        abilities.save_ability(translation(), self.home)
        search = translation(id="search-demo", title="查资料", block="web-search",
                             how={"kind": "search", "query": "{text}", "max_results": 8, "time_range": "none"},
                             output={"type": "list", "check": {"min_items": 1}})
        search.pop("model")
        abilities.save_ability(search, self.home)
        (self.home / abilities.ABILITY_DIR / "bad.json").write_text("{坏卡", encoding="utf-8")
        text = blocks_tools.run(str(self.home), "list_abilities", {})
        self.assertIn("翻译成英文（translate-en）｜bailian｜qwen3.7-plus｜", text)
        self.assertIn(TRANSLATE["description"], text)
        self.assertIn("查资料（search-demo）｜web-search｜不涉及｜", text)
        self.assertIn("bad｜坏了：", text)

    def test_activity_descriptions_distinguish_draft_success_and_failure(self):
        self.assertEqual(blocks_tools.describe("read_block", {"block": "bailian"}, "阿里百炼（bailian）\n模型"),
                         "看积木块：bailian → 阿里百炼（bailian）")
        self.assertEqual(blocks_tools.describe("propose_ability", {"card": translation()}, "已向用户出示确认框，等确认"),
                         "出草稿：能力卡片（草稿）「翻译成英文」")
        self.assertEqual(blocks_tools.describe("propose_ability", {}, "没有出草稿：格式不对\n更多解释"),
                         "没出草稿：没有出草稿：格式不对")


    # CARD-003 追加要求：编号必须真实存在，不能把服务商名换成另一个编号。
    def test_read_block_does_not_resolve_provider_name(self):
        blocks.save_card(blocks.search_card("tavily"), self.home)
        self.assertEqual(blocks.load_card("web-search", self.home)["id"], "web-search")
        text = blocks_tools.run(str(self.home), "read_block", {"block": "tavily"})
        self.assertEqual(text, "基础层里没有积木块 tavily：先用 list_blocks 看编号。")

    def test_provider_name_draft_is_rejected_without_rewriting_or_replacing_pending(self):
        blocks.save_card(blocks.search_card("tavily"), self.home)
        _, pending = self.draft()
        card = search_draft("tavily")
        before = copy.deepcopy(card)
        self.rejected(card, "基础层里没有积木块 tavily：先用 list_blocks 看编号。")
        self.assertEqual(card, before, "拒绝草稿也不能改写传入的编号")
        self.assertEqual(self.load(pending)["state"], "open")

    def test_real_block_id_equal_to_provider_name_is_kept_verbatim(self):
        # 不能把修复做成禁止 tavily 这个词；只要基础层确实有这个编号就应使用它。
        blocks.save_card(blocks.search_card("tavily"), self.home)
        blocks.save_card(dict(blocks.search_card("tavily"), id="tavily", name="独立搜索块"), self.home)
        card = search_draft("tavily")
        before = copy.deepcopy(card)
        conv, pid = self.draft(card=card)
        data = self.load(pid)
        self.assertEqual(data["ability"]["block"], "tavily")
        self.assertEqual(data["block_name"], "独立搜索块")
        event = next(e for e in conv.events if e.get("type") == "proposal")
        self.assertEqual(event["ability"]["block"], "tavily")
        self.assertEqual(card, before)

    def test_ability_replacement_reply_names_ability_layer_and_original_title(self):
        abilities.save_ability(translation(title="原来的翻译"), self.home)
        for broken, title in ((False, "原来的翻译"), (True, "translate-en")):
            with self.subTest(broken=broken):
                if broken:
                    (self.home / abilities.ABILITY_DIR / "translate-en.json").write_text("{坏卡", encoding="utf-8")
                before = self.layer_snapshot()
                reply = tasks.propose_ability(self.conv(), {"card": translation(title="新的翻译")})
                self.assertTrue(reply.startswith("已向用户出示确认框"), reply)
                self.assertIn(f"放进能力层后会替换能力层里原来的能力卡片「{title}」", reply)
                self.assertNotIn("旧的那个积木块就没了", reply)
                self.assertNotIn("替换基础层", reply)
                self.assertEqual(self.layer_snapshot(), before, "出替换草稿不能提前覆盖能力层")


def search_draft(block):
    card = translation(id="search-extra", title="查资料", block=block,
                       how={"kind": "search", "query": "{text}", "max_results": 8, "time_range": "none"},
                       output={"type": "list", "check": {"min_items": 1}})
    card.pop("model")
    return card


class DraftHttp(ProposalHelpers, page68.AbilityCase):
    """防止发送、确认、试做、保存、重开之间丢状态；所有运行依赖使用 68 的假工具。"""

    def setUp(self):
        super().setUp()
        self.models_known()
        self.llm.reply = ENGLISH
        self.client = ScriptedClient()
        self.server.blocks_clerk = self.client
        # 一旦漏传某个可替换依赖就失败，绝不退到真的网络/命令行运行器。
        for owner, name in ((abilities_run, "ModelClient"), (abilities_run.blocks_web, "search"),
                            (abilities_run, "watched_runner")):
            patcher = mock.patch.object(owner, name, side_effect=AssertionError("不能调用真实模型、搜索或 CLI"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def talk(self, steps=None, cid="", **extra):
        self.client.steps = list(steps or [{"text": "好的"}])
        status, data = self.bpost("/api/clerk/send", dict(project=str(self.home), conversation=cid,
                                                        text="请提取翻译能力", **extra))
        self.assertEqual(status, 200, data)
        cid = data["conversation"]
        return cid, wait_idle(self.server.desk, str(self.home), cid)

    def scripted_draft(self, card=None, cid="", **extra):
        card = card if card is not None else translation()
        cid, events = self.talk([
            {"tool_calls": [tool_call("read_block", {"block": card["block"]})]},
            {"tool_calls": [tool_call("list_abilities", {})]},
            {"tool_calls": [tool_call("propose_ability", {"card": card})]},
        ], cid=cid, **extra)
        proposals = [e for e in events if e.get("type") == "proposal" and e.get("kind") == "ability"]
        self.assertTrue(proposals, f"管家工具调用后应出草稿事件：{events}")
        return cid, proposals[-1]["id"]

    def trial(self, cid, pid, inputs=None):
        status, reply = self.confirm(cid, pid, inputs=INPUTS if inputs is None else inputs)
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["state"], "confirmed")
        self.assertTrue(reply["job"])
        job = self.job(reply["job"])
        self.assertEqual(job["kind"], "trial")
        self.assertFalse(job["error"], job)
        return reply, job["result"]

    def save(self, pid):
        return self.bpost("/api/abilities/save", {"proposal": pid})

    def test_full_script_from_block_to_trial_then_save_and_list(self):
        before = self.layer_snapshot()
        cid, pid = self.scripted_draft(block="bailian")
        self.assertEqual(self.layer_snapshot(), before, "出草稿不写能力层")
        event = next(e for e in self.server.desk.events(str(self.home), cid)["events"] if e.get("id") == pid)
        self.assertEqual((event["name"], event["block_name"]), ("翻译成英文", "阿里百炼"))
        reply, result = self.trial(cid, pid)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["status"], result["output"]), ("ok", ENGLISH))
        self.assertEqual(self.load(pid)["trial"]["run_id"], result["run_id"])
        self.assertTrue(self.load(pid)["trial"]["ok"])
        self.assertEqual(self.layer_snapshot(), before, "试做不写能力层")
        self.assertEqual(self.bget("/api/blocks/job", proposal=pid)[1]["result"]["run_id"], result["run_id"])
        self.assertEqual(self.server.blocks_proposal_jobs[pid], reply["job"])
        status, saved = self.save(pid)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["id"], "translate-en")
        card = json.loads((self.home / abilities.ABILITY_DIR / "translate-en.json").read_text(encoding="utf-8"))
        self.assertEqual((card["source"], card["example_run"]), ("clerk", result["run_id"]))
        self.assertTrue(self.load(pid)["saved"])
        self.assertIn("translate-en", [a["id"] for a in self.bget("/api/abilities/list")[1]["abilities"]])
        detail = self.bget("/api/abilities/detail", id="translate-en")[1]
        self.assertEqual(detail["example"]["run_id"], result["run_id"])
        self.assertEqual(detail["example"]["inputs"], INPUTS)

    def test_bad_inputs_keep_proposal_open_and_start_no_job(self):
        cid, pid = self.scripted_draft()
        before = dict(self.server.blocks_jobs)
        for inputs in ({}, [], "text", {"text": "x" * 4001}):
            with self.subTest(inputs_type=type(inputs).__name__):
                status, reply = self.confirm(cid, pid, inputs=inputs)
                self.assertEqual(status, 400, reply)
                self.assertEqual(self.load(pid)["state"], "open")
                self.assertEqual(self.server.blocks_jobs, before)
                self.assertNotIn(pid, self.server.blocks_proposal_jobs)
        self.assertTrue(self.trial(cid, pid)[1]["ok"], "改对例子后可以继续试做")

    def test_failed_format_and_missing_model_are_recorded_and_cannot_save(self):
        for mode, expected_status in (("short", "format"), ("missing", "model")):
            with self.subTest(mode=mode):
                card = translation(id="trial-" + mode)
                card["output"]["check"]["min_chars"] = 20
                self.llm.reply = "x" if mode == "short" else ENGLISH
                self.llm.error = ModelError("这家服务商没有这个模型，或服务地址不对。") if mode == "missing" else None
                if self.llm.error:
                    self.llm.error.code = 404
                cid, pid = self.scripted_draft(card)
                _, result = self.trial(cid, pid)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(self.load(pid)["trial"]["reason"], result["reason"])
                self.assertEqual(self.save(pid)[0], 409)
                self.assertFalse((self.home / abilities.ABILITY_DIR / (card["id"] + ".json")).exists())
                self.talk(cid=cid)
                prompt = self.client.calls[-1][0][0]["content"]
                self.assertIn(result["reason"], prompt)
                self.assertIn("试做没成", prompt)

    def test_next_prompt_reports_only_latest_unsaved_trial(self):
        cid, pid = self.scripted_draft()
        self.trial(cid, pid)
        self.talk(cid=cid)
        prompt = self.client.calls[-1][0][0]["content"]
        self.assertIn("试做成了", prompt)
        self.assertIn("放进能力层", prompt)
        self.assertIn("没点之前不能说已经放进去了", prompt)
        self.assertEqual(self.save(pid)[0], 200)
        self.talk(cid=cid)
        self.assertNotIn("上一张能力卡片（草稿）", self.client.calls[-1][0][0]["content"])

    def test_newer_untried_draft_does_not_repeat_older_failure(self):
        cid, pid = self.scripted_draft()
        tasks.decide(str(self.home), cid, pid, "confirm")
        self.record(pid, reason="旧草稿独有的失败原因")
        self.scripted_draft(translation(id="new-draft"), cid=cid)
        self.talk(cid=cid)
        self.assertNotIn("旧草稿独有的失败原因", self.client.calls[-1][0][0]["content"])

    def test_new_tools_are_exposed_with_manual_and_record_readable_activity(self):
        cid, _ = self.scripted_draft()
        tools = {t["function"]["name"]: t["function"] for t in self.client.calls[0][1]}
        for name in ("read_block", "list_abilities", "propose_ability"):
            self.assertIn(name, tools)
        self.assertIn("block", tools["read_block"]["parameters"]["required"])
        self.assertIn("keyword", tools["read_block"]["parameters"]["properties"])
        self.assertIn("card", tools["propose_ability"]["parameters"]["required"])
        self.assertIn("ability", tools["read_manual"]["parameters"]["properties"]["name"]["enum"])
        # 没有从 block 开对话：第一次调用新工具也要自动翻开做能力手册。
        manual = blocks_tools.manual_text("ability").strip()
        self.assertTrue(manual)
        tool_results = [m["content"] for m in self.client.calls[1][0] if m.get("role") == "tool"]
        self.assertTrue(any(manual in result for result in tool_results), "手册首次附在工具结果里")
        self.talk(cid=cid)
        self.assertIn(manual, self.client.calls[-1][0][0]["content"], "下一轮的系统提示携带翻开的手册")
        fresh = ClerkDesk(lambda: self.server.clerk, blocks_client=lambda: self.server.blocks_clerk)
        activities = [e for e in fresh.events(str(self.home), cid)["events"] if e.get("type") == "activity"]
        labels = {e["label"] for e in activities}
        self.assertTrue({"看积木块", "看能力层", "出草稿"} <= labels, activities)

    def test_failure_prompt_masks_and_limits_reason(self):
        cid, pid = self.scripted_draft()
        tasks.decide(str(self.home), cid, pid, "confirm")
        reason = "错误标记：" + SECRET + "长" * 600 + "尾巴不该进入提示"
        self.record(pid, reason=reason)
        self.talk(cid=cid)
        prompt = self.client.calls[-1][0][0]["content"]
        self.assertIn("错误标记：", prompt)
        self.assertNotIn(SECRET, prompt)
        self.assertNotIn("尾巴不该进入提示", prompt)
        self.assertNotIn("长" * 301, prompt)

    def test_from_block_opens_manual_and_keeps_context_after_reopen(self):
        cid, _ = self.talk(block="bailian")
        self.assertEqual(self.server.desk._index_entry(str(self.home), cid).get("block"), "bailian")
        prompt = self.client.calls[0][0][0]["content"]
        self.assertIn("说的就是这个积木块", prompt)
        self.assertIn("阿里百炼", prompt)
        manual = blocks_tools.manual_text("ability").strip()
        self.assertTrue(manual, "做能力手册必须存在")
        self.assertIn(manual, prompt)
        path = self.home / ".collab" / "行政对话" / (cid + ".jsonl")
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(line.get("role") == "manual" and line.get("name") == "ability" for line in lines))
        self.server.desk = ClerkDesk(lambda: self.server.clerk, blocks_client=lambda: self.server.blocks_clerk)
        self.talk(cid=cid)
        self.assertIn(manual, self.client.calls[-1][0][0]["content"])

    def test_missing_block_is_400_existing_conversation_ignores_block(self):
        for bid in ("missing", "../outside"):
            status, reply = self.bpost("/api/clerk/send", {"project": str(self.home), "text": "提取", "block": bid})
            self.assertEqual(status, 400, reply)
            self.assertIn("基础层里没有这个积木块", reply["error"])
        cid, _ = self.talk()
        self.talk(cid=cid, block="missing")
        self.assertFalse(self.server.desk._index_entry(str(self.home), cid).get("block"))

    def test_repeated_confirm_and_save_are_idempotent(self):
        cid, pid = self.scripted_draft()
        first, result = self.trial(cid, pid)
        status, again = self.confirm(cid, pid)  # 重复确认不重新校验空例子。
        self.assertEqual(status, 200, again)
        self.assertEqual(again["job"], first["job"])
        self.assertEqual(len(abilities.runs("translate-en", self.home)), 1)
        self.assertEqual(len(self.llm.seen), 1)
        with mock.patch.object(abilities, "save_ability", wraps=abilities.save_ability) as save:
            self.assertEqual(self.save(pid)[0], 200)
            status, saved = self.save(pid)
            self.assertEqual(status, 200, saved)
            self.assertEqual(saved, {"ok": True, "id": "translate-en", "repeat": True})
            self.assertEqual(save.call_count, 1, "重复保存不能再次覆盖卡片")
        self.assertEqual(abilities.load_ability("translate-en", self.home)["example_run"], result["run_id"])

    def test_cancel_does_not_start_trial_or_save_card(self):
        cid, pid = self.scripted_draft()
        before = self.layer_snapshot()
        status, reply = self.bpost("/api/clerk/proposal", {
            "project": str(self.home), "conversation": cid, "id": pid, "decision": "cancel"})
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["state"], "cancelled")
        self.assertEqual(self.load(pid)["state"], "cancelled")
        self.assertNotIn(pid, self.server.blocks_proposal_jobs)
        self.assertEqual(self.llm.seen, [])
        self.assertEqual(self.layer_snapshot(), before)
        self.assertEqual(self.save(pid)[0], 409)

    def test_repeated_confirm_while_job_in_flight_returns_same_job(self):
        entered, release = threading.Event(), threading.Event()
        original = self.server.blocks_client_factory

        def slow_factory(*args):
            client = original(*args)
            chat = client.chat

            def slow_chat(*a, **kw):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("测试没有释放假模型")
                return chat(*a, **kw)
            client.chat = slow_chat
            return client

        self.server.blocks_client_factory = slow_factory
        cid, pid = self.scripted_draft()
        try:
            status, first = self.confirm(cid, pid, inputs=INPUTS)
            self.assertEqual(status, 200, first)
            self.assertTrue(entered.wait(5))
            status, again = self.confirm(cid, pid, inputs=INPUTS)
            self.assertEqual(status, 200, again)
            self.assertEqual(again["job"], first["job"])
            self.assertEqual(self.save(pid)[0], 409)
        finally:
            release.set()
        self.assertTrue(self.job(first["job"])["result"]["ok"])
        self.assertEqual(len(self.llm.seen), 1)

    def test_replaces_old_card_and_rejects_new_conflict(self):
        abilities.save_ability(translation(title="原来的翻译"), self.home)
        cid, pid = self.scripted_draft(translation(title="新的翻译"))
        self.assertEqual(self.load(pid)["replaces"], "原来的翻译")
        self.trial(cid, pid)
        self.assertEqual(self.save(pid)[0], 200)
        self.assertEqual(abilities.load_ability("translate-en", self.home)["title"], "新的翻译")
        cid, pid = self.scripted_draft(translation(id="race-card"))
        self.assertFalse(self.load(pid).get("replaces"))
        self.trial(cid, pid)
        abilities.save_ability(translation(id="race-card", title="别处新存的卡"), self.home)
        status, reply = self.save(pid)
        self.assertEqual(status, 409, reply)
        self.assertIn("重新起草", reply["error"])
        self.assertEqual(abilities.load_ability("race-card", self.home)["title"], "别处新存的卡")
        self.assertFalse(self.load(pid).get("saved"))

    def test_save_rejects_invalid_missing_wrong_kind_unconfirmed_and_no_trial(self):
        for pid in ("../outside", "missing"):
            status, reply = self.save(pid)
            self.assertEqual(status, 404, reply)
            self.assertIn("没有这张能力卡片（草稿）", reply["error"])
        cid, pid = self.scripted_draft()
        self.assertEqual(self.save(pid)[0], 409)
        tasks.decide(str(self.home), cid, pid, "confirm")
        self.assertEqual(self.save(pid)[0], 409)
        path = self.home / tasks.FOLDER / "wrong-kind.json"
        path.write_text(json.dumps({"id": "wrong-kind", "kind": "connect", "state": "confirmed"}), encoding="utf-8")
        self.assertEqual(self.save("wrong-kind")[0], 404)

    def test_running_guard_keeps_draft_open_and_defers_save(self):
        cid, pid = self.scripted_draft()
        with abilities_run._OneAtATime("translate-en"):
            status, reply = self.confirm(cid, pid, inputs=INPUTS)
            self.assertEqual(status, 409, reply)
            self.assertIn("正在用", reply["error"])
            self.assertEqual(self.load(pid)["state"], "open")
            self.assertNotIn(pid, self.server.blocks_proposal_jobs)
        self.trial(cid, pid)
        with abilities_run._OneAtATime("translate-en"):
            status, reply = self.save(pid)
            self.assertEqual(status, 409, reply)
            self.assertIn("正在用", reply["error"])
            self.assertFalse(self.load(pid).get("saved"))
        self.assertEqual(self.save(pid)[0], 200)

    def test_run_exception_records_failure_instead_of_leaving_confirmed_without_result(self):
        cid, pid = self.scripted_draft()
        with mock.patch.object(abilities_run, "run", side_effect=RuntimeError("内部失败 " + SECRET)):
            status, reply = self.confirm(cid, pid, inputs=INPUTS)
            self.assertEqual(status, 200, reply)
            self.job(reply["job"])
        data = self.load(pid)
        self.assertEqual(data["state"], "confirmed")
        self.assertFalse(data["trial"]["ok"])
        self.assertEqual(data["trial"]["status"], "error")
        self.assertTrue(data["trial"]["reason"])
        self.assertNotIn(SECRET, json.dumps(data, ensure_ascii=False))
        self.assertEqual(self.save(pid)[0], 409)

    def test_reopen_restores_draft_trial_and_saved_and_save_survives_lost_jobs(self):
        cid, pid = self.scripted_draft()

        def reopen_event():
            fresh = ClerkDesk(lambda: self.server.clerk, blocks_client=lambda: self.server.blocks_clerk)
            events = fresh.events(str(self.home), cid)["events"]
            return next(e for e in events if e.get("type") == "proposal" and e.get("id") == pid)

        self.assertEqual(reopen_event()["ability"]["id"], "translate-en")
        _, result = self.trial(cid, pid)
        self.assertEqual(reopen_event()["trial"]["run_id"], result["run_id"])
        # 清掉重启会丢的内存任务，用新的 desk，只保留磁盘提议和试做记录。
        self.server.blocks_jobs.clear()
        self.server.blocks_proposal_jobs.clear()
        self.server.desk = ClerkDesk(lambda: self.server.clerk, blocks_client=lambda: self.server.blocks_clerk)
        self.assertEqual(self.save(pid)[0], 200)
        event = reopen_event()
        self.assertEqual(event["state"], "confirmed")
        self.assertTrue(event["saved"])
        self.assertTrue(event["trial"]["ok"])
        self.assertEqual(event["name"], "翻译成英文")
        self.assertEqual(event["block_name"], "阿里百炼")

    def test_trial_card_validation_id_and_kind_guards(self):
        self.assertIn("card", inspect.signature(abilities_run.run).parameters, "run 必须接受草稿 card")
        before = self.layer_snapshot()
        cases = [("translate-en", translation(), "run"),
                 ("wrong-id", translation(), "trial"),
                 ("translate-en", translation(unknown=True), "trial")]
        for aid, card, kind in cases:
            with self.subTest(id=aid, kind=kind):
                with self.assertRaises(abilities.AbilityError):
                    abilities_run.run(aid, INPUTS, self.home, card=card, kind=kind,
                                      keys=dict(self.server.blocks_saved_keys), client_factory=self.llm.factory)
        self.assertEqual(self.layer_snapshot(), before)
        self.assertEqual(self.llm.seen, [])

    def test_direct_trial_saves_result_and_record_without_touching_existing_layer(self):
        self.assertIn("card", inspect.signature(abilities_run.run).parameters)
        abilities.save_ability(translation(title="已存在的卡"), self.home)
        before = self.layer_snapshot()
        result = abilities_run.run("translate-en", INPUTS, self.home, card=translation(), kind="trial",
                                   keys=dict(self.server.blocks_saved_keys), client_factory=self.llm.factory)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"], ENGLISH)
        self.assertEqual(self.layer_snapshot(), before)
        rec = abilities.runs("translate-en", self.home)[-1]
        self.assertEqual((rec["type"], rec["run_id"], rec["inputs"]), ("trial", result["run_id"], INPUTS))
        output = self.home / abilities.RESULT_DIR / "translate-en" / result["run_id"] / "结果.txt"
        self.assertEqual(output.read_text(encoding="utf-8"), ENGLISH)

    def test_search_and_cli_drafts_use_same_injected_runners_as_normal_run(self):
        search = translation(id="search-draft", block="web-search",
                             how={"kind": "search", "query": "{text}", "max_results": 8, "time_range": "none"},
                             output={"type": "list", "check": {"min_items": 1}})
        search.pop("model")
        cli = translation(id="image-draft", block="codex", how={"kind": "cli", "prompt": "画 {text}",
                          "output_file": "输出.png", "timeout": 300}, output={"type": "image", "check": {"min_side": 256}})
        cli.pop("model")
        for card in (search, cli):
            with self.subTest(kind=card["how"]["kind"]):
                before = self.layer_snapshot()
                cid, pid = self.scripted_draft(card)
                _, result = self.trial(cid, pid)
                self.assertTrue(result["ok"], result)
                self.assertEqual(self.layer_snapshot(), before)
        self.assertTrue(self.searches, "搜索草稿必须使用注入的假搜索")
        self.assertEqual(self.searches[0][0], INPUTS["text"])
        self.assertEqual(result["files"], ["输出.png"])

    def test_project_clerk_tools_and_prompt_do_not_receive_ability_workflow(self):
        self.server.clerk = ScriptedClient()
        status, reply = self.bpost("/api/clerk/send", {"project": self.project, "text": "你好", "block": "bailian"})
        self.assertEqual(status, 200, reply)
        wait_idle(self.server.desk, self.project, reply["conversation"])
        messages, tools = self.server.clerk.calls[0]
        names = {t["function"]["name"] for t in tools}
        self.assertIn("propose_task", names)
        self.assertFalse(names & {"read_block", "list_abilities", "propose_ability", "read_manual"})
        self.assertNotIn("说的就是这个积木块", messages[0]["content"])
        self.assertNotIn("《做能力》", messages[0]["content"])


    def test_trial_rejects_missing_provider_id_without_rewriting_or_searching(self):
        self.assertEqual(blocks.load_card("web-search", self.home)["id"], "web-search")
        before_layer = self.layer_snapshot()
        for block_id in ("tavily", "missing"):
            with self.subTest(block=block_id):
                card = search_draft(block_id)
                before = copy.deepcopy(card)
                result = abilities_run.run(card["id"], INPUTS, self.home, card=card, kind="trial",
                                           keys=dict(self.server.blocks_saved_keys),
                                           search_fn=self.server.abilities_search_fn)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["status"], "block")
                self.assertEqual(result["reason"], f"基础层里没有积木块 {block_id}：先用 list_blocks 看编号。")
                self.assertEqual(card, before, "试做不能改写草稿里的编号")
                self.assertEqual(self.searches, [], "找不到真实编号就不能调用搜索")
                self.assertEqual(self.layer_snapshot(), before_layer)


if __name__ == "__main__":
    unittest.main()
