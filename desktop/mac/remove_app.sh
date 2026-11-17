#!/bin/bash
# 删掉一个旧的开发助手（或改名前的超级助手）应用包：只删编号是 local.superagent.desktop 的；
# 不存在就当删过了；同名的别的东西（别的应用、普通文件夹）一律不删、报错停下（Codex 复查 11-17）。
# 用法：bash remove_app.sh [--check] <应用包路径>（--check 只核对、不删）
set -euo pipefail
check=""
[ "${1:-}" = "--check" ] && { check=1; shift; }
target="$1"
[ -e "$target" ] || [ -L "$target" ] || exit 0
plist="$target/Contents/Info.plist"
ident=""
if [ -d "$target" ] && [ ! -L "$target" ] && [ -f "$plist" ]; then
  ident="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist" 2>/dev/null || true)"
fi
if [ "$ident" != "local.superagent.desktop" ]; then
  echo "不删：$target 不是开发助手的应用包（编号：${ident:-无}）" >&2
  exit 1
fi
[ -n "$check" ] || rm -rf "$target"
