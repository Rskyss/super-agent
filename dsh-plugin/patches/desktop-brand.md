# 桌面版品牌补丁（第 16 项，用户 11-15 同意；第 24 项 11-16 加空对话页标题）

- **第 16 项 · 改了什么**：DSH `apps/desktop/src/main.ts` 四处——启动、DeepSeek 账号退出、登录过期这三处“弹欢迎页”的判断，和“关于”面板的应用名。只在环境变量 `SUPERAGENT_DESKTOP=1` 时生效（`core/scripts/local-desktop.mjs` 设置），DSH 自己的版本行为不变。
- **为什么只能改本体**：DSH 桌面版只要“没登录 DeepSeek 账号、也没存任何模型 Key”就固定先弹它的欢迎页（含“登录”按钮），没有公开开关；“关于”面板名字是写死的。超级助手不用 DeepSeek 账号，首次使用走自己的引导。
- **怎么打**：`apply_desktop_brand.py`，`sync.sh` 每次同步自动执行；按原文锚点替换、可重复执行，锚点对不上整份不改并报错停下。
- **升级 DSH 时**：锚点对不上会报错，要人来核对新版代码再改脚本。
- **第 24 项 · 空对话页标题**：DSH `packages/client/ui-conversation/src/client/skeleton/EmptyHero.tsx` 两处——标题优先用编译设置 `DSH_CLIENT_HERO_HEADLINE`（`sync.sh` 设成“今天想交给我什么事？”（11-16 页面对齐照图，原为“说出你想做的事”）），有这个设置时不显示“预览版”徽标。没设置时（DSH 自己的版本）照旧。为什么只能改本体：标题文字来自 DSH 的对话字典，每种语言只许一个主人、重复注册会报错，没有公开的逐条覆盖办法（调查见 `docs/DSH改版/第24项-公开能力调查.md`）；图标有公开位置，由插件换，不在这份补丁里。
- **两份文件一起核对**：任何一处锚点对不上，两份文件都不改。
- **不用补丁的部分**：网页和窗口标题用 DSH 公开的编译设置 `DSH_CLIENT_TITLE=超级玩家`（第 22 项由“超级助手”改名，`sync.sh` 里）；应用名、程序坞、⌘Tab 名字在 `core/scripts/local-desktop.mjs` 的 `APP_DISPLAY_NAME`。
