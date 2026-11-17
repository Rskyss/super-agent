# agy · 1-5 设置只留积木库（1-4复查过后再派）

唯一产品代码作者Gemini 4 Hydrogen。不要用工具，不改测试，只输出apply_patch；完整repo路径，新文件每行+。DSH本体与受保护文件禁止碰。不提交推送。用DSH Button/Tag/图标/变量，不手画控件。旧功能与组件不删；换下注册注释保留，已藏清单写恢复方法。

设置保持原SettingsRoot与键盘/模态/关闭机制，不复制壳。正式local-desktop.mjs强制KEYCHAIN=1，首配/换模型走自有Store与FirstUseSetup，接调度员走现有新聊天草稿与确认卡，不依赖DSH设置页。非keychain兼容分支确实依赖旧“模型”导航：不得删守卫、不得强走Host拒绝保存的钥匙串引导、不得悄悄藏坏该分支。按施工单有依赖停下记台账，该兼容分支不改、不实施限制导航，明确遗留交Claude定范围。正式应用的1-5继续完成。

公开slot可行方案：

- 我们现有settings.section id=superagent-blocks排序调到最前（例如order=-100），使冷开Cmd+,默认就是积木库。DSH shell-store无持久化，close清activeId。
- 新SettingsChrome.tsx导出registerSuperagentSettingsChrome(slots,useRestrictedSettings?)。通过settings.section.icon同id贡献DSH图标，并在正式限制模式包自有data-superagent-settings-entry标记。useRestrictedSettings默认true便于独立测试；实际入口hook从onboardingStore读取“未loaded、required或keychainEnabled”为true，已确认非keychain为false。
- CSS仅在[data-shortcut-modal='settings']:has([data-superagent-settings-entry])范围内，隐藏nav里不含此标记的按钮，用:has稳定标记，不靠文字/位置/DSH hash。非keychain没有标记时不限制原导航。
- settings.action列表id=open-document、priority -100空组件盖住“打开配置文件”（原组件和低优先级注册仍在，不删除；它不是三条流程的依赖）。不要用null盖section内容冒充导航消失，也不要关ui-settings-general/模型/模式服务。
- 不重复声明旧children，不克隆SettingsRoot；原sidebar.settings隐藏挂载与settings.launcher桥保持。若有返回disposer需绑定ui.effect。

积木库内容按06a重排，动态模型/调度员用真实metadata，不写死DeepSeek：

- 标题“积木库”+小字“基础层 · 零件”，说明“超级玩家能用的所有零件。要接新的，直接对超级玩家说。”，右上DSH Button“+ 让超级玩家帮我接一个”。接入仍走startDispatcherChat('connect','')，先关设置、开新聊天草稿，不能直接启用或安装。
- 三段h3：大脑（按量付费接口）／干活的（订阅的命令行工具）／工具（命令行工具、MCP、接口）。
- 大脑一行：真实provider名 · model名，下一行“超级玩家在用”，右侧已连接状态和“更换”（现有openAssistantSetup/后续可取消弹框）。正式限制分支若openAssistantSetup返回false，保留短错误/重试提示，不能回退点击已藏的模型导航；旧fallback只在已确认非keychain兼容分支使用。读取失败保留现有安全错误处理，不编造模型。
- 没调度员时“干活的”只写“还没有”；ready时显示真实调度员名称/模型与现有更换动作。工具没有真实清单源，06a写“还没有”，不虚构工具。
- 原“角色”、使用当前默认模型Tag、长安全说明不在图里，作为非keychain旧渲染分支保留，新正式分支不露。BlocksSettingsInjected可新增同一个useRestrictedSettings可选hook，默认true，实际注入与图标限制相同的hook；非keychain走原展示和更换fallback。复用现有加载/模型摘要/按钮handlers，不重复抄一套后台调用。

总调度将先跑3项失败测试：三组/动态模型/无额外长说明；更换沿用自有引导、接入仍先聊天；slot标记与配置文件动作覆盖。player-brand旧测试的“超级玩家”行名由总调度按06a改为“超级玩家在用”，不得让你动测试迁就实现。正式限制与兼容保留需写清台账。完整source会附在实际派工输入后。
