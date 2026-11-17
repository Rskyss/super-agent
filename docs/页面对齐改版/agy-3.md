# agy · 第三批 3-1/3-2/3-3

你唯一编写产品代码。可用本地工具直接修改本施工仓库内第三批产品源文件；不改测试、不改Python后台/DSH本体/保护文件，不提交推送，不另派执行员。只准本单列出的源文件和已藏清单；不能改任何tests。结束返回准确修改文件与验证结果，若工具不可用才退回完整apply_patch。后续第4/5批不要提前做。旧不需要的注册/按钮注释保留，组件代码留，清单写恢复方式。所有色彩必须本地实际--dsw-alias-*，不能发明变量+硬编码浅色fallback；Button/Menu/Input/Tag/图标用DSH。root会先红7项测试，实际源码会附。

## 3-1 确认卡 03/03c/19

复用ConfirmDock的轮询、seq/会话隔离、处理幂等、confirm/dismiss与输入草稿/附件保护，不另造消息系统。卡贴在原conversation.input.dock；金色细边框用实际DSH warn token，别硬编码金色。标题后紧跟Tag“干活 · 开发软件”、DSH Button“ 不对，改成…”；右上“等你确认”，去掉图上没有的收起按钮（旧JSX注释留）。下面按序：要做什么；验收标准（①②③每条一行，有aria-label验收标准的ol/li，容许现有换行/编号文本）；不做什么；？要你拍板的（各问题独立一行、建议灰）；用哪条流程；没调度员再加！调度员短说明。流程无版本不写第4版。底部仅“确认，建任务”“我要改”；原“先不做”JSX注释保留并记清单。确认成功后只靠TaskStrip新的真实确认摘要，不再露6秒长说明卡；失败仍保留安全短错误。

“我要改”成功dismiss后只收卡并调用新增可选focusInput，不改草稿/附件，不自动发送。实际注入用公开ui.sessions.scope(sessionId)取得actx，再ui.conversation.input.for(actx).focus()，不要找DSH DOM或发明InputActions.focus。改类型仍走原安全撤卡后setDraft/submit逻辑；正在输入/有附件时不能覆盖。菜单用DSH Menu或MenuSurface/Button，19结构：只是聊聊（不建任务）；干活（用现成流程做一件事，当前✓）；接零件（往积木库里加东西）分组下命令行工具/大模型（按量接口）/调度员（订阅命令行）/MCP；造能力（做一张能力卡）；搭流程（把能力卡串成流程）；底部“选了别的，超级玩家按新类型重写这张卡”。菜单是分类意图发给现有对话，不能直接安装/启用新功能或伪造未来能力卡已建；造能力/搭流程等侧栏入口本期仍藏。保留TYPE_CHANGES既有chat/connect语义兼容。当前项不撤卡。Escape/点外面只关菜单回触发器，不撤卡；宽屏卡右侧不挡内容，空间不够用DSH窄屏策略。

## 流程与03b真实数据

当前无workflow API、无版本源；开发流程实际是固定七步，前后含写方案/你批方案，执行段写测试→写代码→跑全部测试→复查→你验收。新增Host只读workflowInfo Remote及纯Host workflow-info.ts导出readCurrentWorkFlow(repoRoot)，name开发软件，steps从共享progress-model的STEP_LABELS按test/dev/check/rev/accept取（可只导出该现有常量，不改行为）。maxReviewRounds读真实repoRoot/collab/dispatch.py的字面MAX_REVIEW_RETURNS+1（当前2+1），读不到则null省略，不编造。无版本返回null/不写；不改Python。注入ConfirmDock新增可选workflow()，生产真实取Host；旧helper无该函数时不可编造版本/次数。不要将Host fs模块引入浏览器，接口类型在types.ts。

CardRow新增可选createdAt与requirement（do/notDo/doneWhen/questions/title）。在Host现有readCardOverviewMeta已安全读取的reqMd/流转里补真实元数据：建卡时间取首条有效“需求”事件timeStr，不能用updated；需求按##要做什么/怎么算做完（兼容验收标准）/不做什么等原Markdown章节完整读。用现有安全readSafeMetaText，不新写任务文件、不改后台。刷新后仍能恢复确认摘要，不靠页面内存。

## 3-2 TaskStrip 03b

复用原sessionCards/cardOfSession轮询、乱序失败处理、notify刷新、开任务事件。Row保持原5字段必需，新增CardRow元数据可选以兼容既有接口；不要强制旧测试夹具提供state/actions等无关字段。每个活动非self条目上方：一行“需求 · 22 号 · 已确认”+真实do摘要+“展开”，展开看完整已确认需求。下方任务小条：22号+标题+状态；第二行真实流程/可取到的调度员/真实几点建卡；右边只有“查看任务”，原×“收起这条”JSX注释保留。数据未知就省略相应段，不用updated冒充，不写死Codex。已结束/不做的仍不占任务小条，保持已有规则。任务自己的self普通说明框图上没有，旧JSX注释保留；查询失败可留已有短安全提示。不要影响同任务会话刚建的其他任务条。

## 3-3 消息底部

新增ChatChrome.tsx导出registerSuperagentChatChrome(slots)并实际index接线/ui.effect清理，用conversation.chat.node keyed turn-process priority -100空覆盖。原TurnProcessNodeView只在closed时画已完成用时，错误有独立turn-error不要覆盖，后台推理/工具/统计服务保留。原消息footer的分支/用量无single slot：在HomeChrome自有frame局部CSS，按稳定data-chat-flow-kind/data-turn-tail/data-actions-reveal和svg[data-icon]精确隐藏branch-outline-regular/database-outline-regular/消息时间，复制copy-outline/check-outline仅消息hover或focus-within出现，不全局藏所有按钮、不靠中文文本/hash。图上没有每条复制常驻、分享、用量、完成用时；分享已由第一批header盖住。输入框native[data-composer-dock]统计从仅hero扩展为所有对话隐藏（HomeChrome自己的frame作用域），但不隐藏我们自己的conversation.input.dock卡，不禁token计量服务。第四批再加任务专用footer。

7项冻结测试：03标签/分行/问题/流程与按钮；我要改不动草稿附件并focus；19菜单Escape；03b真实确认摘要展开/时间/查看任务；3-3slot仅turn-process不伤turn-error；Host真实MD/首需求time；流程上限源于后台常量且不编版本。保留现有安全测试。不得改测试。


执行补充：先用可用图像工具打开docs/借鉴DSH/页面对齐/03-*.png、03b-*.png、03c-*.png、19-*.png，mockup.html可逐场景读精确文字。金色边框只加proposal独有类，不能把共用card类都改金色影响第四批/调度员卡。旧change的margin-left:auto必须在新header取消，使它紧跟类型标签而非推到右侧。DSH Menu真实支持side="right"，可portal，内置Escape/外点/焦点返回与碰撞；优先直接用完整Menu。

你可运行本仓库定向测试命令：node /Users/rs/Desktop/project/dsh-base/node_modules/vitest/vitest.mjs run --config dsh-plugin/core/tests/vitest.alignment.config.mts alignment-confirmations confirm-dock task-strip。测试冻结，失败不许改断言或夹具。不要运行sync.sh或改外部DSH、不要启动服务；编译/全量/真实窗口由总调度在你结束后统一做。严格禁止修改FirstUseSetup/Onboarding等已过第二批源码。
