修完了，只改了当前目录 3 个文件，没有 commit、没有推送。

**10 条改动与用例**
- **第 1 条 验收测试识别**：`_acceptance_tests` 现在认 `unittest discover -s/-p`、`tests.test_accept.T.test_sum`、`pytest tests/test_a.py::test_x`、副本内绝对路径；开工时写 `开工记录.json`，锁死判断优先按它。新增 `锁死测试基线.json`，续跑按基线还原，改回 `HEAD` 也能发现。报错位置 `_test_file_errors` 对相对路径要求完整相等，跑卡时传 `worktree` 归一绝对路径；无 root 的老调用保留原认法。用例：`test_acceptance_command_forms`、`test_prejob_acceptance_edit_restored_when_tester_reverts_to_head`、`test_first_write_corruption_resume_uses_prejob_baseline`、`test_error_location_uses_full_path_not_tail`、`test_changed_old_test_reviewed_as_wrong_stops_as_locked`。
- **第 2 条 复查退回续跑删光**：删光检查放到测试员收尾的共用出口，`_dispatch_review_return` 之后续跑也走同一检查；非复查退回的改测试出口一并补上。用例：`test_review_reject_resume_tester_deletes_all_tests_stops`。
- **第 3 条 裁决漏交/重复/跳过**：每次裁决交代写明“本次只裁决这个文件”；判锁死先 `_mark_locked` 再登记，判代码记“待开发处理”；续跑按开发回复序号和文件复用已回复的裁决；判代码错必须派开发并计返工。用例：`test_two_arbitration_prompts_name_their_own_file`、`test_pass_but_code_verdict_resume_dispatches_developer_and_counts_rework`、`test_arbiter_reply_is_reused_not_sent_twice`、`test_locked_mark_interrupted_resume_still_stops`、`test_two_disputed_files_first_done_second_still_arbitrated_on_resume`。
- **第 4 条 分两次修文件卡死**：同一条复查意见保留第一次停卡时的 digest，续跑只更新“哪些文件已处理”。用例：`test_two_locked_files_fixed_one_at_a_time_both_release`、`test_single_locked_file_fixed_on_resume_releases`。
- **第 5 条 续跑撤测试/旧备份覆盖同步**：中断时写 `interrupted.json`，还原前核对现场摘要；已记 `测试收尾` 的只清备份、不撤测试。用例：`test_successful_tester_finish_survives_resume`、`test_resume_does_not_withdraw_tester_half_done_product`。
- **第 6 条 长围栏**：`_plain_lines` 记住开围栏字符和长度，闭围栏必须同字符、不短、后面无内容。用例：`test_long_tilde_fence_keeps_template_inside`。
- **第 7 条 同一用例换说法重裁**：`_dispute_place` 用规范路径加用例名/行号认同一处。用例：`test_same_case_with_different_wording_is_second_time`。
- **第 8 条 纯测试错占返工**：交回测试改完仍红时先派开发且不扣返工；复查判测试错交回后仍红也不扣。用例：`test_test_only_error_does_not_use_developer_rework`。
- **第 9 条 临时拷贝绝对路径**：任务卡和争议材料复制进临时目录，交代路径统一指向拷贝。用例：`test_arbitration_prompt_points_into_temp_copy`。
- **第 10 条 七种错误实现**：分别由 `test_arbitration_prompt_points_into_temp_copy`、`test_review_reject_resume_tester_deletes_all_tests_stops`、`test_resume_does_not_withdraw_tester_half_done_product`、`test_two_locked_files_fixed_one_at_a_time_both_release`、`test_single_locked_file_fixed_on_resume_releases`、`test_three_review_test_returns_share_limit_and_are_counted`、`test_pass_but_code_verdict_resume_dispatches_developer_and_counts_rework` 打红。

**测试结果**
- 卡 D 验收：`Ran 61 tests in 51.913s` / `OK`
- `test_external_trial_fixes`：`Ran 67 tests in 20.894s` / `OK`
- `test_flowfix_c15_extra`：`Ran 8 tests in 1.610s` / `OK`
- `test_flowfix_c15_acceptance`：`Ran 22 tests in 2.417s` / `OK`
- `test_flowfix_c16_extra`：`Ran 10 tests in 2.014s` / `OK`
- `test_flowfix_c16_extra_round54`：`Ran 5 tests in 1.716s` / `OK`
- `test_flowfix_02_review_return`：`Ran 4 tests in 1.118s` / `OK`
- `test_flowfix_c1_extra`：`Ran 49 tests in 10.700s` / `OK`
- `test_flowfix2_dispatch`：`Ran 40 tests in 16.341s` / `OK`
- `test_prompt_snapshots`：`Ran 1 test in 2.760s` / `OK`
- `test_flowfix_c17_extra_round54`：`Ran 6 tests in 0.003s` / `OK`
- `test_rulebook_health`：`Ran 9 tests in 0.346s` / `OK`

改动文件：`collab/dispatch.py`、`tests/test_card_gap_d_acceptance.py`、`docs/跑卡卡点修理/卡D设计.md`（末尾已补“DeepSeek 接手后改的（11-27）”）。没有未完成项，也没有需要绑端口而在沙箱里失败的测试。