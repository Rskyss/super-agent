# agy：修复独立复查第1轮

仅修1-1/1-2的5项中等问题。你是唯一代码作者 Gemini 4 Hydrogen High，当前super-play，不提交推送，不改测试、保护文件、DSH本体。禁止任何工具调用；下面将附完整当前源码和DSH组件源码。最终只输出 apply_patch 补丁，无围栏、无解释。

1. TaskSidebar.handleNewChat 成功后切回chats；失败时保留原标签和错误。
2. TaskSidebar承接可选collapseSidebar、drawer、collapsed；品牌区用DSH Button与IconPanelLeftOutlineRegular提供“收起侧边栏”，宽栏可hover/focus显示，保持草图主结构。root入口传宿主toggleSidebar。
3. 窄屏复用DSH useModalLayer，恢复入焦/Tab约束/ESC与关闭回焦。根节点有dialog、aria-label“侧边栏”、aria-modal、data-shortcut-modal、tabIndex。仅drawer且未collapsed激活；所有hooks必须在条件return前执行。root入口把drawer/collapsed原值传入。
4. clientVersion去掉typeof process guard；DSH构建已把process.env.DSH_CLIENT_VERSION注入为字面量，照原DSH直接读取。当前真实浏览器查v0.2.2-rc.3为0个，产物证明guard使其不显示。
5. 对话/任务用DSH现成SegmentedTabs（label支持ReactNode角标）；唯一id、aria-controls和tabpanel的aria-labelledby配对；左右/Home/End键、单一Tab停靠走DSH组件。切任务仍调showOverview，零不显示。旧自制标签JSX/CSS注释保留，不删除被替换代码。

新增交互失败测试已实跑：alignment-sidebar现9项，原5项绿，导航/收起/抽屉/键盘4项红。其余插件259通过，编译10通过。测试不许改。

只改TaskSidebar.tsx、其CSS、client/index.ts（需要时可抽小.tsx组件）。index.ts内不能写JSX，保持createElement和正确类型。不提前做1-3/1-4/1-5。
