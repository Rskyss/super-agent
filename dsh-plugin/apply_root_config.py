"""把超级助手插件登记进锁定版本 DSH 的三个根配置（只加行、可重复执行）。
DSH 的根 tsconfig 带注释，不能按 JSON 改，按文本在固定位置插入一行。"""
import sys
from pathlib import Path

DSH = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "Desktop/project/dsh-base")
EDITS = [
    ("tsconfig.base.json", '"paths": {', '      "@superagent/core": ["./packages/superagent/core/src"],'),
    ("tsconfig.host.json", '"references": [', '    { "path": "./packages/superagent/core/tsconfig.host.json" },'),
    ("tsconfig.client.json", '"references": [', '    { "path": "./packages/superagent/core/tsconfig.client.json" },'),
]
for name, anchor, line in EDITS:
    path = DSH / name
    text = path.read_text("utf-8")
    if line.strip() in text:
        print(f"{name}: 已登记")
        continue
    if text.count(anchor) != 1:
        raise SystemExit(f"{name}: 找不到唯一的 {anchor!r}，DSH 版本可能变了")
    text = text.replace(anchor, anchor + "\n" + line, 1)
    path.write_text(text, "utf-8")
    print(f"{name}: 已加一行")
