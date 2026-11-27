# 施工单 卡B：全部测试按失败项名字比改前改后

> 依据 [方案](方案.md)（用户 2026-11-26 批准）第 ② 类、[任务清单](任务清单.md) 卡 B。
> 拆卡判断：本单是第 2/4 张，前一张：卡 A（CARD-028，已放进项目 7f270f8）。
> 写给开发助手的施工方。**验收测试已经写好**（`tests/test_card_gap_b_acceptance.py`），施工方**不许改**；写测试的只**补充**，写进新文件 `tests/test_card_gap_b_extra.py`。**只按本施工单做，没要求的功能、写法不要自己加。**

## 一、要做什么（一句话）

复查时跑的“全部测试”，改前改后都没过的项（比如别的卡先放进项目、功能还没做的验收测试）不再算这张卡改坏；只有“改之前没有、改之后多出来”的失败项才退回写代码的，并在交代里写明是哪几项。为此把散在三处的“认出哪几项没过”合成一份 `fullrun.failing_tests`，认 unittest、node/tsx、pytest 三种输出；认不出名字时照旧停下说比不出。

## 二、允许改哪些文件

- `collab/fullrun.py`
- `collab/dispatch.py`
- `collab/progress.py`
- `tests/test_card_gap_b_extra.py`（写测试的新建）

**不许改**：`tests/test_card_gap_b_acceptance.py`、所有老测试、规则包 `agents/`，以及上面没列的任何文件。

## 三、具体做法

动手前先看第四节状态表和第五节原有行为清单。

### P1 一份失败项解析：`fullrun.failing_tests(output) -> set | None`

现在有三份各认各的：`fullrun.failing_names` / `failing_files`（只认 unittest 的 `FAIL:/ERROR:` 行）、`dispatch._failing`（第 2536 行，unittest）、`progress.failing_names`（第 98 行，unittest，还把老版“模块.类”和 3.12 起“模块.类.方法”两种写法认成同一个名字）。合成 `collab/fullrun.py` 里的一份：

- unittest：`FAIL:` / `ERROR:` 行，名字统一成“模块.类.方法”（照 `progress.failing_names` 现有的统一办法）。
- node / tsx（`node --test`）：`✖ 名字 (12.3ms)` 行，去掉开头缩进和末尾的耗时括号；遇到 `✖ failing tests:` 这一行，后面的内容都不再计（那是重复的汇总）。
- pytest：`FAILED 路径::名字 - 原因` 行，取 `路径::名字`。
- 返回失败项名字的集合；全过返回空集合；输出里看得出有失败（unittest 的 `FAILED (`、node 的 `ℹ fail N`/`# fail N` 且 N>0、pytest 的 `N failed`）却一个名字都认不出时，返回 `None`。

`failing_files` 仍保留（Python 逐文件跑要按文件分组），但它用的那条 unittest 行规则和 `failing_tests` 共用同一处定义，不再各写一份。删掉 `fullrun.failing_names`（如没有别处用）、`dispatch._failing`、`progress.failing_names`，调用方改用 `failing_tests`（要列表的地方用 `sorted(...)`）。

### P2 `more_failures` 搬家

`dispatch._more_failures`（第 2544 行，“这次比改之前多坏了”：多出失败项，或认不出名字时按 unittest 失败总数比）搬到 `collab/fullrun.py`，改名 `more_failures(now_out, base_out) -> bool`，判断口径不变，名字比较改用 P1。`dispatch._broken_by_change` 改调它。老测试 `tests/test_flowfix3_dispatch.py::test_more_failures_in_same_file` 已由写施工单的人改成从新位置导入。

### P3 自定义全部测试命令按名字比

`dispatch.py` 的 `_finish_command`（第 3304 行）现在只看改之前整条命令过没过。改成：

1. 改之前没跑成（超时、准备失败）：照旧停下（现有）。
2. 改之前过了：照旧退回写代码的并附输出（现有）。
3. 改之前也没过：用 P1 分别认出改前、改后的失败项名字。
   - 两边都认得出：改后比改前**多出来**的项算这次改坏——退回写代码的，交代里列出这几项的名字（加在现有附的输出前面）；没有多出来的——放行（返回通过），在流转里记一条“全量”，摘要写明“改之前就不过 N 项，这次没有新的”（N 是改之前的失败项个数）。
   - 任一边认不出（`None`）：照旧停下，说“本来就不过、比不出”（现有）。

Python 那一套（`culprits`）的逐文件比较也改用 P1 的名字，做法不变。

## 四、状态表

不涉及（不记状态、不切换；每次复查各跑各的改前改后）。

## 五、原有行为清单

| 老行为（用户看得到的） | 现在由哪个测试守着 |
|---|---|
| 自定义命令全部测试全过 → 放行，右栏写明用的哪条命令 | `tests/test_batch3_full_command.py::test_pass` |
| 改之前过、改之后不过 → 退回写代码的，附没过的那段输出和命令 | `tests/test_batch3_full_command.py::test_broken_goes_back_to_dev_with_output` |
| 改之前也不过、认不出名字 → 停下说“本来就不过” | `tests/test_batch3_full_command.py::test_broken_before_stops_card`、验收 `CompareByName::test_unrecognized_still_stops` |
| 改之前跑不成、超时 → 停下 | `tests/test_batch3_full_command.py::test_base_not_runnable_stops_card`、`test_timeout_stops_card` |
| Python 项目逐文件跑、只算这次改坏的文件；单独跑能过算测试不稳 | `tests/test_flowfix4_full.py`、`tests/test_flowfix4_round2.py`、`tests/test_flowfix4_round3.py`、`tests/test_speedup_19.py` |
| 同一文件里多坏了几项也算改坏；认不出名字按失败总数比 | `tests/test_flowfix3_dispatch.py::test_more_failures_in_same_file` |
| 施工进度、先红名单按失败项名字计（新旧 Python 写法认成同一项） | `tests/test_flowfix4_progress.py`、`tests/test_flowfix_b_extra_progress.py`、`tests/test_flowfix_b_display.py`、`tests/test_batch3_d2_*.py`、`tests/test_batch3_d1_acceptance.py` |

修退回问题时如果要改这里的行为：停下不改，写进汇报。

## 六、不做什么

不改全部测试怎么跑（Python 逐文件同时跑、自定义命令整条跑的跑法都不变）、不改跑的时机、不改“单独跑能过算测试不稳”的判断、不改测试超时、不改界面、不改规则包。

## 七、老测试

开工前由写施工单的人（Claude）已改好、施工方不许改：`tests/test_flowfix3_dispatch.py::test_more_failures_in_same_file` 改成从 `collab.fullrun` 导入 `more_failures`（判断口径不变）；它现在按预期报“找不到”，搬家后会过。

开工前跑过第五节列的老测试，全过（`test_flowfix4_progress.py` 第一次有 1 项不过，连跑两次都过，是计时偶发）。

## 八、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_card_gap_b_*.py" && python3 -m unittest discover -s tests -p "test_batch3_full_command.py" && python3 -m unittest discover -s tests -p "test_flowfix4_full.py" && python3 -m unittest discover -s tests -p "test_flowfix4_round2.py" && python3 -m unittest discover -s tests -p "test_flowfix4_round3.py" && python3 -m unittest discover -s tests -p "test_flowfix3_dispatch.py" && python3 -m unittest discover -s tests -p "test_speedup_19.py" && python3 -m unittest discover -s tests -p "test_flowfix4_progress.py" && python3 -m unittest discover -s tests -p "test_flowfix_b_extra_progress.py" && python3 -m unittest discover -s tests -p "test_flowfix_b_display.py" && python3 -m unittest discover -s tests -p "test_batch3_d2_*.py" && python3 -m unittest discover -s tests -p "test_batch3_d1_acceptance.py"`
- 走查：不涉及
- 测试写法：照验收测试，用假的全部测试跑法（`tests/test_batch3_full_command.py` 的 `CmdRunnerFake`）和 `run_card`。
- **只跑上面这些，不要跑全部测试**（全部测试由程序在复查时跑）。
- 汇报用大白话，不写文件名。

## 九、粘给行政的话（用户原样复制）

```
按施工单做：docs/跑卡卡点修理/施工单-卡B-全部测试按名字比.md
要做什么：按施工单 docs/跑卡卡点修理/施工单-卡B-全部测试按名字比.md 做。失败项解析合成 fullrun.failing_tests 一份（认 unittest、node/tsx、pytest），more_failures 搬进 fullrun；复查时的全部测试改前也不过时按失败项名字比，只有多出来的才退回写代码的并列出名字，没有新的就放行并记下改之前就不过几项，认不出名字照旧停下。
不做什么：不改全部测试的跑法、时机、超时和“单独跑能过算不稳”的判断，不改界面和规则包。
怎么算做完：验收命令全过；补充测试写在 tests/test_card_gap_b_extra.py。
允许改哪些文件：collab/fullrun.py、collab/dispatch.py、collab/progress.py、tests/test_card_gap_b_extra.py
验收命令：python3 -m unittest discover -s tests -p "test_card_gap_b_*.py" && python3 -m unittest discover -s tests -p "test_batch3_full_command.py" && python3 -m unittest discover -s tests -p "test_flowfix4_full.py" && python3 -m unittest discover -s tests -p "test_flowfix4_round2.py" && python3 -m unittest discover -s tests -p "test_flowfix4_round3.py" && python3 -m unittest discover -s tests -p "test_flowfix3_dispatch.py" && python3 -m unittest discover -s tests -p "test_speedup_19.py" && python3 -m unittest discover -s tests -p "test_flowfix4_progress.py" && python3 -m unittest discover -s tests -p "test_flowfix_b_extra_progress.py" && python3 -m unittest discover -s tests -p "test_flowfix_b_display.py" && python3 -m unittest discover -s tests -p "test_batch3_d2_*.py" && python3 -m unittest discover -s tests -p "test_batch3_d1_acceptance.py"
```
