# 第三批真实窗口自查退回（尚未正式独立复查）

你是唯一产品施工员agy/Gemini4Hydrogen。这次不要调用任何工具、不要扫描仓库、不要直接写文件、不要运行测试。下面附两个实际源码及现成DSH Menu定位类型，只返回可机械应用的apply_patch，目标仅ConfirmDock.tsx、ConfirmDock.module.css、HomeChrome.module.css。不能改测试、DSH本体/保护文件或其他批页面，不能any/强转。

root独立最终插件305/305、10/10构建通过，但真实浏览器1440×900失败断言保存在confirmations-browser-red.json：card right1227.7，menu left902.7，菜单覆盖卡（必须右侧不挡内容）；消息时间可见2个（必须0）。正式复查尚未开始。

根因已读真实DOM确认：HomeChrome .frame仅包composer子树，不是消息父级。data-conversation-content稳定根同时包含消息与我们.frame。请把第三批新增消息底部隐藏/复制hover规则的作用域改到这个稳定content根，并用:has(.frame)限定我们的界面。原.hero隐藏workspace与.frame隐藏native composer dock仍保留。不能只去掉作用域变全局，也不能靠DSH哈希类名。data-clock start实际直接span时间+复制按钮；end为复制+assistant-actions槽+分支按钮+span(endInfo内用量和时间)。按稳定属性/图标精确隐藏时间、分支、用量；复制仅当前消息hover/focus时显示，普通键盘复制可达、turn-error保留。必要的空footer高度一并收敛。

19菜单继续用DSH Menu原生portal/collision/Escape/焦点回归，不另写定位算法。现在菜单anchor在全宽card右边，右侧只剩212px，DSH把320px菜单折回卡内。宽屏打开时给card留出右侧菜单的真实空间（卡片可以像19草图收窄、左对齐；不要把卡推到左栏或裁掉内容），使实际menu.left >= card.right，menu.right <= innerWidth。窄屏仍沿用DSH碰撞回退，不能强行溢出/横向滚动。菜单不改变确认/撤卡/草稿保护逻辑。打开/关菜单的card布局状态用React/CSS自有标记，不DOM mutation。closed时保持03/03c原全宽。父dock当前桌面width742、x485，在1160px main中；menu宽320，预留宽度要按整个main可用空间考虑，不能只写side=right（已有）。当前触发器在header，portal独立菜单，不改菜单内容。

请输出最小完整补丁，旧规则若换下保留注释或清晰说明可恢复，别删产品功能。root会机械应用、重新10道编译/305全量及同一真实窗口断言，确认菜单Escape回焦点不撤卡、时间/用量/分支隐藏、复制悬停才出。
