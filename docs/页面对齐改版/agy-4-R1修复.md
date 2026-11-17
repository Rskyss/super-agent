# agy 第四批 R1 修复施工单

真实 agy Gemini 4 Hydrogen独占产品源码。根先红，独立Codex复查R1六项P2真实复现。读review-4-R1.md、页面说明04a/05补充/14和六图。一次修完本单，不提前做第五批，不改任何tests/测试config/夹具、Python、DSH本体、保护文件，不sync/起服务/提交推送。允许第四批必要产品文件：TaskPageChrome.tsx/.module.css、client/index.ts(仅接plan端口)、ProgressPanel及共享progress-model、client/progress-view、DecisionDock/decision-model/ConfirmDock CSS、trajectory.ts、Host index.ts、types.ts、必要工程清单、已藏清单。旧不用的JSX/算法注释保留，别物理删除。根当前最后352全量/10门禁及12并排已齐，现不能通过，按最新源读，别回滚你原会话后续已应用的purity/CSS/状态/轨迹/空Session桥/layoutEffect补丁。

## 先红冻结的根测试（禁止你改）

- alignment-task-review-r1.spec.ts：独立Codex真实Gateway/临时文件的4红+2正对照，再加全阶段分布测试。旧轮汇报归属、规划步骤污染、200秒等你、非复查停因已全红；现有秒数/未知用量正对照保留。
- alignment-task-announcement.client.spec.tsx：实际使用的getTaskPhaseAnnouncement(payload,plan?)和formatTaskJobsLabel(label,rows)两个导出，3红。不要只写测试用导出，必须接真实组件/原jobs。
- alignment-task-decisions新增预览正常态无裸地址/链接、how只一次，先红。
- alignment-task-trajectory新增“总…其中等你”分布头/阶段标签，先红。
- alignment-task-labels把漏“了”按图校准，先红。alignment-task-view-host旧14 fixture补真实cause:'复查不通过'（避免新严格停因把未声明原因的旧fixture当复查，原路径安全/配后续开发断言保留）。这些都是根改，freeze20你不得碰。
- 日志/tmp/superagent-align-20261116/batch4-review-r1-final-red.log，四文件前次10红8绿，最后加label以真输出为准。

## 六项P2与两条细节全部修

1. **4-1 jobs缺“· 预览”**：仍镜像原ui-jobs组件/inject/locale，不能自己画job列表/订阅另一份假状态。原公开type-only JobListActionProps从@deepseek-ai/dsh-client-ui-jobs/client导出，原inject绑定useJobs；同session真实rows在useJobs(s=>s.rows[sessionId])。原t('count.idle.one'或'count.idle.other',{count})用于可见count和aria。可用稳定组件薄包装原entry.component，沿原useJobs读本会话rows（kind='preview'且status running/stopping才有预览），仅代理这两个t key的返回文字补“ · 预览”，其它key原样委托；不改计数/不关watchRows/observe/kill。纯formatter formatTaskJobsLabel(label,rows)供此实际代理调用，未知/结束/其他job不冒充预览。type-only跨feature契约允许，禁止运行时导入别的feature组件。

2. **4-2固定“调度员写方案”不得用后来的disp当前状态**：Host已安全读rawEvents，按最新真实写方案→方案/方案失败配对恢复专属planning数据（actor/model/detail/elapsed）；或新增显式planning DTO给共享progress-model使用。后续复查失败/汇报/派活不能写入第1步。源码desktop/view.py旧disp语义是当前汇总，只可在真正规划阶段有限fallback；缺历史来源时省略/用不捏事实的状态文字，不能把复查失败塞绿色历史步骤。新测试仅禁止污染，真实CARD001有写方案23:50:52→方案23:50:52和Codex/model可核对。不要破坏isRunningNow唯一转圈守卫/7步/旧phase兼容。

3. **4-3中间独立阶段说明**：真实TaskHandoffTimeline（或同一已有view poll）在时间线之后、原Session聊天内容之前渲染说明块，不能只在贴底决定卡写标题。getTaskPhaseAnnouncement(payload,plan?)实际用于此块：planning精确“调度员正在写方案，好了我叫你。”；plan_ready无问题“调度员把方案写好了，你批了才开工。”；真实有1问则精确“调度员把方案写好了，有 1 个问题要你答，我标了建议。你批了才开工。”，不能虚构问题数。可复用已有公开纯parsePlanMarkdown解析问号章节，现TaskPageChromePorts无plan，可加plan并由client/index在已有端口旁传入，仅plan_ready才读取且复用原poll/序号守卫，不另造后台新轮询。停下复查真实3条说明含“复查 3 轮都没通过，我先停下来了”，可接“，详情在下面。”；不凭样本捏造同一个问题反复。非复查停下用真实phase.detail/reason，不谎称复查。其他阶段无需额外图外说明。切轨迹时与timeline一起隐藏、普通不出现，不重建原Session/草稿。

4. **4-4旧汇报串轮**：readLatestAcceptReport已有本轮边界，但fallback汇报.md无归属校验。必须拒绝边界之前旧条；本轮显式开发回复带“汇报：”仍优先真实。安全降级：存在新轮边界但不能证明汇报.md条属于本轮就返回空，UI明确没读到，不猜完成；无任何轮次证据的旧兼容卡可保留原fallback。不要用文件mtime假证明旧条内容属于新轮，不读不安全路径。新正对照必须仍取本轮明确中文汇报。

5. **4-4非复查停因**：不能有任意旧复查（甚至通过）就改成“复查N轮都没通过”。按本次最后真实转人工/停下cause或phase.actions中的accept_issues等强证据判当前是否复查失败；其他停因不展示旧复查表、不隐藏真实reason。真实14 cause:'复查不通过'完整保留3轮配后续开发记录；末轮仍无修改记录。不同停因要按真实原因给标题/中间说明、不能硬三轮。保留backend actions约束，不造accept按钮。blocked头部和badge补漏字“停下了 · 需要你决定”。

6. **4-5阶段分布+等你时间**：保留distribution契约actor/seconds兼容，不再只按角色累计。有step的真实回复映射写测试/写代码(含返工累计)/复查；有明确写方案开始→方案结束成对时间，计写方案；方案结束→批准成对时间计等你批；程序全量phase开始→结果/停成对时间计跑测试。只用合法非负明确seconds或成对ISO事件时间，不能对缺配对猜Date.now，不把需求创建到开跑整个时间塞等你；重复开始/修订/坏时间要保守，不跨轮误配/双计数。仅旧事件无step/阶段证据的显式src+seconds保持旧actor fallback（独立正对照开发员60）。新测试六类别58/200/190/400/142/181。UI分布头按图“用时分布（总…，其中等你…）”，基于真实已记录累计阶段秒数，未知不装完整全历史；明确不要用“各角色累计用时”替代阶段。并行全量/复查可说明为已记录阶段累计，别冒充实际wallclock；尽量保持图的紧凑结构，不加图外大段解释。未知用量—，不把usage未知单位标字。

7. **05去重裸URL**：成功打开的正常主卡按图一行预览说明，复用真实openPreview(url)行为和job；不把裸URL塞actionNote，也不在按钮下再显示备用链接。同一个how只显示一次。旧JSX/文案注释保留，可保留打开失败的必要错误恢复，不把成功态当失败展示。根mock真实URL传到openPreview仍必须一次。

## 不碰的依赖

D1：施工中revise被Python拒；无确定pending HTTP，旧/send要再调模型不能冒充。保持真实失败提示/不清草稿，不为修图停工、不改Python/卡文件、不伪造成功。D2：Python无历史diff producer，保持仅明确安全event.diff显示，不能拿当前git diff冒充历史。两项继续交Claude裁定。

做完至少跑上列新测试和所有第四批/原trajectory/progress/decisions目标，返回实际修改文件、结果、任何真实边界。不能改测试使绿；遇到旧断言冲突向根报告，根决定是否真实规格校准。根负责最终sync/全量/实窗重拍12图与正式R2。本轮每项已R1一次，还最多2次，不超3轮。
