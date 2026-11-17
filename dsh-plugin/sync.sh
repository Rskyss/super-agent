#!/bin/bash
# 把本仓库 dsh-plugin/core 同步进锁定版本 DSH，登记根配置，再编译。
# 用法：dsh-plugin/sync.sh [DSH 目录]（默认：本仓库同级的 dsh-base，即 ~/Desktop/project/超级玩家/dsh-base）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DSH="${1:-$HERE/../../dsh-base}"
LOCKED="0.2.2-rc.3"
# 先核对目标确实是锁定版本的 DSH，再做任何删除式同步（复查第 1 轮建议 1）。
[ -f "$DSH/package.json" ] || { echo "不是 DSH 目录：$DSH" >&2; exit 1; }
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get('name')=='@deepseek-ai/dsh-root' and d.get('version')==sys.argv[2] else 1)" "$DSH/package.json" "$LOCKED" \
  || { echo "DSH 版本不是 $LOCKED，停止" >&2; exit 1; }
TARGET="$DSH/packages/superagent/core"
mkdir -p "$TARGET"
# 整条路径都不许经过符号链接：解析后的真实路径必须和字面路径一致（复查第 2 轮建议 2）。
REAL_DSH="$(cd -P "$DSH" && pwd)"
REAL_TARGET="$(cd -P "$TARGET" && pwd)"
if [ "$REAL_TARGET" != "$REAL_DSH/packages/superagent/core" ]; then echo "目标路径经过符号链接，停止：$TARGET -> $REAL_TARGET" >&2; exit 1; fi
rsync -a --delete --exclude lib --exclude node_modules "$HERE/core/" "$TARGET/"
python3 "$HERE/apply_root_config.py" "$DSH"
python3 "$HERE/apply_desktop_brand.py" "$DSH"   # 第 16、24 项：不弹 DSH 欢迎页、空对话页标题（本体补丁，原因见 patches/desktop-brand.md）
cd "$DSH"
pnpm install --no-frozen-lockfile >/dev/null
# 网页和窗口标题：DSH 公开的编译设置 DSH_CLIENT_TITLE，不改本体（第 16 项；第 22 项改名“超级玩家”）
# 空对话页标题：DSH_CLIENT_HERO_HEADLINE，配合上面的补丁生效，有值时也不显示“预览版”（第 24 项；页面对齐改版 11-16 照图改为“今天想交给我什么事？”）
DSH_CLIENT_TITLE=超级玩家 DSH_CLIENT_HERO_HEADLINE=今天想交给我什么事？ DSH_GATE_CONCURRENCY=${DSH_GATE_CONCURRENCY:-6} pnpm run build
