#!/bin/bash
# 生成“开发助手”（原“超级助手”，11-17 改名冻结）Mac 应用：编译窗口 → 画图标 → 装进应用包 → 本机签名。
# 用法：bash desktop/mac/build.sh            生成到 desktop/mac/build/开发助手.app
#       bash desktop/mac/build.sh --install  另外放一份到 ~/Applications（启动台、聚焦搜索里能找到）
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
rsync -a --exclude '__pycache__' --exclude 'mac' "$ROOT/desktop" "$APP/Contents/Resources/super-agent/"

echo "5/5 本机签名…"
codesign --force --deep --sign - "$APP"

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/Applications"
  # 先把两个目标都核对完（只认本应用的包），全部通过才开始删，免得删了旧的却装不上新的（复查第 2 轮）
  bash "$HERE/remove_app.sh" --check "$HOME/Applications/超级助手.app"
  bash "$HERE/remove_app.sh" --check "$HOME/Applications/开发助手.app"
  bash "$HERE/remove_app.sh" "$HOME/Applications/超级助手.app"   # 改名前的旧应用，免得搜索里出现两个
  bash "$HERE/remove_app.sh" "$HOME/Applications/开发助手.app"
  cp -R "$APP" "$HOME/Applications/"
  echo "完成：$HOME/Applications/开发助手.app"
else
  echo "完成：$APP"
fi
