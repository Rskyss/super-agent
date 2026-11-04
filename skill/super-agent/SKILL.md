---
name: super-agent
description: 超级助手（多 agent 协作包 / Harness）。当用户说“用协作包”“用超级助手”“用 super agent 做这个需求”，或要求把协作包装进某个项目、查看协作包的任务进度时使用。Claude 当调度员（对齐需求、拆解方案、派活、复核、汇报），Cursor / Codex / agy 负责写代码、写测试、复查。Use when the user asks to handle a requirement with the 协作包 / 超级助手 / super agent.
---

# 超级助手（入口）

本文件只是入口，**操作细节以调度手册为准**。

## 1. 找到协作包
- 先看当前项目根目录下有没有 `collab/`，并且里面有 `规则.md` 和 `README.md`。
- 没有的话，从源目录安装（项目必须是 git 仓库；安装前用一句话告诉用户：会在项目里新增 `collab/`（协作包）和 `.collab/`（协作记录）两个文件夹）：
  `python3 ~/Desktop/project/super-agent/collab install <项目路径>`
- 已安装但版本旧：`python3 ~/Desktop/project/super-agent/collab install <项目路径> --update`（只替换包，不动协作记录）。

## 2. 先读调度手册，再行动
完整读完项目里的 `collab/README.md`，严格按其中“完整流程”一步步走；同时遵守 vibe-workflow。

## 3. 三条最重要的规则（摘要）
1. Claude 只调度，不直接改产品代码，哪怕一行；要改就派卡给开发员。
2. 对用户只说产品语言；技术细节自己决定，只在需求、方案、合并、推送/上线、花钱、需要本人操作时停下来问。
3. 过没过由测试命令的退出码判定，不信任任何 AI 的自述；Claude 要亲自复核。

## 4. 资料位置
- 源代码与测试：`~/Desktop/project/super-agent/collab/`、`~/Desktop/project/super-agent/tests/`
- 设计与历史：`~/Desktop/project/super-agent/docs/多agent协作包/`（方案、联调手册、试跑与验收记录）
