# CARD-001：建图环节的 Zep 写入补上瞬时错误重试

> 状态：待用户确认（关口 1）　来源：sp2 台账 C1（"建图环节一次瞬时网络错误就整步失败"）
> 目标项目：`~/Desktop/project/sp2`（backend）

## 1. 背景（已核实的代码事实）

- 建图 = 把材料写入 Zep 知识图谱，再用大模型抽取角色和关系。
- **大模型调用已有重试**：`backend/app/services/graph_builder.py` 第 203、218、259 行都经过 `call_with_retry`（`app/services/llm_retry.py`）。台账里的问题，这一半已经修好。
- **Zep 写入没有重试**（本卡要做的）：
  - `run_build_graph` 第 294 行 `zep_client.ensure_graph(...)` 直接调用；
  - 第 304 行 `zep_client.add_text(...)` 直接调用；
  - `app/clients/zep_client.py` 的 `ensure_graph`（约 33 行）和 `add_text`（约 46 行）内部都没有重试；`add_text` 按段循环上传，任何一段失败整步失败。

## 2. 需求

在 Zep 写入的两处补上瞬时错误重试，**复用现有的 `call_with_retry` 和 `is_transient_error`**，不新写一套判断。

1. `ensure_graph`：瞬时错误重试；"already exists" 仍视为成功（保持现有行为）。
2. `add_text`：**按段重试**。某一段瞬时失败，只重试这一段，不重传已成功的段（否则 Zep 里会出现重复数据）。

## 3. 不做的事

- 不改大模型调用部分（已有重试）。
- 不改任务界面、不加"断点续跑"、不加新的用户提示文案。
- 不改重试次数（沿用 `llm_retry` 的 3 次）和判定清单。
- 不动删除图谱、可视化、报告相关代码。

## 4. 验收标准（全部可自动判断）

| 编号 | 场景 | 期望 |
|---|---|---|
| A1 | `add_text` 第 2 段第 1 次抛 "connection reset" | 该段重试后成功；第 1 段只写入 1 次，第 2 段写入 2 次，第 3 段写入 1 次 |
| A2 | `add_text` 某段抛 "invalid api key"（永久错误） | 立即抛出，该段只调用 1 次 |
| A3 | `ensure_graph` 第 1 次抛 "503 service unavailable" | 重试后成功 |
| A4 | `ensure_graph` 抛 "already exists" | 视为成功，不重试、不抛错（保持现有行为） |
| A5 | 同一段连续 3 次都是瞬时错误 | 最终抛出，`run_build_graph` 把任务置为 failed（与现在一致） |
| A6 | 全量后端测试 | `cd backend && uv run pytest -q` 全部通过，且新增用例覆盖 A1–A5 |

测试约束：重试等待时间在测试里必须置零或打桩（`call_with_retry` 有 `pause` 参数），否则测试会真的睡 2–6 秒。

## 5. 技术约束

- 复用：`app/services/llm_retry.py` 的 `call_with_retry`、`is_transient_error`。
- 测试风格：参照 `tests/test_zep_client.py`、`tests/test_llm_retry.py`，用 fake client 计数调用次数。
- 不新增依赖。
- 代码风格沿用现有文件（中文注释，说明"为什么"）。

## 6. 风险

- 按段重试要求 `add_text` 的循环里拿到每段的结果，实现时需注意 `on_chunk` 的进度回调不能重复触发。
- 若 Zep 的"写入一段"在服务端已部分成功，重试可能重复。这是 Zep 客户端行为，需用 fake client 无法验证，**第一版不处理，记为已知风险**。

## 7. 需要你确认的点

1. 范围是否按上面缩小（只做 Zep 写入，大模型部分已完成）？
2. 同意按段重试、不重传已成功段？
3. 验收 A1–A6 是否足够？

确认后我把这张卡交给开发（Cursor）执行。
