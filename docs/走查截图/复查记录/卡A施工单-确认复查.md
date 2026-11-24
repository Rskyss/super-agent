**卡 A 开工前确认复查。** 第 3 轮的八项里，五项已解决，三项未解决。全程离线，项目文件未改。

| 第 3 轮问题 | 判定 | 证据 |
|---|---|---|
| 复查员记轻微仍会被退回 | 已解决 | 复查交代明确要求标「轻微、原来就有的、功能问题」。用合规清单实跑，旧标法退回 1 条，新标法通过并记入「以后再修」。[复查交代](/Users/rs/Desktop/project/开发助手/agents/交代/复查/走查截图.md:12) · [实跑](/private/tmp/card-a-confirm.oXUFKV/probe.log:9) |
| 右栏认到中断或失效的走查结果 | 未解决 | 同一次开跑内，追加要求可以直接生效。临时卡实测没有新的「开跑」，旧成功仍符合 P5 的拼接条件，会被拼到新一轮先红说明上。另实测「接着跑」刚启动、还没出新判定时，右栏原说明直接显示旧走查成功或失败，单靠拼接过滤挡不住。现有验收用例先写了新判定，盖过了旧结果。[施工单 P5](/Users/rs/Desktop/project/开发助手/docs/走查截图/施工单-卡A.md:72) · [验收用例](/Users/rs/Desktop/project/开发助手/tests/test_walk_card_a_acceptance.py:344) · [实跑](/private/tmp/card-a-confirm.oXUFKV/probe.log:13) |
| 副本代码改坏后台被说成环境出错 | 未解决 | 工具已修。临时副本移除 `set_archived` 后实跑，退出码为 1，判定「没过」，保留了 `ImportError`。但施工单 P3 和工具说明仍写后台起不来按 127 算环境出错，开工口径互相冲突。[施工单 P3](/Users/rs/Desktop/project/开发助手/docs/走查截图/施工单-卡A.md:63) · [工具说明](/Users/rs/Desktop/project/开发助手/tests/walk_tool.py:14) · [实跑](/private/tmp/card-a-confirm.oXUFKV/probe.log:19) |
| 批准前「继续改」丢走查命令 | 已解决 | P1 已明确 AI 重写方案时从原施工单取走查命令，写入 `开工.json`。新增验收用例进入了重写分支，当前正因缺少 `walk_command` 触发预期红测。[施工单 P1](/Users/rs/Desktop/project/开发助手/docs/走查截图/施工单-卡A.md:30) · [验收用例](/Users/rs/Desktop/project/开发助手/tests/test_walk_card_a_acceptance.py:431) · [红测日志](/private/tmp/card-a-confirm.oXUFKV/acceptance.log:254) |
| 卡 A 走查第 3 步被标题误通过 | 已解决 | 按用户决定删掉第三张假卡及其断言。脚本和步骤表均剩两步，原先由标题命中的断言已删除。[走查脚本](/Users/rs/Desktop/project/开发助手/tests/walk_卡A.py:43) · [施工单步骤表](/Users/rs/Desktop/project/开发助手/docs/走查截图/施工单-卡A.md:114) |
| 命令输出只留末尾 | 已解决 | 施工单明确最多保留末尾 4,000 字，并要求文件开头注明截断。长输出实测保留 4,000 字，开头诊断丢弃，末尾诊断保留。[施工单 P3](/Users/rs/Desktop/project/开发助手/docs/走查截图/施工单-卡A.md:52) · [实跑](/private/tmp/card-a-confirm.oXUFKV/probe.log:18) |
| 方案第七节文件夹名 | 已解决 | 已改为 `走查截图/第K次/`，并写明每次新建，与施工单一致。[方案第七节](/Users/rs/Desktop/project/开发助手/docs/走查截图/方案.md:92) · [施工单 P3](/Users/rs/Desktop/project/开发助手/docs/走查截图/施工单-卡A.md:46) |
| 步骤名取不到 | 未解决 | 样板仍先 `page.click()` 再 `shot()`，点不到按钮时异常在步骤名传入前就抛出。A-3 改成「能取到步骤名时写」，已批准的需求仍要求这条场景的退回原因写明步骤。P3 的新过滤也仍会把 `WalkError: “python3 -m unittest”这一步失败` 里的命令当成步骤名。[工具样板](/Users/rs/Desktop/project/开发助手/tests/walk_tool.py:25) · [任务清单 A-3](/Users/rs/Desktop/project/开发助手/docs/走查截图/任务清单.md:15) · [需求共识](/Users/rs/Desktop/project/开发助手/docs/走查截图/需求共识.md:53) · [输出验证](/private/tmp/card-a-confirm.oXUFKV/probe.log:21) |

**本轮改动带出的新问题。** 后台启动故障改判后，正常副本遇到本机拒绝绑定 `127.0.0.1`，也以 1 退出并判「没过」。按 P3，这类权限错误会被计入开发返工。[启动处理](/Users/rs/Desktop/project/开发助手/tests/walk_tool.py:193) · [实跑](/private/tmp/card-a-confirm.oXUFKV/probe.log:20)

**遗留。** 方案第七节仍把流转写成「走查」，施工单规定的记录类型是「页面检查」并带 `walk=True`。[方案第七节](/Users/rs/Desktop/project/开发助手/docs/走查截图/方案.md:92) · [施工单记录格式](/Users/rs/Desktop/project/开发助手/docs/走查截图/施工单-卡A.md:55)

**开工前测试证据。** 验收收集 26 项，8 项通过，3 项命中预期断言，另有 21 个错误（含子用例），均因 `walk_command` 尚未接入。11 项定向旧测试和 7 项可离线运行的工具测试全过。浏览器复跑被本机绑定限制挡在启动阶段。留存截图确认第一步显示走查结果，第二步的假卡经真实右栏数据入口核对，目前只显示「全部测试都过了」，符合开工前预期。[验收日志](/private/tmp/card-a-confirm.oXUFKV/acceptance.log) · [旧测试](/private/tmp/card-a-confirm.oXUFKV/old.log) · [工具测试](/private/tmp/card-a-confirm.oXUFKV/tool-exit.log) · [首步截图](/private/tmp/claude-501/-Users-rs-Desktop-project-----/02e564c5-d6b7-4def-9809-56d395a1a735/scratchpad/a-walk/01-右栏走查刚过.png) · [右栏数据](/private/tmp/card-a-confirm.oXUFKV/probe.log:11)

结论：不通过