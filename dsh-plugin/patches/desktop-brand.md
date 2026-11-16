# 桌面版品牌补丁（第 16 项，用户 11-15 同意）

- **改了什么**：DSH `apps/desktop/src/main.ts` 四处——启动、DeepSeek 账号退出、登录过期这三处“弹欢迎页”的判断，和“关于”面板的应用名。只在环境变量 `SUPERAGENT_DESKTOP=1` 时生效（`core/scripts/local-desktop.mjs` 设置），DSH 自己的版本行为不变。
- **为什么只能改本体**：DSH 桌面版只要“没登录 DeepSeek 账号、也没存任何模型 Key”就固定先弹它的欢迎页（含“登录”按钮），没有公开开关；“关于”面板名字是写死的。超级助手不用 DeepSeek 账号，首次使用走自己的引导。
- **怎么打**：`apply_desktop_brand.py`，`sync.sh` 每次同步自动执行；按原文锚点替换、可重复执行，锚点对不上整份不改并报错停下。
- **升级 DSH 时**：锚点对不上会报错，要人来核对新版代码再改脚本。
- **不用补丁的部分**：网页和窗口标题用 DSH 公开的编译设置 `DSH_CLIENT_TITLE=超级助手`（`sync.sh` 里）。
