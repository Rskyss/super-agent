# 第三批03b结构修正：回执进入原确认轮

你agy/Gemini4Hydrogen唯一写产品源码。可直接用工具修改下列范围，不改tests/测试配置/夹具，不改Python/DSH本体/保护文件，不提交推送、不运行sync/服务、不另派人。不扫描整个仓库，只读附带上下文和这里明确的公共文件。先看03b图；未确认03/03c仍贴输入框上，已确认需求摘要+任务小条属于聊天流，后续新消息应在其后，不能跟着最新轮移动或继续钉底部。

root先红：alignment-receipt-anchor三项3/3失败（模块未有）。真实DOM也先红：任务卡小条CARD-001 insideInputDock=true/insideTurnTail=false。所有既有安全测试不能改。当前真实CARD-001来源暂缓，前一单已补元数据8/8绿，必须保留。

## 复用DSH现成槽和持久store

`conversation.chat.turnTail` 是session list，TurnTailNodeView无论closing是否null都renderSlot，位置在原动作行之前。注册新id，原input.dock TaskStrip注册整段注释保留，TaskStrip旧组件不删。不要wrapper/复制ChatFlow，不把已确认回执挪到content顶部或每次最新轮。任务自己的self普通说明/卡头不应在该流重复；普通聊天已确认需求和小条用原真实rows与openTask复用。

公开TurnTailOwnerProps.turn为TurnLocation：turn编号、start/end事件各含seq/time、status；owner.seq是closing assistant seq，不能当start seq。公开UseChat来自`@deepseek-ai/dsh-client-ui-chat/client`，已在严格session标准props，ChatSnapshot.timeline有turnOrder:number[]、turns:ReadonlyMap<number,TurnLocation>。确认点击前同步捕获当前timeline最后turn的start.seq（即便open也可）；不能等confirm远程返回后再取最新轮，也不能用DOM/最大序号猜。

用DSH公开defineStore持久UI锚，`@deepseek-ai/dsh-client-store`；工厂在apply里创建一次，ConfirmDock项和turnTail项用相同handle+严格session scope。模块不能导出已创建的handle单例。spec `{init:()=>state,persist:'superagent.confirmed-turn-anchors.v1',actions:{remember(d,project,card,anchor){...}}}`；注册store后PropsStore/BoundActions/useStore/actions均来自ui-slots公开类型。框架persist自动加sessionId，profile本地刷新保留；项目维度必须用Host.status().project（标准SessionSnapshot没有workspaceId，workspace也不等当前施工项目）。records按project/card分层，anchor记真实createdAt和startSeq，createdAt必须和现row一致防同号卡复用；同一创建指纹只记首次锚，不被后续消息挪动。只存UI定位，不存需求、Key、内容或凭据。

建卡成功后可读真实sessionCards取得该卡createdAt，记录点击前捕获的锚。当前session/key/异步切会话守卫不能退化；要保留原onCreated确保任务会话及notify刷新。保存失败/未知createdAt不能伪造；仅该客户端profile持久能力限制记台账。不改Host confirm API、不往来源.json写额外metadata，后端仍只作之前的只读聚合。

公开DSH store API和依赖：store包package.json正式导出；给本插件package devDependency workspace:*以及tsconfig.client references ../../client/store。`defineStore`仅init/persist/pure actions，没有onMount副作用；组件不能自己create生产实例。

## 旧卡唯一时间恢复与冻结函数契约

新增共享纯`src/receipt-anchor.ts`，导出`resolveReceiptTurn(createdAt:string|null|undefined, savedStartSeq:number|undefined, windows:readonly {startSeq:number;startTime:number;endTime:number|null}[]):number|null`。

- 有精确savedStartSeq时，只匹配已载入那个startSeq；没载入返回null，绝不回退到最新轮。
- 无精确锚时，首条需求时间只有整秒（flow.py timespec seconds）；将该秒作为完整[start,start+1000)不确定区间。必须整段唯一位于已结束轮endTime与下一轮startTime之间才恢复该轮。末轮没有next可以延伸到未来，但确认区间也必须整个在已结束轮后；open轮不能猜完成。边界同秒和缺少start/end不确定就null。没有时间就null。精确带毫秒字符串也可保守按整秒处理，不需要伪精度。
- 该函数须真正用于生产turnTail关联，不为测试单独造helper。使用公共timeline转windows。
- 只在当前owner.turn.start.seq等于解析结果时显示该卡回执；后续新消息和刷新保持原位置，不能每个尾部重复一份。

## 数据复用与轮询

保留原TaskStrip乱序/代际隔离/失败提示/确认事件立即刷新、真实需求MD/建卡时间/流程调度员/openTask。迁移为多个turnTail实例时不能每轮一个3秒轮询造成请求风暴：用apply内创建的session共享feed/controller（可独立新模块，ui.effect统一dispose），同会话所有tail订阅同一个快照，只启动一个poll timer，最后订阅离开停止，旧会话回包隔离。不要模块全局单例绑住不同插件reload。新feed可从旧TaskStrip提取共用读取逻辑，原兼容组件留，不许弱化安全行为。

旧dock模式FINISHED过滤/slice3保持兼容，但新的聊天历史回执不能因任务完成/放弃或第四张卡就消失。新inline路径保留已确认需求和任务条，状态随真实row变；只在原确认轮显示。任务会话本身的self不重复出普通说明。布局inline宽度跟聊天内容，用新自有类覆盖，不复用dock居中/底部margin使回执与原消息分成两列。摘要里的需求、问题建议、展开、时间、任务号/名称/状态、查看任务仍03b。保留原关闭按钮注释。

## 允许文件与验证

允许TaskStrip.tsx（提取复用/兼容）、新TaskReceipt.tsx及必要局部CSS/共享feed模块、receipt-anchor.ts、ConfirmDock.tsx（只精确捕获与成功绑定）、client/index.ts接线、types.ts必要纯类型、package.json/tsconfig.client.json/tsconfig.host.json模块清单、已藏清单。前三批其他页面/第四批任务页/第五批总览不得顺手修改。

可运行定向：`node /Users/rs/Desktop/project/dsh-base/node_modules/vitest/vitest.mjs run --config dsh-plugin/core/tests/vitest.alignment.config.mts alignment-receipt-anchor alignment-confirmations confirm-dock task-strip`。如果缺真实编译产物导致依赖错误，报告别改测试。root负责同步10门/全量/真实UI确认→再聊→刷新位置断言与03b深浅并排→独立Codex正式R1。结果准确列文件/测试/持久化边界。
