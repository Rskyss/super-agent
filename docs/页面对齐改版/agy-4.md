# agy · 第四批任务页（施工前派工稿）

仅在总调度明确交付第四批红测试后开工。本单实现4-1至4-5，05j/05t必须复用DSH现成后台任务和原任务轨迹。你是唯一产品源码施工员（Gemini 4 Hydrogen），可用工具直接修改本仓库指定产品文件；不改任何测试、不改Python、不改DSH本体/受保护文件、不提交推送、不启动服务、不运行sync。旧不用的注册/按钮注释保留，不删实现，并登记已藏清单。先打开04、04a、05、14、05j、05t六张草图和mockup精确文字。

## 页面外框与可用公共接口

- 普通对话保持已过前三批；任务必须真实取消hero，不能只藏大字让输入框仍居中。`useProjection('agentPreset')`公开值直接是string/null/undefined，任务值superagent-task；类型扩展入口`@deepseek-ai/dsh-agent-preset-registry/types`（仅type import），不是Host根模块。
- `main.conversation`原StoredEntry公开component可保留；原组件没有/client组件导出。公开类型ConversationSlotProps来自ui-conversation/client，含renderFactorySlot。原main入口无store/inject/locale，只有header child；新wrapper使用唯一header alias，不能重声明DSH旧child。
- 原caller给conversation.content factory传variant/phase/hero与slots.widthControls。任务已就绪时传hero:false/phase:active（settling保留），views替换为时间线+原conversation.session；其余input/options/widthControls全部保留，不能重新注册同名factory。
- 注意保留原main组件时，外层data-phase仍可能hero，factory wrapper不能改变；DSH CSS据此外层居中scrollBody且不启用sticky输入。必须用自有任务marker局部恢复active几何，或用公开类型写极薄main caller壳正确掌握phase。不得导入DSH私有源码/复制ChatFlow/操作DOM改属性。
- 原conversation.session.header入口共享conversationStore，公开ConversationSessionHeaderSlotProps；新唯一session别名复制原store/inject/locale，useStore读取view，调用原inject的selectView（内部activateView+setView）；actions没有selectView。新header下方DSH标签“对话 / 轨迹”，真实控制原session view，不另造Modal；第一批临时“任务轨迹”按钮和Modal路径注释保留。
- 原ui-jobs header.actions entry id job-list、order20、locale job，无store/children。复制component/inject/locale到新session别名，标准sessionId/useJobs/t由框架给，watchRows/observe/killJob原inject。不能自己重画后台任务列表。任务header主行编号号：标题、灰字调度员·真实状态、右边原jobs控件；普通header不露任务tab。

## 固定右栏

根rightbar自己没有currentSessionId；新session-maybe子别名由框架给当前sessionId，根usePanelInfo过滤非聊天main。复用DSH外框/拖宽，ui.layout.openRightbar/closeRightbar几何服务；不要关闭ui-sidebar-right。原ProgressTab注册与旧openProgress自动tab逻辑注释保留。右栏任务才显示，普通对话不显示，切卡/切聊天/切总览必须正确收起且旧回包不闪卡。

复用ProgressPanel现有轮询/七步/真实actor-model/elapsed/rawdetail：顶“进度 · 第 N 步 / 共 7 步”右真实总用时或等待状态Tag；每步indicator+标题，第二行工具·模型·强度·用时（无源省略）；第三行真实大白话最多3行；有原文右上“详情 ›”，展开再原文。去掉CARD头、重复卡名、每2秒刷新、DSH任务进度×+标签/三个面板按钮。底部改文件数/守规矩数没有可靠源就不露。

Gateway.view目前算isRunningNow但未返回；带入payload和parseProgressView，只有真实运行才active/spin，暂停/等待/blocked不得强补active。实际运行中的事件间隙仍有且仅有合理当前一步。原未提供flag的历史输入按保守不转，真实产品总传flag。不要把plan_waiting字样当真运行。

## 中间与footer

conversation.content的caller-selected slots.views是session scope；原views仅renderSlot conversation.session，新views在其前显示灰关键进展时间线，数据取真实view.handoffs（seq/kind/time/from/to/summary/tone/has_text，time仅HH:mm:ss）。详情仍走原TaskTrajectory。轨迹视图不重复叠时间线，可原轨迹根自有marker控制，不通过hash或文本判断。

输入下方footer走新session子alias，沿用HomeChrome原bar/附件/草稿/输入：左“调度员 Codex · 订阅额度”（真实ready身份才名称），右“超级玩家 N 字”。公开useChat只有SessionStandardProps，来自ui-chat/client的ChatSnapshot.legacy.nodes中kind assistant.blocks的kind text.text，加partial.blocks文字；不计用户/工具/推理/usage，不叠新旧两路径。范围仅已加载内容，不冒充全历史总计，记台账。数值不从tokens换算、不编钱。原native data-composer-dock继续隐藏，自己的footer不要被一起藏。

## 输入框上方三类决定卡

复用DecisionDock/decision-model/TaskDockBridge所有poll代际、busy、stale、draft/附件保护、preview安全校验、action/revision流程，仅改呈现和必要真实元数据。不能为了折叠丢主要按钮。

- 04a：标题“方案 · 第 N 版”（实际plan.version），右等你批；按序要做什么/分几步/做完你会看到/不做什么/只动这些地方/怎么算做好/问题带建议；按钮只批准，开工/我要改。原先不做注释留。范围/测试命令用既有大白话与详情，不在主卡堆命令路径。
- 05：标题“做好了，请你验收”，右“等你 · 用时”（真实workElapsed）。真实一句做了什么，N条验收标准全部✓、追加改动摘要+展开；默认只收详细标准/记录，主要三按钮始终可见：“打开预览窗口 / 没问题，放进项目 / 继续改”。点展开才标准与changeNotes，默认没有原文技术区。原先不做等图上无按钮注释留。实际预览已开时提示可来自真实openedPreviewUrl；不预先写已开。
- 14：真实停因标题与需要你Tag；复查轮次表列“轮次 / 复查发现 / 写代码改了什么”，内容取已有真实review/flow/报告字段，不编造轮次/建议。四按钮按顺序接受剩下的问题，去验收/按我说的改/再跑一次/先不做；非复查满轮停因仍不能露accept_issues。保持原服务能力/权限限制，历史无数据省略空行并记限制。
- 中途改要求确认条沿用现有revision卡动作“确认改 / 算了”，仅对照04整理外观；不能绕确认直接改需求。

当前后台accept.result可能仍是方案预期：Host Gateway.view优先从安全读取的真实开发报告取最新一句作为accept.result（不要改Python）。实际dispatch.py保存规则为成功开发回复中最后一个“汇报：”后第一行、最多200字，追加为“- YYYY-MM-DD HH:mm 文本”到汇报.md。可优先本轮最新成功开发回复按同格式提取；否则取汇报.md最后一条有效记录。没有真实报告不得自己生成完成文案。测试会用旧方案预期+新汇报检验。汇报仅执行者陈述，不等于验收已过。

14表格准确源：现有handoffs/TrajectoryRow已丢reply_to/round/ok/text/say/issues，不能从summary反推。Host从流转.jsonl聚合新增stopReviews等typed payload；按最后“追加要求”之后真实复查员回复顺序编号，不能拿event.round当复查序号（实际3次复查round都可能1）。每次复查发现配其后、下一次复查/停下/新要求前的开发员回复，末轮没有后续就显示无修改记录，不能编“没改好”。报告路径读事件text，别猜文件名。转人工事件的say/phase.detail可取真实建议；没有独立advice时不得生成建议。

安全读取：readSafeMetaText只拼文件名，不是任意事件报告路径安全器；读取流转事件text时必须复用trajectory.ts的readSafeCardFile，沿原readCardTrajectory确定真实卡目录（拒越界/软链接，512KiB上限）。静态汇报文件也优先这套安全读；不要引入事件路径穿越或读错项目。缺报告保留“无记录”，不能解释为无问题。

## 范围与交付

允许产品：client/index.ts、ConversationHeader.tsx/module.css、HomeChrome.tsx/module.css、新TaskPageChrome.tsx/module.css（必要只读辅助）、ProgressPanel.tsx/module.css、ProgressTab.tsx（旧组件留）、DecisionDock.tsx、decision-model.ts、TaskDockBridge/TaskTrajectory及其样式（只必要接线）、progress-model.ts、types.ts、Host index.ts、必要tsconfig产品模块清单与package声明公共类型依赖、已藏清单。前两批首配/设置/侧栏不得顺手重构；第五批TasksProbe不提前动。任何额外产品文件先在结果说清必要性，禁止碰tests。

总调度提供冻结红测试后再执行。完成返回实际改动文件、实现路径、定向测试结果、未知数据与公共接口限制；总调度独立负责全量/十道编译/真实窗口/并排图/未施工Codex复查。你可读测试，不改测试、测试配置或夹具。

## 根先红测试的最小呈现契约（施工前冻结）

- `TaskPageChrome.tsx` 导出 `taskConversationContentInput(input,isTask)`：保留输入对象不可变；任务将hero:false、hero阶段变active、settling保留；非任务原值语义不变。并将它真正接入factory调用，不能只有测试用导出。
- 同文件导出无业务副作用的 `TaskPageHeader({task,dispatcherLabel,view,selectView,jobs})`。title来自真实CardRow；原views默认null/undefined为对话，轨迹key为`superagent-trajectory`；jobs为原DSH widget插槽。产品接线必须使用此呈现层。
- 同文件导出 `countAssistantTextCharacters(snapshot)`，按`legacy.nodes/partial`仅助手text块计Unicode码点，图上显示实际计数。snapshot为空/partial:null返回0，不计用户/推理/工具。
- `Gateway.view` 返回 `isRunningNow:boolean` 与 `stopReviews:readonly {round:number;found:string;changed:string}[]`、`stopAdvice:string`。三轮事件round都为1也按实际回复顺序编号；报告缺失用“无记录”，轮后未修改用“无修改记录”。未知stop数据不造行。
- 四个冻结测试文件分别为 `alignment-task-page.client.spec.tsx`、`alignment-task-progress.client.spec.tsx`、`alignment-task-decisions.client.spec.tsx`、`alignment-task-view-host.spec.ts`，共12测试。旧测试中因图变更而需要改“打开预览”→“打开预览窗口”、默认标准折叠、方案不出多余收起等，由根Codex先改并冻结；agy不能修改任何测试去迁就实现。
- 三批已新增CardRow.dispatcher，任务身份优先这张卡的真实flow actor；不拿全局TTL失效缓存改写历史卡身份。

公开接口只读复核（研究Codex，本批不施工）：原main StoredEntry ui-conversation/src/client/apply.ts:337，原session header同文件:442/457；原ui-jobs entry ui-jobs/src/client/index.ts:36。原ConversationRoot.module.css:509/636的active/hero几何要在自有marker下处理[data-conversation-scroll]、[data-composer-seat]和viewArea；普通对话不受影响。RightbarOwnerProps来自ui-layout/client，width/viewportWidth/canShow原值使用；root层usePanelInfo.activePanelId===null才是聊天面板，新session-maybe alias真实session/card匹配后打开，离开即关闭。原index.ts:647 openProgress自动openTab也要和旧ProgressTab注册一起注释，避免旧tab影响几何。原header和jobs的inject必须经renderer框架转换，不能手动spread原inject返回值冒充标准props。

原预览到DSH jobs的数据桥已存在：Host index.ts 已有 JobRegistry/attachController('superagent-preview')、registerPreviewJob 和 preview-jobs.ts（输出轮询/停止/合并清理）。本批只把原 ui-jobs 头部控件接回任务header，不重写原预览桥。真实05j必须启动本地预览后看到真实jobs、真实输出与停止能力；不能造“1个后台任务”文字。

任务输入框提示逐状态照原mockup：04a“对方案有意见直接说，比如‘挨个测就行’…”（原图中文双引号）；04“问进度、插话改要求，都可以在这里说…”；05/05j“接着聊不会把验收卡顶走；哪里不对直接说，可以贴截图…”。第三批会先补共享的真实上下文提示hook，本批复用该hook扩展任务self.phase映射，不再统一用首页提示。

## 本次正式派工（11-17 第三批完成后）

根已把第四批4个测试文件共12项放入仓库并实际运行：11 failed / 1 passed（现有真实运行交接空隙用例原已通过），日志 `/tmp/superagent-align-20261116/batch4-red.log`。旧3测试文件已由根按图改名/默认折叠/去方案多余按钮及显式运行标记；所有测试一起冻结，禁止agy改。施工前基线69文件326/326，十道10/10；预计至少338项，不以测试个数替代实际输出。

现在开始直接实现，不等额外批准。只改允许的产品源码，允许必要新只读helper及编译产品文件清单；测试、Python、DSH本体、受保护文件一律不改。第三批ConfirmDock/TaskReceipt/锚恢复相关已收尾，除HomeChrome任务占位hook扩展外不要顺手改第三批。不要改TasksProbe（第五批）、首配/设置/侧栏。不能自己sync/起服务/提交推送。

可直接运行根配置的指定tests；你本地未sync时外部引用缺失不能靠改测试解决。完整sync/全量/真实窗口由根处理。请集中读本单、6图/mockup、相关现有源和新测试、公用DSH导出/StoredEntry，不全仓扫几百份旧文档。交付实际文件、真实测试输出、未知/边界，勿声称未跑的检查通过。

保持包装组件与slot component身份稳定，任务轮询/切状态不能重建原编辑器、清草稿/附件或打断输入。CSS不用hash类名、中文text选择；避免CSS嵌套:has（浏览器会丢掉整组规则），第三批已排过此坑。图上没有的工具统计/按钮/hero不能靠模糊遮罩处理。
