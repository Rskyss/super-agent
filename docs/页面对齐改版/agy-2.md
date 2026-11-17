# agy · 第二批 00a–00e 首次配置整页

唯一产品代码作者Gemini 4 Hydrogen。无工具调用，不改测试，不改Python后台/DSH本体/保护文件，不提交推送。只交一份完整apply_patch（每个新文件每行+，repo相对路径），旧组件和注册不删。仅第二批源及已藏清单。

原FirstUseSetup的防抖/代际隔离/不代选/真实试聊后保存/连点守卫、OnboardingUiStore全复用。FirstUseSetup新增可选presentation:'page'|'dialog'，默认dialog保留既有换模型弹框与旧测试；page才按00a–00e新布局。原OnboardingOverlay组件保留；index旧首次shell.overlay注册整段注释并注明恢复方法；新注册只渲染canCancel更换弹框（可薄wrapper复用原Overlay）。不能给#root加inert，不修改onboarding-root-lock测试。

新增OnboardingPage.tsx导出OnboardingPage（接现有OnboardingOverlayInjected）、installOnboardingPageNavigation(store,layout,panelId)。新keyed main panel注册在ui层，注入现有onboardingOverlayInjected。安装导航守卫ui.effect绑定清理：未loaded或required时选setup，layout.panelInfo订阅发现跳到其他main就回setup，需相等检查避免递归；标记完成后若仍在setup回null，不抢用户后来正常导航；卸载清理两订阅。公开ILayout.panelInfo.getSnapshot().activePanelId/subscribe、selectPanel(MainPanelId|null)，不要私有接口。onOnboardingDone原startChat+模型同步保持。OnboardingPage仅首次锁定时显示，不挂Modal；可取消更换仍走原Overlay，不能被main守卫拦。

页面结构：中间主体flex列、可滚动配置内容居中、底部灰composer独立。标题“配置一个大模型来做你的超级玩家吧”，一句“它负责和你对话，也负责帮你把各种工具接进来。这一步设好后一般不用再改。”。一个圆角框内：服务商一排DSH Button（允许窄屏换行）：DeepSeek、阿里百炼、腾讯云 TokenHub、腾讯混元、其他（OpenAI 兼容）；API Key标签右侧“去哪里拿？”链接；DSH Input password默认聚焦，placeholder“粘贴你的 API Key，粘贴后自动验证”；下面短句“只存在这台电脑的钥匙串里，不会上传。”。page不露默认接口地址、安全遮罩预览、旧Key搬家按钮/长隐私说明（旧功能代码保留dialog分支并登记隐藏）；不提前露确认。

一步一步：Key自动验证进行中只短状态；成功展开“选一个模型”可滚动单选列表，默认不选，展开后才露确认按钮（未选灰）；错误仅Key框下方一行短安全原因，保留去哪里拿/换一家服务商链接，不能模型/确认一起露。选其他多接口地址Input+一行提示，仍走原URL验证与过期保护；确认真回复保存才离开到首页，不出成功卡。底部保留图上灰输入框（DSH Input、DSH +/发送Button及图标，disabled，aria-label/placeholder精确“接好大模型后就能跟超级玩家说话…”），只作不能发送的首配状态。不要改Home原InputBar。左栏已有Store锁与空历史逻辑复用。provider/model选择用DSH控件，颜色/圆角/字体用现有--dsw-*，不引--color自画主题。尺寸间距按DSH，结构按图。

去哪里拿：已核实官方帮助地址：DeepSeek https://platform.deepseek.com/api_keys；百炼 https://help.aliyun.com/zh/model-studio/get-api-key/；TokenHub https://cloud.tencent.com/document/product/1823/130090；混元 https://cloud.tencent.com/document/product/1729/111007 。其他服务商未知，不编造API Key控制台：有合法http(s)接口时链接其origin，title说明到该服务商网站获取；未填地址时链接可置禁用文案仍同名。新标签浏览器打开，rel=noreferrer，绝不把Key带URL。后台provider预置不用改，只page对other标签补括号。

4项root冻结测试已先红：page先只Key/服务商→成功模型/确认→点选后完成；错误一行无确认且换服务商清空；主页面无dialog/根锁、底部灰且无跳过；导航锁定直至完成与清理。所有既有测试继续过，特别root-lock（原Overlay组件仍保留）。不允许改测试来迁就实现。新函数注册/依赖类型严格，所有订阅/ui.effect清理、异步操作防串包。
