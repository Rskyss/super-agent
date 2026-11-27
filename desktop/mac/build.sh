#!/bin/bash
# 生成“开发助手”（原“超级助手”，11-17 改名冻结）Mac 应用：编译窗口 → 画图标 → 装进应用包 → 本机签名。
# 用法：bash desktop/mac/build.sh            生成到 desktop/mac/build/开发助手.app
#       bash desktop/mac/build.sh --install  装到 ~/Applications（启动台、聚焦搜索里能找到），编译出来的那份装完就删，本机只留一个
# 第一次运行会从 GitHub 下载终端组件 SwiftTerm（固定 1.99.0 版），需要联网，之后就不用了。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
APP="$HERE/build/开发助手.app"

echo "1/5 编译窗口程序…"
swift build -c release --package-path "$HERE"
BIN="$(swift build -c release --package-path "$HERE" --show-bin-path)/SuperAgent"

echo "2/5 装配应用包…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/super-agent"
cp "$BIN" "$APP/Contents/MacOS/SuperAgent"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>开发助手</string>
  <key>CFBundleDisplayName</key><string>开发助手</string>
  <key>CFBundleIdentifier</key><string>local.superagent.desktop</string>
  <key>CFBundleExecutable</key><string>SuperAgent</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

echo "3/5 画图标…"
ICONSET="$HERE/build/AppIcon.iconset"
rm -rf "$ICONSET"
swift "$HERE/make_icon.swift" "$ICONSET"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

echo "4/5 放入流转面板（数据服务和页面，只读项目记录）…"
rsync -a --exclude '__pycache__' "$ROOT/collab" "$APP/Contents/Resources/super-agent/"
rsync -a --delete "$ROOT/agents" "$APP/Contents/Resources/super-agent/"   # 规则包，和 collab 并排（collab/rulebook.py 按这个位置找）
rsync -a --exclude '__pycache__' --exclude 'mac' "$ROOT/desktop" "$APP/Contents/Resources/super-agent/"

echo "5/5 本机签名…"
# 固定签名（11-27 用户要）：用本机钥匙串里的“Apple Development”证书签，重装后 macOS 仍认得是同一个程序，
# 录屏等权限不用重新给。可用 SUPERAGENT_SIGN_ID 指定别的证书；找不到证书就退回临时签名（每次重装都要重新给权限）。
# 查证书出错（钥匙串打不开等）不让脚本中途退出，照样退回临时签名（复查第 1 轮）
# 用 <<< 和变量截取代替管道：输出很长时不会因为管道提前关闭让脚本退出（复查第 3 轮）
SIGN_NOTE="没找到“Apple Development”证书（装了证书的话，可能是钥匙串没解锁）"
if [ -n "${SUPERAGENT_SIGN_ID:-}" ]; then
  SIGN_ID="$SUPERAGENT_SIGN_ID"
elif IDS="$(security find-identity -v -p codesigning 2>&1)"; then
  SIGN_ID="$(awk '/"Apple Development:/ {print $2; exit}' <<< "$IDS")"
else
  SIGN_ID=""
  SIGN_NOTE="查钥匙串里的证书出错（${IDS%%$'\n'*}）"
fi
if [ -n "$SIGN_ID" ]; then
  if ! codesign --force --deep --sign "$SIGN_ID" "$APP"; then
    echo "  用证书 ${SIGN_ID} 签名失败（证书不在、或钥匙串没允许）。检查 SUPERAGENT_SIGN_ID，或在钥匙串弹窗里点“始终允许”后重跑。" >&2
    exit 1
  fi
else
  echo "  ${SIGN_NOTE}，改用临时签名：重装后录屏权限要重新给。"
  codesign --force --deep --sign - "$APP"
fi

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Applications"
  # 先把两个目标都核对完（只认本应用的包），全部通过才开始删，免得删了旧的却装不上新的（复查第 2 轮）
  bash "$HERE/remove_app.sh" --check "$HOME/Applications/超级助手.app"
  bash "$HERE/remove_app.sh" --check "$HOME/Applications/开发助手.app"
  bash "$HERE/remove_app.sh" "$HOME/Applications/超级助手.app"   # 改名前的旧应用，免得搜索里出现两个
  bash "$HERE/remove_app.sh" "$HOME/Applications/开发助手.app"
  cp -R "$APP" "$HOME/Applications/"
  bash "$HERE/remove_app.sh" "$APP"   # 编译出来的那份不留：搜索里会多一个，点错就打开旧的（用户 11-25）
  echo "完成：$HOME/Applications/开发助手.app"
else
  echo "完成：$APP"
fi
