# 施工单 卡A：施工单缺节就不开工

> 依据 [方案](方案.md)（用户 2026-11-26 批准）第 ④ 类、[任务清单](任务清单.md) 卡 A。第 4 版：按开工前复查第 1–3 轮意见改；用户 11-26 定去掉“看页面没认出算缺项”，其余改完直接开工（不送第 4 轮）。
> 拆卡判断：本单是第 1/4 张，前一张：无（第 0 步清账已提交 1eb0cec）。
> 写给开发助手的施工方。**验收测试已经写好**（`tests/test_card_gap_a_acceptance.py`），施工方**不许改**；写测试的只**补充**，写进新文件 `tests/test_card_gap_a_extra.py`。**只按本施工单做，没要求的功能、写法不要自己加。**

## 一、要做什么（一句话）

任务卡点名了一份施工单、项目里也确实有这份施工单，但程序照施工单格式摘不全时，写方案这一步记“方案失败”，右栏那句话写清是哪份施工单、缺哪几项，卡停在待对齐，旧方案和旧开工资料作废、不能被批准；不再悄悄换成 AI 写方案。没点名施工单（或点名的文件不存在）、批准前点过“继续改”、需要先起草项目说明（说明文件不存在或还是模板）的卡，照旧由 AI 写方案。

## 二、允许改哪些文件

- `collab/planner.py`
- `tests/test_card_gap_a_extra.py`（写测试的新建）

**不许改**：`tests/test_card_gap_a_acceptance.py`、所有老测试、规则包 `agents/`，以及上面没列的任何文件。

## 三、具体做法

动手前先看第四节状态表和第五节原有行为清单。

### P1 命令只摘一次，检查和生成共用

现在 `_plan_from_order` 从全文第一处“验收命令”加冒号往后找反引号命令。改成：施工单有“验收”一节时，**只认这一节里以 `- 验收命令` 加冒号开头那一行**（行首就是它，不是一句话中间提到）的反引号命令，别处提到的、同一节里说明文字中提到的命令一律不算；没有“验收”一节的老格式施工单，照旧找全文第一处（老格式由 `tests/test_external_trial_batch2.py` 和验收 `test_old_format_without_check_section_still_plans` 守着）。把这个取法写成一个函数，P2 的检查和照施工单生成**都用它**，不许各摘各的。

### P2 新增 `order_gaps(project, rel) -> list`

返回这份施工单缺的项，用下面固定的说法（列表，没缺就返回空列表）：

| 说法 | 什么时候算缺 |
|---|---|
| `读不了` | 文件存在但读不出来（权限、编码等）。读不了时只返回这一项 |
| `要做什么` | “要做什么”一节摘不出第一段（和 `_plan_from_order` 现在的摘法一致） |
| `允许改哪些文件` | “允许改”一节摘不出任何反引号路径（和现在的摘法一致） |
| `验收命令` | 用 P1 的取法摘不出 |
| `没填完` | 照施工单生成的方案全文（`_render_plan_md` 的结果）、摘出的允许文件、P1 摘出的命令、摘出的走查命令（`walk_command`），任何一处含空白标记 `（待填）`（`collab.workspace.PLACEHOLDER`）。走查那一行留着空白也算（它会写进方案） |

“施工单写了要看页面、但格式没认出”**不在本卡**：用户 11-26 定去掉，记已知问题，靠规则包《施工单模板》的固定写法（`- 走查：放进项目前要看页面：……`）和 `collab/look.py` 现有认法兜着。

`_plan_from_order` 摘不出时仍返回 `None`（`tests/test_batch4_extra_claude.py` 守着）。

### P3 写方案时有缺项就失败，不掉进 AI

`plan_card` 第 427–445 行那段：`existing_orders` 找到施工单后，先调 `order_gaps`：

- **有缺项**：
  1. 删掉这张卡的 `开工.json`（如果有）；把 `方案.md` 写回空白模板 `collab.workspace.PLAN_TEMPLATE`。旧方案、旧开工资料作废，`approve` 因为方案有空白被拦（`collab/cli.py` 第 129 行现成检查）。
  2. 调现成的 `_fail(card_dir, summary, text=…)` 记“方案失败”，返回 2。
     - `summary`（右栏直接显示）固定格式：`<施工单文件名去掉 .md> 缺：<缺项用顿号连>。补好后点“重写方案”。` 例：`施工单-改右栏 缺：验收命令、允许改哪些文件。补好后点“重写方案”。` 缺项**恰好**是 `order_gaps` 的结果，不多不少；**不写文件夹路径**（规则包：进度说明不显示路径）。
     - `text`（点开看的全文）：施工单相对路径，加上每个缺项一行补法例子（照规则包《施工单模板》）。各项例子里必须出现的字：要做什么 → `## 一、要做什么（一句话）`；允许改哪些文件 → `## 二、允许改哪些文件`；验收命令 → 第八节的写法（`- 验收命令` 加冒号再加反引号命令）；没填完 → 写明是哪一项（要做什么 / 允许改哪些文件 / 验收命令 / 走查）里还有 `（待填）`；读不了 → 写“读不了”和读取时的原因原文（权限、编码等）；验收命令的例子要是一条写全的命令，不能是空反引号。
- **没缺项**：照现在照施工单生成。
- 删掉现在 `continue` / `break` 掉进 AI 写方案的那两处。
- 进不进这段的条件（`not need_profile and not revised`）不变。

## 四、状态表

| 什么时候 | 谁的东西 | 存什么 | 恢复成什么 / 显示什么 |
|---|---|---|---|
| 正常情况：新卡，施工单缺项 | 这张卡 | 方案失败一条（摘要 + 全文） | 卡在待对齐；方案是空白模板；没有开工资料；右栏显示失败那句 |
| 已有成功方案，再写一次时施工单缺项 | 这张卡 | 同上，并作废旧方案、旧开工资料 | 同上；批准被拦 |
| 补好施工单后点“重写方案” | 这张卡 | 照施工单生成的方案、开工资料 | 和第一次就写对一样 |
| 页面或数据还在加载时 | 不适用 | | |
| 正在看别的东西时 | 不适用 | | |
| 删掉之后 | 不适用 | | |
| 切到一半又切走 | 不适用 | | |
| 退出应用时 | 不适用（写方案是后台任务，失败已落盘） | | |

## 五、原有行为清单

| 老行为（用户看得到的） | 现在由哪个测试守着 |
|---|---|
| 施工单格式齐全时照施工单生成方案、写开工资料（命令、走查命令、看页面），不叫 AI | `tests/test_batch4_acceptance.py::test_order_gives_the_start_sheet_without_ai`、`tests/test_walk_card_a_acceptance.py::PlannerWalkTest`、`tests/test_batch3_look_page.py`、`tests/test_look_default.py` |
| 没有“验收”一节的老格式施工单，照旧认全文第一处命令 | `tests/test_external_trial_batch2.py`（第 488、575 行一带）、验收 `test_old_format_without_check_section_still_plans` |
| 摘不出时 `_plan_from_order` 返回空，不拿别的段落顶替摘要 | `tests/test_batch4_extra_claude.py::test_missing_summary_section_falls_back` |
| 批准前点过“继续改”的卡由 AI 重写，新要求交给 AI，施工单的走查命令照样记进开工资料 | `tests/test_batch4_extra_round54_regressions.py::test_order_rewrite_goes_back_to_ai_flow`、`tests/test_walk_card_a_acceptance.py::test_walk_command_kept_when_plan_is_rewritten`、验收 `StillAi::test_revised_card_with_broken_order` |
| 需要先起草项目说明（说明文件不存在或还是模板）时由 AI 写方案并写出项目说明 | `tests/test_batch4_extra_round54.py::test_order_needing_project_profile_falls_back_to_ai` |
| 需要先起草项目说明时，即使施工单缺节也照旧由 AI 写方案（不拦） | 验收 `StillAi::test_profile_needed_with_broken_order` |
| 没点名施工单的卡、点名但文件不存在的卡由 AI 写方案 | 验收 `StillAi::test_card_without_order`、`StillAi::test_named_order_file_missing`；`tests/test_batch4_acceptance.py::test_no_order_unchanged` |

修退回问题时如果要改这里的行为：停下不改，写进汇报。

## 六、不做什么

不改施工单的格式要求、不改 `existing_orders` 怎么找施工单、不改“看页面”的认法（`collab/look.py`）、不改 AI 写方案的提示词、不改建卡和批准方案的流程、不改界面、不改规则包。

## 七、老测试

开工前由写施工单的人（Claude）已改好、施工方不许改：
- `tests/test_batch4_acceptance.py`：原 `test_order_missing_test_command_falls_back_to_ai` 改为 `test_order_missing_test_command_stops`（缺命令 → 失败、不叫 AI、说明缺验收命令）。
- `tests/test_batch4_extra_round54.py`：原 `test_order_without_allowed_files_falls_back_to_ai` 改为 `test_order_without_allowed_files_stops`（缺允许文件 → 失败、不叫 AI、说明缺允许改哪些文件）。

这两条现在按预期不过，改对代码后会过。规则包里旧说法（`agents/角色/写方案.md`、`agents/手册/施工单怎么写.md`、`agents/交代/写方案/拆方案.md`）已由写施工单的人改好（含“需要先起草项目说明”这条例外），提示词快照 `tests/fixtures/交代快照/写方案等.json` 已重新生成。

## 八、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_card_gap_a_*.py" && python3 -m unittest discover -s tests -p "test_batch4_acceptance.py" && python3 -m unittest discover -s tests -p "test_batch4_extra_round54.py" && python3 -m unittest discover -s tests -p "test_batch4_extra_round54_regressions.py" && python3 -m unittest discover -s tests -p "test_batch4_extra_claude.py" && python3 -m unittest discover -s tests -p "test_external_trial_batch2.py" && python3 -m unittest discover -s tests -p "test_walk_card_a_acceptance.py" && python3 -m unittest discover -s tests -p "test_flowfix_08_existing_order.py" && python3 -m unittest discover -s tests -p "test_look_default.py" && python3 -m unittest discover -s tests -p "test_batch3_look_page.py" && python3 -m unittest discover -s tests -p "test_prompt_snapshots.py"`
- 走查：不涉及
- 测试写法：用 `tests/acceptance_helpers.py` 的 `ProjectCase`，`FAKE_PLAN` 假装 AI，`codex.log` 数 AI 调用次数，和验收测试一样。
- **只跑上面这些，不要跑全部测试**（全部测试由程序在复查时跑）。
- 汇报用大白话，不写文件名。

## 九、粘给行政的话（用户原样复制）

```
按施工单做：docs/跑卡卡点修理/施工单-卡A-施工单缺节不开工.md
要做什么：按施工单 docs/跑卡卡点修理/施工单-卡A-施工单缺节不开工.md 做。验收命令只摘一次、检查和生成共用；新增 order_gaps 列出施工单缺哪几项；任务卡点名的施工单有缺项时记“方案失败”、右栏写“<施工单名> 缺：…。补好后点“重写方案”。”、作废旧方案和开工资料，不再悄悄换成 AI 写方案；没点名施工单、文件不存在、点过“继续改”、需要先起草项目说明的卡照旧由 AI 写。
不做什么：不改施工单格式要求、不改找施工单和认看页面的办法、不改 AI 写方案的提示词、不改界面和规则包。
怎么算做完：验收命令全过；补充测试写在 tests/test_card_gap_a_extra.py。
允许改哪些文件：collab/planner.py、tests/test_card_gap_a_extra.py
验收命令：python3 -m unittest discover -s tests -p "test_card_gap_a_*.py" && python3 -m unittest discover -s tests -p "test_batch4_acceptance.py" && python3 -m unittest discover -s tests -p "test_batch4_extra_round54.py" && python3 -m unittest discover -s tests -p "test_batch4_extra_round54_regressions.py" && python3 -m unittest discover -s tests -p "test_batch4_extra_claude.py" && python3 -m unittest discover -s tests -p "test_external_trial_batch2.py" && python3 -m unittest discover -s tests -p "test_walk_card_a_acceptance.py" && python3 -m unittest discover -s tests -p "test_flowfix_08_existing_order.py" && python3 -m unittest discover -s tests -p "test_look_default.py" && python3 -m unittest discover -s tests -p "test_batch3_look_page.py" && python3 -m unittest discover -s tests -p "test_prompt_snapshots.py"
```
