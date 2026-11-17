# agy · 1-3 普通/任务对话标题栏

你是唯一实现作者，模型 Gemini 4 Hydrogen。不要使用任何工具，不要改测试。只输出可由apply_patch应用的补丁（Begin Patch/End Patch），不执行命令。源码已附在输入末尾。不提交、不推送。

只做本项，禁止改DSH本体、sync.sh、apply_desktop_brand.py、patches、local-desktop.mjs。旧组件/注册不能删；换下来的注册要注释并写中文恢复说明，记已藏清单。使用DSH Button、Modal和--dsw-*变量，不手画控件。

新建 src/client/ConversationHeader.tsx，导出纯展示组件 SuperagentConversationHeader，props为 title:string, blank:boolean, task:{id:string,title:string}|null, onTrajectory:()=>void。普通非空对话只显示 title + 小字“超级玩家”，没有标签/更多/面板。普通blank渲染空。task即便blank也显示“22 号：标题”+“超级玩家 · 任务”+ DSH Button“任务轨迹”，点击回调（第一批临时入口，第四批换对话/轨迹）。

在client/index.ts以priority:-100占conversation.header。使用session-maybe的useSession取得标题和blank；通过现有cardOfSession(sessionId)获取真正绑定的卡，不以会话标题猜卡。异步切会话时不能闪出旧卡/旧轨迹，卸载/返回后不提交过期结果。无会话时不查remote。任务标题在空会话也必须显示。

轨迹临时入口使用DSH Modal包住现有TaskTrajectory，传当前sessionId和现有trajectory/cardAttachment端口；关闭/Escape/返回焦点使用DSH机制。TaskTrajectory目前实际只用sessionId和TaskTrajectoryInjected，可将其Props收窄为 Pick<PropsRuntime<'conversation.view'>,'sessionId'> & TaskTrajectoryInjected，让原conversation.view注册继续可用。不要重写轨迹、不要去改DSH持久化内部选择。Modal不常驻打开。入口位置记台账（标题右侧“任务轨迹”，仅任务有）。第四批正式双标签时再收口。

index.ts是.ts，禁止JSX，使用createElement。register children要按所用公开slot声明；这里标题自己渲染，不渲染旧session.header，避免旧菜单/标签露出。DSH外框的shell.leading窄屏入口保持现状。对错误不编造卡标题或编号。只编辑本组件/CSS、必要index.ts接线、TaskTrajectory的Props收窄和已藏清单。

总调度已写2个失败测试alignment-header.client.spec.tsx；你不许改测试或让它忽略内容。
