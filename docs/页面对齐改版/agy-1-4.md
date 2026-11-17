# agy · 1-4 首页（前项独立复查过后再派）

唯一产品实现作者Gemini 4 Hydrogen。不要调用工具，不改测试，只返回apply_patch补丁（新文件每行有+，完整repo路径）。不提交推送。DSH本体、四类受保护文件禁止碰。DSH现成输入框/附件/菜单必须复用，不复制编辑器，不用DOM mutation/React树遍历/私有renderer。

按01：大字前不出任何图标，仍占conversation.hero.brand.mark防止鲸鱼回退；Branding.tsx旧SuperagentHeroBrandMark组件保留、旧注册注释保留，改为注册空组件。左栏碗原样。隐藏首页工作区/模式选择，后台模式服务不关。默认聊天/项目逻辑原样。提示“跟超级玩家说你想做什么…”，下方只有原DSH+与发送。不要额外内容。

已核实公共机制：

1. SlotCore.register禁止重复声明已存在children；ctx.slots.renderSlot只允许root。不要镜像旧children名字，不要薄克隆后调用旧child名字。
2. 以公开entries捕获原conversation.composer.bar StoredEntry，排除自己的registrant/component，保留原component/inject/locale及全套PropsRuntime/ownerprops。它没有store。
3. 新覆盖bar仅声明两个全新子槽别名（如superagent.home.attachments single/session-maybe，superagent.home.overlay list/session）。新wrapper内部渲染原InputBar，给它适配renderSlot：旧conversation.input.attachments转新attachments别名，旧conversation.input.overlay转新overlay别名，其余旧UI槽返回null。owner/options照转。保留输入编辑器/keyboard/attachments/stop/retry/onChange等全部原props。只在variant=hero且非blocked/disabled时覆盖placeholder，阻塞/失去项目不冒充可用。
4. 两个alias的entry复制公开原attachments和overlay的component/inject/locale/options（只改name）；overlay只镜像slash-menu、command-popup允许名单。已核实三个entry都没有children，不需要递归。按各自原优先级/order/id。附件与弹层不可丢。不要复制原源码。
5. 监听原bar/attachments/overlay三处公开subscribe；只有原entry identity改变才重建相应alias。避免自注册触发无限循环，原source卸载时拆镜像，等待新source；dispose清理订阅与注册。新bar声明自己的alias一次，旧source变化不重复声明alias。原bar尚未出现要等待subscribe，而不是永远不注册。用公开接口的少量隔离类型适配，核心接口尽量保留ComposerBarProps/Injected类型。
6. 根hero.frame HOC用自有class包住原children（每阶段都保留同树，不重造输入框），hero=true时局部CSS隐藏[class*='heroWorkspaceRow']（WorkspaceChip是宿主直接画，盖workspace slot不足），隐藏[data-composer-dock]，不影响非hero布局。模式slot可额外priority -100空占位。保留错误与编辑器本身。新conversation.header已去掉原corner，因此不应再露右上面板按钮。

新文件HomeChrome.tsx导出registerSuperagentHomeSlots(slots)便于测试，相关CSS。index.ts仅导入并调用它，仍为.ts不可JSX。Branding.tsx按上述留代码改注册。已藏清单加本项变更与明确恢复方法。

总调度的4个测试已先红：hero-brand两项新规格、alignment-home两项（原编辑器/发送/输入回调、原附件/菜单与inject/locale保留）。不得改测试。不许用空壳新输入框让测试过；测试的Original只是原DSH组件的替身，真实运行必须仍是捕获的DSH InputBar。
