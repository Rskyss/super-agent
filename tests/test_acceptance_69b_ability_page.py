"""施工单 69b：能力草稿页面的七类验收契约。

只新增测试。Node 执行真实 HTML/JS，替代 DOM/HTTP/时钟边界；完整流程保留
真实本机服务、提议存档、试做和能力层，只注入假管家/模型及临时资料夹。
入口匹配本卡命令的 test_acceptance_69b_*.py，不调用真实模型、搜索或助手。
"""
import copy
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from acceptance_helpers import ScriptedClient, tool_call
import test_acceptance_68_abilities_page as page68
import test_acceptance_69_ability_draft as draft69
from collab import abilities, abilities_run

HERE = Path(__file__).resolve().parent
NODE = shutil.which("node")  # ServerCase 会隔离 PATH，须先保留 node 的绝对路径。


def run_node(case, scenario, *args):
    case.assertIsNotNone(NODE, "69b 页面验收需要本机 Node，不能静默跳过页面行为")
    proc = subprocess.run(
        [NODE, str(HERE / "ability_69b_check.js"), scenario, *args],
        capture_output=True, text=True, timeout=45, cwd=HERE.parent,
    )
    case.assertEqual(proc.returncode, 0, f"69b / {scenario}\n{proc.stderr or proc.stdout}")
    case.assertIn("OK " + scenario, proc.stdout)


class AbilityPageAcceptance(unittest.TestCase):
    """断言可见内容、点击产生的请求及恢复行为，不匹配产品脚本里的函数实现。"""

    def test_shared_core_and_old_require_and_script_loading(self):
        run_node(self, "core")

    def test_llm_block_extract_button_passes_id_and_name(self):
        run_node(self, "block-llm_api")

    def test_search_block_extract_button_passes_id_and_name(self):
        run_node(self, "block-search")

    def test_agent_block_extract_button_passes_id_and_name(self):
        run_node(self, "block-agent")

    def test_unchecked_and_failed_block_still_offer_extraction(self):
        run_node(self, "block-failed")

    def test_ordinary_tool_has_no_extract_button(self):
        run_node(self, "block-tool")

    def test_working_chat_disables_extraction_with_reason(self):
        run_node(self, "block-busy")

    def test_start_from_block_sends_new_conversation_and_first_message(self):
        run_node(self, "start-ok")

    def test_missing_block_400_shows_error_without_empty_conversation(self):
        run_node(self, "start-400")

    def test_extraction_only_available_in_blocks_butler_mode(self):
        run_node(self, "mode-guard")

    def test_llm_draft_metadata_prompts_and_prefilled_schema_form(self):
        run_node(self, "draft-llm")

    def test_cli_draft_shows_expanded_instruction(self):
        run_node(self, "draft-cli")

    def test_search_draft_shows_query_and_no_model(self):
        run_node(self, "draft-search")

    def test_required_input_error_stays_in_draft_without_request(self):
        run_node(self, "input-required")

    def test_number_range_error_stays_in_draft_without_request(self):
        run_node(self, "input-number")

    def test_trial_400_keeps_form_editable_and_allows_correction(self):
        run_node(self, "trial-400")

    def test_trial_409_shows_reason_inside_draft(self):
        run_node(self, "trial-409")

    def test_failed_trial_shows_reason_and_guidance_without_save(self):
        run_node(self, "trial-failure")

    def test_trial_payload_timer_poll_cadence_deduplication_and_completion(self):
        run_node(self, "poll")

    def test_trial_text_is_displayed(self):
        run_node(self, "output-text")

    def test_trial_list_is_displayed_with_items_and_links(self):
        run_node(self, "output-list")

    def test_trial_image_url_and_reveal_request(self):
        run_node(self, "output-image")

    def test_trial_file_link_and_reveal_request(self):
        run_node(self, "output-file")

    def test_successful_trial_saves_to_ability_layer(self):
        run_node(self, "save-ok")

    def test_repeat_save_also_displays_saved(self):
        run_node(self, "save-repeat")

    def test_save_conflict_is_visible_without_false_saved_marker(self):
        run_node(self, "save-409")

    def test_cancel_only_sends_cancel_without_trial_or_save(self):
        run_node(self, "cancel")

    def test_reopen_success_restores_result_and_save_button(self):
        run_node(self, "recover-success")

    def test_reopen_saved_restores_marker_without_save_button(self):
        run_node(self, "recover-saved")

    def test_reopen_failed_trial_shows_reason_and_guidance_without_save(self):
        run_node(self, "recover-failure")

    def test_reopen_running_trial_resumes_polling(self):
        run_node(self, "recover-running")

    def test_reopen_missing_job_explains_lost_result(self):
        run_node(self, "recover-lost")

    def test_reopen_cancelled_draft_has_no_trial_action(self):
        run_node(self, "recover-cancelled")

    def test_reopen_voided_draft_has_no_trial_action(self):
        run_node(self, "recover-voided")

    # 追加要求：保留原验收，只增加草稿重开后的查询、重试与文件恢复契约。
    def test_reopen_running_offline_waits_three_seconds_even_after_redraw(self):
        run_node(self, "retry-running-offline")

    def test_reopen_running_http_error_is_temporary_and_retries(self):
        run_node(self, "retry-running-503")

    def test_resumed_poll_offline_shows_retry_and_keeps_polling(self):
        run_node(self, "retry-poll-offline")

    def test_resumed_poll_http_error_waits_three_seconds(self):
        run_node(self, "retry-poll-503")

    def test_reopen_success_file_offline_recovers_result_when_network_returns(self):
        run_node(self, "retry-file-offline")

    def test_reopen_success_file_http_error_is_temporary_and_recovers(self):
        run_node(self, "retry-file-503")

    def test_reopen_success_file_404_shows_result_not_retained(self):
        run_node(self, "retry-file-404")

    def test_reopen_running_has_only_one_inflight_query_across_redraws(self):
        run_node(self, "retry-running-pending")

    def test_reopen_success_has_only_one_inflight_file_query_across_redraws(self):
        run_node(self, "retry-file-pending")

    def test_existing_project_and_block_proposal_rules_still_pass(self):
        self.assertIsNotNone(NODE)
        for filename in ("chat_page_check.js", "chat_card_check.js", "chat_blocks_check.js", "abilities_page_check.js"):
            with self.subTest(filename=filename):
                proc = subprocess.run([NODE, str(HERE / filename)], capture_output=True, text=True, timeout=45)
                self.assertEqual(proc.returncode, 0, f"{filename}\n{proc.stderr or proc.stdout}")
                self.assertIn("OK", proc.stdout)


class AbilityPageLocalFlow(page68.AbilityCase):
    """从页面发起到真实本机接口存入能力层；外部执行入口全部替换或拒绝。"""

    def setUp(self):
        super().setUp()
        self.models_known()
        self.llm.reply = "The weather is lovely today."
        self.server.blocks_clerk = ScriptedClient([
            {"tool_calls": [tool_call("read_block", {"block": "bailian"})]},
            {"tool_calls": [tool_call("list_abilities", {})]},
            {"tool_calls": [tool_call("propose_ability", {"card": copy.deepcopy(draft69.TRANSLATE)})]},
            {"text": "请看能力卡片草稿，填好例子再试做。"},
        ])
        for owner, name in ((abilities_run, "ModelClient"), (abilities_run.blocks_web, "search"),
                            (abilities_run, "watched_runner")):
            patcher = mock.patch.object(owner, name, side_effect=AssertionError("69b 禁止调用真实模型、搜索或助手"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_page_to_fake_butler_trial_save_list_and_reopen(self):
        run_node(self, "http-flow", self.base, str(self.home))
        saved = abilities.load_ability("translate-en", self.home)
        self.assertEqual(saved["title"], "翻译成英文")
        self.assertTrue(saved["example_run"], "页面保存应绑定真实试做记录")
        runs = abilities.runs("translate-en", self.home)
        self.assertEqual(len(runs), 1, "完整流程只实际试做一次")
        self.assertTrue(runs[0]["ok"])
        self.assertEqual(len(self.llm.seen), 1, "能力执行经过注入的假模型")


if __name__ == "__main__":
    unittest.main()
