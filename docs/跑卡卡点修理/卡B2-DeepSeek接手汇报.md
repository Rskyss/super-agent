已修完并验证。改动只在当前目录，未 commit、未推送。

**逐条修改**
- 严重 1：pytest 插件改为逐项记录 `setup/call/teardown` 结果，并直接取 `item.path`、`listchain()` 类层级、`item.name`。任何 setup/teardown 失败（含 session fixture 收尾）一律“认不全”。用例：`test_r3_session_teardown_error_not_attributed`。
- 严重 2：`runner_kind` 只要 Node 参数里有 `--run` 或 `--run=...` 就返回 `None`，不再当直接测试命令。用例：`test_r3_node_run_task_not_direct_test`。
- 严重 3：Node 事件必须带 `entryFile`，缺失就“认不全”，不再退回用 `file`。同步给三个 node 样例补了 `entry`。用例：`test_r3_node_missing_entry_file_unknown`。
- 严重 4：pytest 每项必须有 call 结果；只有 setup 记录（没有 call、没有明确 skip）算“认不全”。用例：`test_r3_last_item_exit_zero_not_passed`。
- 严重 5：偶发重跑改为从头按“步骤、文件、项”核对，不再预先减掉所有偶发项；同一项在另一步新坏也会带回。用例：`test_r3_flaky_cannot_hide_new_failure_in_other_step`。
- 严重 6：新增 `_step_total`，每个直接测试步骤单独检查总项数，0 项即该步“认不全”，后面的步骤不能盖掉。用例：`test_r3_zero_test_step_cannot_be_covered`。
- 严重 7：`split_steps` 遇到任何 `$` 都不拆，整条当一步。用例：`test_r3_dollar_expansion_not_split`。
- 严重 8：名单 Broken 会强制结果算没过，`_FullSlot.finish` 先检查 Broken（命令过没过都停），人工接受也先拦；名单路径是符号链接（含失效链接）一律 Broken，增删拒绝且不覆盖。用例：`test_r3_broken_list_stops_even_when_command_passes`、`test_r3_symlink_list_is_broken_and_not_overwritten`。
- 严重 9：`load()` 与增删共用一把锁（共享锁），台账未记成功时关卡看不到新名单；撤多条只写一行台账，全成或全不撤。用例：`test_r3_load_waits_for_ledger_before_publishing`、`test_r3_remove_many_writes_one_ledger_line`。
- 中等 1：Node 重跑新失败改用重跑那一遍的 Step 输出和证据；Python 单独重跑换出的新项进入退回清单和输出。用例：`test_r3_rerun_new_failure_uses_rerun_output`、`test_r3_python_rerun_new_item_reaches_prompt`。
- 中等 2：退回清单直接用已认出的项构造；保留 `checks/` 等任意文件名（不再靠测试路径筛）。Python 清单按方法扣掉改之前同文件的旧失败。用例：`test_r3_checks_file_kept_in_listing`、`test_r3_python_listing_drops_old_failure`。
- 中等 3：裸 `pytest` 只在启用名单的执行路径链接进工具目录；`--version/--help/--collect-only` 等按普通步骤处理。用例：`test_r3_bare_pytest_only_when_listed`。
- 中等 4：pytest 项名直接取收集对象结构，`ids=["a]::b"]` 的正式项名 `test_pay[a]::b]` 可照抄登记。用例：`test_r3_param_id_with_bracket_and_colons`。
- 中等 5：名单条目必须有非空字符串 `by`、`at`、`why`，否则 Broken。用例：`test_r3_entry_requires_by_at_why`。
- 中等 6：补了真实人工接受用例：登记持续失败的“偶发”项，断言接受失败、卡状态保持“需Claude介入”。用例：`test_persistent_flaky_not_accepted`。这条是补缺口，当前实现本来就该拦；它会在人工接受错误地把偶发当等卡时变红。
- 轻微：流转摘要改为“整条命令重跑时过了”。用例：`test_r3_flaky_summary_wording`。

施工设计 `docs/跑卡卡点修理/卡B2设计.md` 已同步上述口径。

**测试结果**
- B2 验收：`Ran 92 tests in 192.941s` / `OK`（原 72 项，新增 20 项）
- `test_batch3_full_command`：`Ran 30 tests in 27.044s` / `OK`
- `test_external_trial_fixes`：`Ran 67 tests in 22.425s` / `OK`
- `test_card_gap_b1_acceptance`：`Ran 17 tests in 0.195s` / `OK`
- `test_acceptance_20_merge`：`Ran 27 tests in 16.474s` / `OK`
- `test_fullcard_extra`：`Ran 18 tests in 12.426s` / `OK`

说明：后两套测试需要绑本机端口，在沙箱里运行会报 `PermissionError: [Errno 1] Operation not permitted`（27/27 setup 失败）；按同一命令在沙箱外复跑均为上面的 `OK`，不是代码回归。