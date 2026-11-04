# 验收记录：CARD-001（建图 Zep 写入重试）— 第一次试跑

> 日期：2026-11-03（系统时间，版本标记同日）· 状态：✅ 代码与测试已验收，**待用户看效果（关口 2）**，未合并、未提交

## 1. 施工环境
- 目标项目：`~/Desktop/project/sp2`
- 版本标记（动手前）：`pre-CARD-001-20261103-2130`（指向 main 的 `04f7588`）
- 独立副本：`~/Desktop/project/sp2-worktrees/CARD-001`，分支 `card/CARD-001`
- 主工作区：未改动（仍有用户原有的 11 处未提交改动）
- 依赖安装：`uv sync --frozen --python 3.12.11`（Python 3.13 下 tiktoken 编译失败，改用与主 `.venv` 相同的 3.12）

## 2. 改动
- `backend/app/clients/zep_client.py`：`ensure_graph`、`add_text` 套用 `call_with_retry`；`add_text` 按段重试
- `backend/tests/test_zep_client.py`：新增 5 个用例，对应 A1–A5

## 3. 验收结果

| 项 | 结果 | 证据 |
|---|---|---|
| A1 第 2 段瞬时失败只重试该段 | ✅ | `test_add_text_retries_only_failed_chunk` |
| A2 永久错误立即抛出 | ✅ | `test_add_text_permanent_error_raises_immediately` |
| A3 ensure_graph 瞬时错误重试后成功 | ✅ | `test_ensure_graph_retries_transient_then_succeeds` |
| A4 "already exists" 仍视为成功 | ✅ | `test_ensure_graph_already_exists_still_success_without_retry` |
| A5 持续瞬时错误最终抛出 | ✅ | `test_add_text_exhausts_retries_on_persistent_transient` |
| A6 全量后端测试 | ✅ | 改动前 1001 passed；改动后 **1006 passed**，退出码 0 |

## 4. 过程中的问题与偏差（如实记录）

1. **开发员的自报不可信**：Cursor 的汇报写"测试未跑（Shell 被拒）"，但它的测试其实没有跑完。我没有采信汇报，改由我本人运行测试并以退出码为准。→ 印证方案中"过没过由程序判定，不靠 AI 自述"的原则。
2. **测试由开发员自己写，没有交给 Codex**：方案设计是"写与测分开两家"。本次为了让第一轮先跑通，测试由 Cursor 自己写，**这与方案不一致，需在后续试跑中修正**。
3. **Codex 没有参与**：因此"测试员"这一环本轮未验证。
4. **未提交**：按规则，只有用户要求时才 commit，因此改动仍在副本工作区里，未进入 main。
5. **返工轮次**：0 次（一次通过）。方案要求的"至少一次测试没过 → 自动退回"本轮**未触发**，仍需在后续试跑中验证。

## 5. 已知风险（沿用卡片第 6 节）
- 如果 Zep 某段在服务端已部分写入，重试可能重复。本地测试无法验证，需要在真实 Zep 上确认。

## 6. 待用户决定（关口 2）
1. 是否把 `card/CARD-001` 合并进 sp2 的 main（合并前主工作区的 11 处未提交改动需要先处理）。
2. 是否需要在真实 Zep 环境做一次建图验证。
3. 是否按第 4 节第 2、5 点，下一轮试跑改为"Codex 写测试 + 故意制造一次测试失败观察返工"。
