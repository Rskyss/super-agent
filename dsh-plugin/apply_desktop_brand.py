"""DSH 改版第 16 项：超级助手桌面版不弹 DeepSeek Harness 欢迎页，“关于”面板写超级助手。
这处没有公开开关，只能改 DSH 本体（用户 11-15 同意用补丁）。做法同 apply_root_config.py：
按原文锚点替换、可重复执行；任何一处锚点找不到就整份不改并报错（DSH 版本变了要人来核对）。
只在 SUPERAGENT_DESKTOP=1（由 core/scripts/local-desktop.mjs 设置）时生效，其余 DSH 行为不变。
原因与来历见 patches/desktop-brand.md。"""
import sys
from pathlib import Path

DSH = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "Desktop/project/dsh-base")
FLAG = "process.env.SUPERAGENT_DESKTOP"
MAIN = "apps/desktop/src/main.ts"
EDITS = [
    # 启动时：没登录也没 Key 就弹欢迎页
    ("if (!enteredWorkspace && needsWelcome({ loggedIn: state.loggedIn, hasApiKey: state.hasApiKey })) {",
     f"if (!enteredWorkspace && {FLAG} !== '1' && needsWelcome({{ loggedIn: state.loggedIn, hasApiKey: state.hasApiKey }})) {{"),
    # DeepSeek 账号退出登录后
    ("if (needsWelcome(value) && !quitting) {",
     f"if ({FLAG} !== '1' && needsWelcome(value) && !quitting) {{"),
    # DeepSeek 账号登录过期后
    ("if (!needsWelcome(value) || quitting) return",
     f"if ({FLAG} === '1' || !needsWelcome(value) || quitting) return"),
    # “关于”面板
    ("applicationName: 'DeepSeek Harness',",
     f"applicationName: {FLAG} === '1' ? app.getName() : 'DeepSeek Harness',"),
]

path = DSH / MAIN
text = path.read_text("utf-8")
out = text
for old, new in EDITS:
    if new in out:
        continue
    if out.count(old) != 1:
        raise SystemExit(f"{MAIN}: 找不到唯一的 {old!r}，DSH 版本可能变了，停止（一处都没改）")
    out = out.replace(old, new, 1)
if out == text:
    print(f"{MAIN}: 已打过补丁")
else:
    path.write_text(out, "utf-8")
    print(f"{MAIN}: 已打补丁（超级助手版不弹欢迎页、关于面板写超级助手）")
