"""DSH 改版第 16、24 项：桌面版不弹 DeepSeek Harness 欢迎页，“关于”面板写自己的名字；空对话页标题换成自己的一句话、去掉“预览版”。
这几处没有公开开关，只能改 DSH 本体（用户 11-15 同意欢迎页用补丁；11-16 第 24 项同样办法）。做法同 apply_root_config.py：
按原文锚点替换、可重复执行；任何一处锚点找不到就所有文件都不改并报错（DSH 版本变了要人来核对）。
欢迎页、关于面板只在 SUPERAGENT_DESKTOP=1（由 core/scripts/local-desktop.mjs 设置）时生效；
空对话页标题只在编译设置 DSH_CLIENT_HERO_HEADLINE 有值（sync.sh 设置）时生效；其余 DSH 行为不变。
原因与来历见 patches/desktop-brand.md。"""
import os
import sys
from pathlib import Path

DSH = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "dsh-base")  # 本仓库同级的 dsh-base（11-17 工具分家）
FLAG = "process.env.SUPERAGENT_DESKTOP"
HEADLINE = "process.env.DSH_CLIENT_HERO_HEADLINE"
FILES = {
    "apps/desktop/src/main.ts": [
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
    ],
    # 第 24 项：空对话页标题（DSH 字典每个语言只许一个主人，没有公开覆盖办法，见 docs/DSH改版/第24项-公开能力调查.md）
    "packages/client/ui-conversation/src/client/skeleton/EmptyHero.tsx": [
        ("<span>{t('hero.headline')}</span>",
         f"<span>{{{HEADLINE} ?? t('hero.headline')}}</span>"),
        ("<span className={css.previewBadge}>{t('hero.preview')}</span>",
         f"{{{HEADLINE} === undefined && <span className={{css.previewBadge}}>{{t('hero.preview')}}</span>}}"),
    ],
}

# 先把所有文件都核对、算好，全部对得上才一起写（任何一处对不上，一个文件都不改）
planned = []
for rel, edits in FILES.items():
    path = DSH / rel
    text = path.read_text("utf-8")
    out = text
    for old, new in edits:
        # 只认两种状态：恰好一处新写法、没有旧写法（已打过）；恰好一处旧写法、没有新写法（待打）。
        # 新写法里可能包含旧写法（比如“预览版”那处），所以数旧写法时先把新写法去掉。其余一律停下。
        patched = out.count(new)
        unpatched = out.replace(new, "").count(old)
        if patched == 1 and unpatched == 0:
            continue
        if patched != 0 or unpatched != 1:
            raise SystemExit(f"{rel}: 找不到唯一的 {old!r}（或新旧写法同时在），DSH 版本可能变了，停止（一处都没改）")
        out = out.replace(old, new, 1)
    planned.append((rel, path, text, out))

# 先全部写成临时文件，再逐个换上；中途出错就把已换上的恢复原样
changed = [(rel, path, text, out) for rel, path, text, out in planned if out != text]
done = []
try:
    for rel, path, text, out in changed:
        tmp = path.with_name(path.name + ".brand-tmp")
        tmp.write_text(out, "utf-8")
    for rel, path, text, out in changed:
        os.replace(path.with_name(path.name + ".brand-tmp"), path)
        done.append((path, text))
except BaseException:
    # 恢复也走“写临时文件再换上”，不直接截断原文件；每份恢复各自兜住，清理一定执行
    for path, text in done:
        back = path.with_name(path.name + ".brand-restore")
        try:
            back.write_text(text, "utf-8")
            os.replace(back, path)
        except OSError as exc:
            print(f"{path}: 恢复原样失败（{exc}），请用 git checkout 恢复这个文件", file=sys.stderr)
        finally:
            back.unlink(missing_ok=True)
    for rel, path, text, out in changed:
        path.with_name(path.name + ".brand-tmp").unlink(missing_ok=True)
    raise
for rel, path, text, out in planned:
    print(f"{rel}: " + ("已打过补丁" if out == text else "已打补丁"))
