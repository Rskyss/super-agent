
## 复查（codex）
结论：不通过

指定验收命令原样跑了两次，每次约一分钟。第一组 147 项都通过。第二组 110 项里，同一项后台进程清理测试两次都失败，串联命令因此没有跑到后七组。最慢的是第一组，约 31–33 秒。全程只读，未修改项目文件，也未另跑全量测试。

复查清单：

```json
{
  "结论": "不通过",
  "问题": [
    {
      "级别": "【严重】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "两次退回用完后，全部测试明明显示跳过，卡片仍会自动进入“等你确认”。",
      "位置": "collab/dispatch.py:2273、collab/dispatch.py:2305、collab/dispatch.py:3134",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:17 和交接资料.md:52 要求已配置的全部测试必须实际通过。collab/cli.py:177 和 collab/cli.py:303 显示正常开工会配置测试执行器。collab/dispatch.py:2273 在找不到可跑的 Python 测试时标记跳过，collab/dispatch.py:2305 在未启动时返回通过，collab/dispatch.py:3134 又把跳过直接当作满足放行条件。"
      }
    },
    {
      "级别": "【严重】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "复查清单有一轮格式写错后，先前没确认修好的严重问题会从下一轮核对里消失，卡片可能带着它进入验收。",
      "位置": "collab/dispatch.py:802、collab/dispatch.py:814、collab/dispatch.py:1854、collab/dispatch.py:3103、collab/dispatch.py:3160",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:12 和方案.md:15 要求漏答的问题继续按编号退回。设首轮退回一条严重问题，第二轮清单和重写都不合格。collab/dispatch.py:3103、collab/dispatch.py:3113 和 collab/dispatch.py:3160 只存第二轮原文，没有存下先前仍待核对的条目。下一轮在 collab/dispatch.py:1854 取不到编号，在 collab/dispatch.py:823 也取不到要继续判断的原条目。第三轮即使还写着不通过，只要列出来的都是新小问题，collab/dispatch.py:850 就会判为通过。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "复查员查明是测试写错后，卡片仍可能把它当成普通小问题延期，写测试的人收不到修正任务。",
      "位置": "collab/dispatch.py:833、collab/dispatch.py:836、collab/dispatch.py:3125、collab/dispatch.py:3185",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:16 和方案.md:18 要求测试写错要退回给写测试的人。上轮留下一个中等功能问题，本轮确认同一现象、同一位置其实是测试写错，且仍标中等。collab/dispatch.py:836 只按现象和位置合并，只有新条目升级为严重才替换，因此测试写错被丢掉。退回用完时，collab/dispatch.py:3125 会把剩下的条目当成普通小问题处理。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "全部测试没过而停下时，用户能看到测试失败的原因，却看不到这一轮还有哪些问题没改好，也找不到本轮新发现的小问题的延期记录。",
      "位置": "collab/dispatch.py:1238、collab/dispatch.py:3138、collab/dispatch.py:3153、collab/phase.py:359",
      "证据": {
        "方式": "推演",
        "对照": "施工单-C1.6.md:44 要求登记后续轮次新发现的小问题，施工单-C1.6.md:54 要求转人工时带齐实际剩余条目。collab/dispatch.py:3143 只把全部测试失败的原因和输出交给转人工记录，下一行就返回，既没保存上轮没改好的原条目，也没走到 collab/dispatch.py:3153 登记本轮原本就该延期的新问题。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "复查员说还有问题没改好，只要填错了一个不存在的编号，卡片就可能把“不通过”显示成“复查通过”。",
      "位置": "collab/dispatch.py:634、collab/dispatch.py:816、collab/dispatch.py:825、collab/dispatch.py:850",
      "证据": {
        "方式": "推演",
        "对照": "施工单-C1.6.md:26 和施工单-C1.6.md:42 允许用上轮实际没改好的条目来说明不通过。若上轮只有第 1 条，本轮写第 1 条改好了、第 2 条没改好，问题列表为空，collab/dispatch.py:634 会接受这份清单。collab/dispatch.py:833 只遍历实际存在的第 1 条，第 2 条被忽略，collab/dispatch.py:850 最终记为通过。首轮误填“上轮核对”也会在 collab/dispatch.py:816 被直接当成通过。"
      }
    },
    {
      "级别": "【轻微】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "两个地方遇到同样的小问题时，“等你确认”只算一件。点过“继续改”以后，之前延期的小问题还可能被说成开工前就有。",
      "位置": "collab/dispatch.py:857、collab/dispatch.py:889、collab/phase.py:259、collab/phase.py:465",
      "证据": {
        "方式": "推演",
        "对照": "交接资料.md:37 要求同现象、不同位置各算一件，交接资料.md:70 要求历史条目不计入本次剩余问题。collab/dispatch.py:857 和 collab/dispatch.py:889 存给页面的是展示文字，collab/phase.py:266 按文字去重，两个位置会合成一件。当前没有新延期时，collab/phase.py:469 又用包含历史条目的列表计数，统一说成开工前就有。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【原来就有的】",
      "类型": "功能问题",
      "现象": "一轮测试结束后，仍能查到它启动的后台进程。这让本卡的验收命令两次都提前停在第二组。",
      "位置": "tests/test_flowfix4_round3.py:94、tests/test_flowfix4_round3.py:107、collab/fullrun.py:185",
      "证据": {
        "方式": "实跑",
        "命令": "指定串联验收命令的第二段：python3 -m unittest discover -s tests -p \"test_flowfix4_*.py\"",
        "输出": "两次均为 Ran 110 tests、FAILED (failures=1)。失败用例 test_new_session_grandchild_cleaned 在检查后台进程号时得到 AssertionError: ProcessLookupError not raised。该用例直接调用原有的测试进程清理代码，两处与开工前的 git diff 均无改动。未另跑开工前版本。"
      }
    }
  ]
}
```

## 复查格式重写（codex）
结论：不通过

上次复查把指定的验收命令原样跑了两次，每次约一分钟。第一组 147 项全部通过，也是最慢的一组，用了约 31–33 秒。第二组有 110 项，两次都是同一项后台进程清理测试失败，导致后七组都没跑到。上次复查全程只读，没有修改项目文件，也没有另跑全量测试。

这次只整理原回复。按退回提示，下面清单里的级别和来源使用不带括号的值。

复查清单：

```json
{
  "结论": "不通过",
  "问题": [
    {
      "级别": "严重",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "两次退回用完后，全部测试明明显示跳过，卡片仍会自动进入“等你确认”。",
      "位置": "collab/dispatch.py:2273、collab/dispatch.py:2305、collab/dispatch.py:3134",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:17 和交接资料.md:52 要求已配置的全部测试必须实际通过。collab/cli.py:177 和 collab/cli.py:303 显示正常开工会配置测试执行器。collab/dispatch.py:2273 在找不到可跑的 Python 测试时标记跳过，collab/dispatch.py:2305 在未启动时返回通过，collab/dispatch.py:3134 又把跳过直接当作满足放行条件。"
      }
    },
    {
      "级别": "严重",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "复查清单有一轮格式写错后，先前没确认修好的严重问题会从下一轮核对里消失，卡片可能带着它进入验收。",
      "位置": "collab/dispatch.py:802、collab/dispatch.py:814、collab/dispatch.py:1854、collab/dispatch.py:3103、collab/dispatch.py:3160",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:12 和方案.md:15 要求漏答的问题继续按编号退回。假设首轮退回一条严重问题，第二轮清单和重写都不合格。collab/dispatch.py:3103、collab/dispatch.py:3113 和 collab/dispatch.py:3160 只存第二轮原文，没有存下先前仍待核对的条目。下一轮在 collab/dispatch.py:1854 取不到编号，在 collab/dispatch.py:823 也取不到要继续判断的原条目。第三轮即使还写着不通过，只要列出来的都是新小问题，collab/dispatch.py:850 就会判为通过。"
      }
    },
    {
      "级别": "中等",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "复查员查明是测试写错后，卡片仍可能把它当成普通小问题延期，写测试的人收不到修正任务。",
      "位置": "collab/dispatch.py:833、collab/dispatch.py:836、collab/dispatch.py:3125、collab/dispatch.py:3185",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:16 和方案.md:18 要求测试写错要退回给写测试的人。上轮留下一个中等功能问题，本轮确认同一现象、同一位置其实是测试写错，且仍标中等。collab/dispatch.py:836 只按现象和位置合并，只有新条目升级为严重才替换，因此测试写错被丢掉。退回用完时，collab/dispatch.py:3125 会把剩下的条目当成普通小问题处理。"
      }
    },
    {
      "级别": "中等",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "全部测试没过而停下时，用户能看到测试失败的原因，却看不到这一轮还有哪些问题没改好，也找不到本轮新发现的小问题的延期记录。",
      "位置": "collab/dispatch.py:1238、collab/dispatch.py:3138、collab/dispatch.py:3153、collab/phase.py:359",
      "证据": {
        "方式": "推演",
        "对照": "施工单-C1.6.md:44 要求登记后续轮次新发现的小问题，施工单-C1.6.md:54 要求转人工时带齐实际剩余条目。collab/dispatch.py:3143 只把全部测试失败的原因和输出交给转人工记录，下一行就返回，既没保存上轮没改好的原条目，也没走到 collab/dispatch.py:3153 登记本轮原本就该延期的新问题。"
      }
    },
    {
      "级别": "中等",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "复查员说还有问题没改好，只要填错了一个不存在的编号，卡片就可能把“不通过”显示成“复查通过”。",
      "位置": "collab/dispatch.py:634、collab/dispatch.py:816、collab/dispatch.py:825、collab/dispatch.py:850",
      "证据": {
        "方式": "推演",
        "对照": "施工单-C1.6.md:26 和施工单-C1.6.md:42 允许用上轮实际没改好的条目来说明不通过。若上轮只有第 1 条，本轮写第 1 条改好了、第 2 条没改好，问题列表为空，collab/dispatch.py:634 会接受这份清单。collab/dispatch.py:833 只遍历实际存在的第 1 条，第 2 条被忽略，collab/dispatch.py:850 最终记为通过。首轮误填“上轮核对”也会在 collab/dispatch.py:816 被直接当成通过。"
      }
    },
    {
      "级别": "轻微",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "两个地方遇到同样的小问题时，“等你确认”只算一件。点过“继续改”以后，之前延期的小问题还可能被说成开工前就有。",
      "位置": "collab/dispatch.py:857、collab/dispatch.py:889、collab/phase.py:259、collab/phase.py:465",
      "证据": {
        "方式": "推演",
        "对照": "交接资料.md:37 要求同现象、不同位置各算一件，交接资料.md:70 要求历史条目不计入本次剩余问题。collab/dispatch.py:857 和 collab/dispatch.py:889 存给页面的是展示文字，collab/phase.py:266 按文字去重，两个位置会合成一件。当前没有新延期时，collab/phase.py:469 又用包含历史条目的列表计数，统一说成开工前就有。"
      }
    },
    {
      "级别": "中等",
      "来源": "原来就有的",
      "类型": "功能问题",
      "现象": "一轮测试结束后，仍能查到它启动的后台进程。这让本卡的验收命令两次都提前停在第二组。",
      "位置": "tests/test_flowfix4_round3.py:94、tests/test_flowfix4_round3.py:107、collab/fullrun.py:185",
      "证据": {
        "方式": "实跑",
        "命令": "指定串联验收命令的第二段：python3 -m unittest discover -s tests -p \"test_flowfix4_*.py\"",
        "输出": "两次均为 Ran 110 tests、FAILED (failures=1)。失败用例 test_new_session_grandchild_cleaned 在检查后台进程号时得到 AssertionError: ProcessLookupError not raised。该用例直接调用原有的测试进程清理代码，两处与开工前的 git diff 均无改动。未另跑开工前版本。"
      }
    }
  ]
}
```

## 复查（codex）
结论：不通过

- 【严重】【这次改的】复查清单格式写错后，这轮新发现的严重问题可能从下轮核对里消失，卡片可能带着它进入“等你确认”。
- 【严重】【这次改的】两次退回用完后，全部测试实际跑了零项，卡片仍可能自动进入“等你确认”。
- 【中等】【这次改的】复查清单重写仍失败时，上轮没改好的问题可能没交给开发员，写测试的人也收不到对应任务。
- 【中等】【原来就有的】一轮测试结束后，测试仍查得到它启动的后台进程，指定验收命令因此在第二组停下。

11 月 18 日 21:26 完成只读复查。指定命令跑了约 64 秒，最慢的是第一组，约 34 秒，153 项全过。第二组 110 项中有 1 项失败，后七组未执行。对照 Git 确认，失败用例和它直接调用的清理代码都没有本卡改动。未另跑开工前版本或全部测试，未修改文件。

复查清单：

```json
{
  "结论": "不通过",
  "问题": [
    {
      "级别": "【严重】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "复查清单格式写错后，这轮新发现的严重问题可能从下轮核对里消失，卡片可能带着它进入“等你确认”。",
      "位置": "collab/dispatch.py:1909、collab/dispatch.py:3196、collab/dispatch.py:3267、collab/dispatch.py:871",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:13 要求下轮核对实际退回的问题。假设第二轮新报一条严重问题，只把“碰上”填错，重写也未成功。collab/dispatch.py:3186 保留第二轮原话，但 collab/dispatch.py:3196 和 collab/dispatch.py:3269 只把第一轮的条目存进编号清单。第三轮在 collab/dispatch.py:1909 只列这些编号。若第三轮确认第一轮问题已改好，只另报一条轻微问题，collab/dispatch.py:871 会把本轮判为通过，第二轮那条严重问题没有得到确认。"
      }
    },
    {
      "级别": "【严重】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "两次退回用完后，全部测试实际跑了零项，卡片仍可能自动进入“等你确认”。",
      "位置": "collab/dispatch.py:3218、collab/dispatch.py:3260",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:17 要求当轮全部测试跑成并通过后才能放行。项目只要有一个空的 Python 测试文件，collab/fullrun.py:28 就会判定可以跑全部测试。collab/fullrun.py:286 和 collab/fullrun.py:302 会把零项测试汇总为 Ran 0 tests 和 OK。collab/dispatch.py:3218 只检查通过状态，于是会在 collab/dispatch.py:3260 放行。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "复查清单重写仍失败时，上轮没改好的问题可能没交给开发员，写测试的人也收不到对应任务。",
      "位置": "collab/dispatch.py:2919、collab/dispatch.py:2935、collab/dispatch.py:3196、collab/dispatch.py:3296",
      "证据": {
        "方式": "推演",
        "对照": "施工单-C1.6.md:42 要求退回交代带上漏答条目的原现象和位置，施工单-C1.6.md:45 要求照常派改测试或补测试。第二轮如果漏答上一轮的问题，且原话只写了本轮发现、重写又不合格，collab/dispatch.py:3196 会把上一轮条目保存在退回记录里。实际派活时，collab/dispatch.py:3296 只把第二轮原话交给开发员，改测试和补测试在另一分支。中断后接着跑也会在 collab/dispatch.py:2935 清空这两份派活清单。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【原来就有的】",
      "类型": "功能问题",
      "现象": "一轮测试结束后，测试仍查得到它启动的后台进程，指定验收命令因此在第二组停下。",
      "位置": "tests/test_flowfix4_round3.py:94、tests/test_flowfix4_round3.py:107、collab/fullrun.py:223",
      "证据": {
        "方式": "实跑",
        "命令": "指定串联验收命令的第二段：python3 -m unittest discover -s tests -p \"test_flowfix4_*.py\"",
        "输出": "Ran 110 tests in 30.054s。test_new_session_grandchild_cleaned 失败，AssertionError: ProcessLookupError not raised。FAILED (failures=1)。串联命令退出码为 1，后七组未执行。"
      }
    }
  ]
}
```

## 复查格式重写（codex）
结论：不通过

- 【严重】【这次改的】复查清单格式写错后，这轮新发现的严重问题可能从下轮核对里消失，卡片可能带着它进入“等你确认”。
- 【严重】【这次改的】两次退回用完后，全部测试实际跑了零项，卡片仍可能自动进入“等你确认”。
- 【中等】【这次改的】复查清单重写仍失败时，上轮没改好的问题可能没交给开发员，写测试的人也收不到对应任务。
- 【中等】【原来就有的】一轮测试结束后，测试仍查得到它启动的后台进程，指定验收命令因此在第二组停下。

上次只读复查于 11 月 18 日 21:26 完成。指定命令跑了约 64 秒，最慢的是第一组，约 34 秒，153 项全过。第二组 110 项中有 1 项失败，后七组未执行。对照 Git 确认，失败用例和它直接调用的清理代码都没有本卡改动。当时未另跑开工前版本或全部测试。

本次只重排原回复，未重新复查、运行测试或修改文件。清单里的级别和来源按校验要求填写纯文字，正文保留方括号标签。

复查清单：

```json
{
  "结论": "不通过",
  "问题": [
    {
      "级别": "严重",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "复查清单格式写错后，这轮新发现的严重问题可能从下轮核对里消失，卡片可能带着它进入“等你确认”。",
      "位置": "collab/dispatch.py:1909、collab/dispatch.py:3196、collab/dispatch.py:3267、collab/dispatch.py:871",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:13 要求下轮核对实际退回的问题。假设第二轮新报一条严重问题，只把“碰上”填错，重写也未成功。collab/dispatch.py:3186 保留第二轮原话，但 collab/dispatch.py:3196 和 collab/dispatch.py:3269 只把第一轮的条目存进编号清单。第三轮在 collab/dispatch.py:1909 只列这些编号。若第三轮确认第一轮问题已改好，只另报一条轻微问题，collab/dispatch.py:871 会把本轮判为通过，第二轮那条严重问题没有得到确认。"
      }
    },
    {
      "级别": "严重",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "两次退回用完后，全部测试实际跑了零项，卡片仍可能自动进入“等你确认”。",
      "位置": "collab/dispatch.py:3218、collab/dispatch.py:3260",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:17 要求当轮全部测试跑成并通过后才能放行。项目只要有一个空的 Python 测试文件，collab/fullrun.py:28 就会判定可以跑全部测试。collab/fullrun.py:286 和 collab/fullrun.py:302 会把零项测试汇总为 Ran 0 tests 和 OK。collab/dispatch.py:3218 只检查通过状态，于是会在 collab/dispatch.py:3260 放行。"
      }
    },
    {
      "级别": "中等",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "复查清单重写仍失败时，上轮没改好的问题可能没交给开发员，写测试的人也收不到对应任务。",
      "位置": "collab/dispatch.py:2919、collab/dispatch.py:2935、collab/dispatch.py:3196、collab/dispatch.py:3296",
      "证据": {
        "方式": "推演",
        "对照": "施工单-C1.6.md:42 要求退回交代带上漏答条目的原现象和位置，施工单-C1.6.md:45 要求照常派改测试或补测试。第二轮如果漏答上一轮的问题，且原话只写了本轮发现、重写又不合格，collab/dispatch.py:3196 会把上一轮条目保存在退回记录里。实际派活时，collab/dispatch.py:3296 只把第二轮原话交给开发员，改测试和补测试在另一分支。中断后接着跑也会在 collab/dispatch.py:2935 清空这两份派活清单。"
      }
    },
    {
      "级别": "中等",
      "来源": "原来就有的",
      "类型": "功能问题",
      "现象": "一轮测试结束后，测试仍查得到它启动的后台进程，指定验收命令因此在第二组停下。",
      "位置": "tests/test_flowfix4_round3.py:94、tests/test_flowfix4_round3.py:107、collab/fullrun.py:223",
      "证据": {
        "方式": "实跑",
        "命令": "指定串联验收命令的第二段：python3 -m unittest discover -s tests -p \"test_flowfix4_*.py\"",
        "输出": "Ran 110 tests in 30.054s。test_new_session_grandchild_cleaned 失败，AssertionError: ProcessLookupError not raised。FAILED (failures=1)。串联命令退出码为 1，后七组未执行。"
      }
    }
  ]
}
```

## 复查（codex）
结论：不通过

- 【严重】【这次改的】复查员明明写出了严重问题，下轮却可能看不到，卡片还可能进入“等你确认”。清单重写也失败时，只要坏清单里能读出一条小问题，程序就不再从正文里找严重问题。位置：`collab/dispatch.py:970`。
- 【中等】【这次改的】复查员把证据写成一句话，退回流程可能直接报错，开发员和写测试的人都收不到任务。程序从坏清单捞回问题时保留了错误格式，派活时却按固定格式读取。位置：`collab/dispatch.py:791`。
- 【中等】【原来就有的】测试结束后仍能查到它启动的后台进程，本卡指定测试再次在第二组停下。该用例和它调用的清理代码都没有本卡改动。位置：`tests/test_flowfix4_round3.py:94`。

11 月 18 日 21:59，只读复查结束。指定命令约跑了 60 秒：最慢的第一组 156 项全过（约 30 秒）；第二组 110 项里有 1 项失败，后七组未执行。上轮点名的零项测试放行、漏答旧问题派活（含接着跑），都有通过的用例；严重问题漏带只修好了能从清单直接读到的情形。未修改文件，未另跑全量测试。

复查清单：

```json
{
  "结论": "不通过",
  "问题": [
    {
      "级别": "【严重】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "复查员明明写出了严重问题，下轮却可能看不到，卡片还可能进入“等你确认”。",
      "位置": "collab/dispatch.py:970、collab/dispatch.py:983、collab/dispatch.py:3448",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:13 要求下轮按编号核对实际退回的问题。第二轮正文标出一条严重问题，末尾清单只列一条轻微问题且把“碰上”填成“偶尔”，重写仍失败。collab/dispatch.py:970 读到了轻微条目，collab/dispatch.py:981 将它筛掉，collab/dispatch.py:983 却因清单非空而不再找正文中的严重项。collab/dispatch.py:3448 因此只存上轮旧条目供下轮编号。若第三轮确认旧条目改好、只新报轻微问题，collab/dispatch.py:1008 会判复查通过；若测试通过，collab/dispatch.py:3496 会进入“等你确认”。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【这次改的】",
      "类型": "功能问题",
      "现象": "复查员把证据写成一句话，退回流程可能直接报错，开发员和写测试的人都收不到任务。",
      "位置": "collab/dispatch.py:699、collab/dispatch.py:895、collab/dispatch.py:791、collab/dispatch.py:2778",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:16 要求保留原有派活方式。一条明确写了级别和位置的严重功能问题，把“证据”写成非空字符串，collab/dispatch.py:699 会要求重写。重写仍失败时，collab/dispatch.py:895 和 collab/dispatch.py:979 会把这个字符串原样带入实际退回条目。collab/dispatch.py:2778 在派活前翻译该条目，collab/dispatch.py:792 对字符串调用 .get() 会抛出 AttributeError，补测试和开发都还没派出。"
      }
    },
    {
      "级别": "【中等】",
      "来源": "【原来就有的】",
      "类型": "功能问题",
      "现象": "测试结束后仍能查到它启动的后台进程，本卡指定测试再次在第二组停下。",
      "位置": "tests/test_flowfix4_round3.py:94、tests/test_flowfix4_round3.py:107、collab/fullrun.py:223",
      "证据": {
        "方式": "实跑",
        "命令": "指定串联命令的第二段：python3 -m unittest discover -s tests -p \"test_flowfix4_*.py\"",
        "输出": "test_new_session_grandchild_cleaned 在第 107 行失败：AssertionError: ProcessLookupError not raised。Ran 110 tests in 29.450s；FAILED (failures=1)。串联命令退出码为 1，后七组未执行。Git 对照显示该测试文件及它直接调用的 collab/fullrun.py 均无本卡改动。"
      }
    }
  ]
}
```

## 复查格式重写（codex）
结论：不通过

- 【严重】【这次改的】复查员明明写出了严重问题，下轮却可能看不到，卡片还可能进入“等你确认”。清单重写也失败时，只要坏清单里能读出一条轻微问题，程序就不再从正文里找严重问题。位置：`collab/dispatch.py:970`。
- 【中等】【这次改的】复查员把证据写成一句话，退回流程可能直接报错，开发员和写测试的人都收不到任务。程序从坏清单捞回问题时保留了错误格式，派活时却按固定格式读取。位置：`collab/dispatch.py:791`。
- 【中等】【原来就有的】测试结束后仍能查到它启动的后台进程，本卡指定测试再次在第二组停下。该用例和它调用的清理代码都没有本卡改动。位置：`tests/test_flowfix4_round3.py:94`。

上次只读复查于 11 月 18 日 21:59 结束。指定命令约跑了 60 秒：最慢的第一组 156 项全过（约 30 秒）；第二组 110 项里有 1 项失败，后七组未执行。上轮点名的零项测试放行、漏答旧问题派活（含接着跑），都有通过的用例；严重问题漏带只修好了能从清单直接读到的情形。上次未另跑全量测试；本次只补格式，没有重新复查、跑测试或修改文件。

复查清单：

```json
{
  "结论": "不通过",
  "问题": [
    {
      "级别": "严重",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "复查员明明写出了严重问题，下轮却可能看不到，卡片还可能进入“等你确认”。",
      "位置": "collab/dispatch.py:970、collab/dispatch.py:983、collab/dispatch.py:3448",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:13 要求下轮按编号核对实际退回的问题。第二轮正文标出一条严重问题，末尾清单只列一条轻微问题且把“碰上”填成“偶尔”，重写仍失败。collab/dispatch.py:970 读到了轻微条目，collab/dispatch.py:981 将它筛掉，collab/dispatch.py:983 却因清单非空而不再找正文中的严重项。collab/dispatch.py:3448 因此只存上轮旧条目供下轮编号。若第三轮确认旧条目改好、只新报轻微问题，collab/dispatch.py:1008 会判复查通过；若测试通过，collab/dispatch.py:3496 会进入“等你确认”。"
      }
    },
    {
      "级别": "中等",
      "来源": "这次改的",
      "类型": "功能问题",
      "现象": "复查员把证据写成一句话，退回流程可能直接报错，开发员和写测试的人都收不到任务。",
      "位置": "collab/dispatch.py:699、collab/dispatch.py:895、collab/dispatch.py:791、collab/dispatch.py:2778",
      "证据": {
        "方式": "推演",
        "对照": "方案.md:16 要求保留原有派活方式。一条明确写了级别和位置的严重功能问题，把“证据”写成非空字符串，collab/dispatch.py:699 会要求重写。重写仍失败时，collab/dispatch.py:895 和 collab/dispatch.py:979 会把这个字符串原样带入实际退回条目。collab/dispatch.py:2778 在派活前翻译该条目，collab/dispatch.py:792 对字符串调用 .get() 会抛出 AttributeError，补测试和开发都还没派出。"
      }
    },
    {
      "级别": "中等",
      "来源": "原来就有的",
      "类型": "功能问题",
      "现象": "测试结束后仍能查到它启动的后台进程，本卡指定测试再次在第二组停下。",
      "位置": "tests/test_flowfix4_round3.py:94、tests/test_flowfix4_round3.py:107、collab/fullrun.py:223",
      "证据": {
        "方式": "实跑",
        "命令": "指定串联命令的第二段：python3 -m unittest discover -s tests -p \"test_flowfix4_*.py\"",
        "输出": "test_new_session_grandchild_cleaned 在第 107 行失败：AssertionError: ProcessLookupError not raised。Ran 110 tests in 29.450s；FAILED (failures=1)。串联命令退出码为 1，后七组未执行。Git 对照显示该测试文件及它直接调用的 collab/fullrun.py 均无本卡改动。"
      }
    }
  ]
}
```
