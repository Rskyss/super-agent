**改了什么**
- 严重 1 快照分不清：`desktop/clerk/tasks.py` 把未决文件和“同步前副本基于的主线版本、同步后主线 HEAD”的主线/副本/快照摘要记进 `快照待定.json`；人工改动前，“接着跑/继续改/自动生效”都继续停，自动巡视只留一条提醒。判断主线是否改测试改为对照旧、新主线提交里的内容；两边各自改成同一份新断言也会更新快照；整批核对成功才写快照。用例：`Round3SnapshotPending.test_ambiguous_snapshot_stays_blocked_until_resolved`、`test_same_new_assertion_on_both_sides_still_updates_snapshot`。
- 严重 2：`_continue_locked` 同步前先调 `dispatch._undo_unfinished_tester_fix(..., collab.vcs.revert_paths)`，再读快照、同步、刷新。用例：`Round3GuardRestore.test_unfinished_tester_guard_restored_before_sync`。
- 严重 3：`desktop/server.py` 的 `accept_issues` 在 `card_apply_lock` 里启动；`continue_card` 同步前查这张卡的 `running(project, "accept")`；首次判断时记状态和最近一条“转人工”序号，同步后两样都复核。用例：`Round3AcceptLock` 两项、`Round3SyncStamp.test_stop_record_added_during_sync_stops`。
- 严重 4：`collab/merge.py` 的 `sync_copy` 提交进度前先 `git reset -q`，再只暂存 `_selected`。用例：`Round3ProgressCommit.test_prestaged_protected_file_not_in_backup`。
- 严重 5：最终提交改为 `commit --only -- :(literal)文件…`，不再传全局 `--literal-pathspecs`。用例：`Round3FinalCommit.test_final_commit_hook_sees_literal_filename`（真钩子按 `*.tsx` 看见文件并拒绝）。
- 中等 1：先 `merge-base --is-ancestor` 判断要不要同步，再逐文件流式 sha256；读写出错手动报文件名和原因、自动留一条提醒。用例：`test_no_new_main_commit_does_not_read_snapshot`、`test_snapshot_read_error_names_file_manual_and_auto`。
- 中等 2：`collab/dispatch.py` 测试员越界撤回的两处 `git reset` 加 `--literal-pathspecs`。用例：`Round3TesterReset.test_revert_bracket_path_spares_neighbour_in_index`。
- 中等 3：排队验收标准冲突检查移到停起草之前。用例：`Round3CriteriaBeforeDraft.test_conflicting_criteria_refused_before_stopping_draft`。
- 中等 4：删掉只数字符串的 `test_stop_points_pass_fresh_scope`，换成真跑 `run_card` 两个停顿点的用例；补“删除方括号文件按字面”和“开跑失败抛 `ReviseNotStarted`”回归。用例：`Round3StopPoints`、`Round3FinalCommit.test_delete_bracket_file_staged_literally`、`Round2Plain.test_launch_failure_is_revise_not_started`。

**测试结果**
- 卡 C 验收：`Ran 85 tests in 17.203s` / `OK`（原 35 项，新增用例含继承执行）。
- 指定的老测试（逐条跑，最后的 Ran/OK 行）：`test_queued_revise` 36 OK；`test_batch3_same_root` 14 OK；`test_batch3_redesign_note` 9 OK；`test_flowfix4_draft` 13 OK；`test_acceptance_06_plan` 39 OK；`test_acceptance_07_run` 9 OK；`test_acceptance_20_merge` 27 OK；`test_acceptance_23_review_fixes` 70 OK；`test_collab_vcs` 8 OK；`test_acceptance_10_fixes` 50 OK。
- 其中 `test_acceptance_06/07/20/23/10` 要绑本机端口，沙箱里报 `Operation not permitted`；已在沙箱外运行并全部通过。
- 另在临时副本里把 13 处修复逐条改回去跑新用例，全部变红（快照重试、同一新断言、先判断同步、读写失败、还原修测试现场、接受锁、接受中拒绝、转人工序号、进度提交、最终提交钩子、删除方括号、邻居暂存、起草前判断、两个停顿点）。

**其他**
- 设计文档末尾已补 `DeepSeek 接手后改的（11-27，第 3 轮复查）`。
- 已知问题（右栏 409、终端开的卡、本卡起草误挡、运行中无填文件入口）未动；没有 commit、没有推送，改动仅在当前目录。