# 第三批真实03c→03b状态修正

只返回可机械应用apply_patch；不要调用工具、不要扫描/读写文件、不要跑测试。唯一产品文件dsh-plugin/core/src/index.ts。root已补红alignment-confirmations-host第三项，1红2绿：真实CARD-001无调度员建卡后来源.json为`{conversation:'s',request_id:'request',deferred_plan:true}`；原api/cards phase plan_waiting/label等待写方案/needs_you:false，现界面错写进行中，且flow为空。

必须只改Host展示聚合：当真实来源deferred_plan===true且当前阶段是plan_waiting或planning时，给CardRow needsYou:true、phaseLabel“等你处理 · 接调度员”，isRunningNow仍false；保持原phaseCode、actions、后台状态与文件不变，避免TaskDockBridge既有流程受影响。这样左栏数字和分组、小条都能共享真实等待状态。不要凭缺少PID就推断没调度员：没有deferred标记的暂停卡仍显示暂停/原状态；终态/已进入施工阶段不能被陈旧标记覆盖。

真实后台desktop/clerk/tasks.py:1098/1103只有软件任务新建且需暂缓写collab方案时才写deferred_plan，接好后_mark_deferred_plan会pop标记并正常写方案产生workflow事件。可据这条明确来源为该卡补kind=work/flow开发软件（仅原kind/flow未知时；显式类型/流程不能覆盖），沿用defaultFlowForKind；不向来源写入新字段、不编版本/工具。原历史无来源/无事件仍未知，不一律假设所有卡开发软件。

已有readJsonRecord、readSafeMetaText、CARD_ID与readCardOverviewMeta/hasWorkWorkflowEvent/defaultFlowForKind可复用，只需最薄读取。事件路径不得新增不安全读取。下面附必要实际源码上下文，禁止改测试、Python、DSH本体/保护文件、其他批代码。输出最小补丁与无额外解释。
