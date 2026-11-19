# 按施工单 C1.6 调整跑卡流程（复查有个头）

## 要做什么
按施工单 /Users/rs/Desktop/project/开发助手/docs/跑卡流程调整/施工单-C1.6.md 做：复查有个头。第 1 轮照旧严格；退回后的复查按编号核对上轮问题（复查员逐条答改好了 / 没改好，漏答算没改好），只退回没改好的、新冒出的严重的和测试写错的，其余新问题记以后再修和台账；复查清单每条加“碰上”（常见 / 少见 / 极巧合），第 1 轮极巧合又不严重的也记以后再修；退回用完只剩不严重的，先等这一轮全部测试跑完，全过才自动进待验收，改坏老功能或没跑成就停下；还有严重的照旧停下。发现改坏原来能用的功能一律标严重。允许改：collab/dispatch.py、collab/phase.py、collab/规则.md、tests/test_flowfix_c16_extra*.py（新建）。验收命令：python3 -m unittest discover -s tests -p "test_flowfix_c1*.py" && python3 -m unittest discover -s tests -p "test_flowfix4_*.py" && python3 -m unittest discover -s tests -p "test_flowfix2_dispatch.py" && python3 -m unittest discover -s tests -p "test_flowfix_0[12]_*.py" && python3 -m unittest discover -s tests -p "test_flowfix_14_*.py" && python3 -m unittest discover -s tests -p "test_collab_flow.py" && python3 -m unittest discover -s tests -p "test_collab_review.py" && python3 -m unittest discover -s tests -p "test_c15_claude_fixes.py" && python3 -m unittest discover -s tests -p "test_review_fmt.py"

## 不做什么
不改退回次数上限（还是 2 次）；不改复查强度；不改“接受剩下的问题”那条路和全部测试的跑法（下一张卡做）；不碰 C2 的内容；不改验收测试、施工单第五节列出的老测试、转换工具。

## 怎么算做完
验收命令全部通过；全部测试不因这次改动变坏。

## 允许改动的范围

- collab/dispatch.py
- collab/phase.py
- collab/规则.md
- tests/test_flowfix_c16_extra.py
## 追加要求
删掉第 1 步和第 14 步里“跑一遍全部测试”的部分：全部测试由程序在复查时另跑，施工方只跑验收命令（开工.json 里那条），不要跑全部测试。其余步骤不变。
