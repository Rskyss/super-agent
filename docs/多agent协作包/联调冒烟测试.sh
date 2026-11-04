#!/usr/bin/env bash
# 联调冒烟测试：验证 Codex / agy / Cursor(agent) 三家 CLI 能否连上、指定模型、写文件。
# 用法：bash 联调冒烟测试.sh [工作目录，默认新建临时目录]
# 详细说明见同目录 联调手册.md
set -u

WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK" && cd "$WORK" || exit 1
rm -f codex_smoke.txt agy_smoke.txt agent_smoke.txt

pass=0
fail=0
check() { # 参数：名称 文件 期望内容
  if [ -f "$2" ] && [ "$(tr -d '\r\n' < "$2")" = "$3" ]; then
    echo "PASS $1"; pass=$((pass + 1))
  else
    echo "FAIL $1：文件 $2 未生成或内容不对"; fail=$((fail + 1))
  fi
}

# 1) Codex：gpt-6.1-sol，推理强度 xhigh
timeout 300 codex exec --skip-git-repo-check -s workspace-write \
  -m gpt-6.1-sol -c model_reasoning_effort="xhigh" \
  "在当前目录创建文件 codex_smoke.txt，内容为 CODEX_SMOKE_OK，然后只回复 DONE" \
  </dev/null >/dev/null 2>&1
check codex codex_smoke.txt CODEX_SMOKE_OK

# 2) agy：gemini-4-argon-high（不使用 --dangerously-skip-permissions）
timeout 300 agy -p "在当前目录创建文件 agy_smoke.txt，内容为 AGY_SMOKE_OK，然后只回复 DONE" \
  --model gemini-4-argon-high --output-format json </dev/null >/dev/null 2>&1
check agy agy_smoke.txt AGY_SMOKE_OK

# 3) Cursor：命令是 agent，模型 auto。偶发首次不落盘，失败后重试一次。
for _ in 1 2; do
  timeout 300 agent -p --trust --model auto --output-format json \
    "在当前目录创建文件 agent_smoke.txt，内容为 AGENT_SMOKE_OK，然后只回复 DONE" \
    </dev/null >/dev/null 2>&1
  [ -f agent_smoke.txt ] && break
done
check agent agent_smoke.txt AGENT_SMOKE_OK

echo "结果：${pass} 通过 / ${fail} 失败（工作目录：${WORK}）"
[ "$fail" -eq 0 ]
