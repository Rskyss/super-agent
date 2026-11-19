# 按施工单 C1.7 修 C1.6 复查暴露的三处小问题

## 要做什么
按施工单 /Users/rs/Desktop/project/开发助手/docs/跑卡流程调整/施工单-C1.7.md 做：复查清单里的级别、来源、类型带不带【】都认（正式读法和兜底读法都在同一处去括号）；写方案的交代先读规则，并交代不要让施工方跑全部测试。允许改：collab/dispatch.py、collab/planner.py、tests/test_flowfix_c17_extra*.py（新建）。验收命令：python3 -m unittest discover -s tests -p "test_flowfix_c17*.py" && python3 -m unittest discover -s tests -p "test_flowfix4_*.py" && python3 -m unittest discover -s tests -p "test_flowfix_c1[56]*.py" && python3 -m unittest discover -s tests -p "test_c15_claude_fixes.py" && python3 -m unittest discover -s tests -p "test_review_fmt.py" && python3 -m unittest discover -s tests -p "test_flowfix_08_existing_order.py" && python3 -m unittest discover -s tests -p "test_acceptance_11_fixes.py" && python3 -m unittest discover -s tests -p "test_dsh_revision_attachments.py"

## 不做什么
不改后台进程清理功能；不改复查交代的写法；不改写方案的其他内容；不改程序自己的测试命令和跑法；不改验收测试 tests/test_flowfix_c17_acceptance.py 和 tests/test_flowfix4_round3.py。施工方只跑验收命令，不要跑全部测试（全部测试由程序在复查时另跑）。

## 怎么算做完
验收命令全部通过；全部测试不因这次改动变坏。

## 允许改动的范围

- collab/dispatch.py
- collab/planner.py
- tests/test_flowfix_c17_extra*.py
