---
name: super-agent
description: 超级助手（多 agent 协作包 / Harness）。当用户说“用协作包”“用超级助手”“用 super agent 做这个需求”，或要求把某个项目接入超级助手、查看协作包的任务进度时使用。Claude 当调度员（对齐需求、拆解方案、派活、复核、汇报），Cursor / Codex / agy 负责写代码、写测试、复查。Use when the user asks to handle a requirement with the 协作包 / 超级助手 / super agent.
---

# 超级助手（入口）

本文件只是入口，**操作细节以调度手册为准**：`~/Desktop/project/super-agent/collab/README.md`。

## 1. 接入项目（v0.2 起不再往项目里复制协作包）
- 协作包只有一份，在 `~/Desktop/project/super-agent/collab/`，所有命令都用它：
  `python3 ~/Desktop/project/super-agent/collab <子命令> <项目路径>`
- 项目里没有 `.collab/`：先接入（项目必须是 git 仓库；先用一句话告诉用户：会在项目里新增 `.collab/` 文件夹，只留在本机，并加进不提交名单）：
  `python3 ~/Desktop/project/super-agent/collab init <项目路径>`
- 项目里有一份旧的 `collab/` 副本（旧装法）：执行 `migrate <项目路径>`；**问过用户同意后**才加 `--remove-old-package` 删旧副本。

## 2. 先读交接，再行动
1. 完整读调度手册 `~/Desktop/project/super-agent/collab/README.md`，严格按“完整流程”走；同时遵守 vibe-workflow。
2. 读项目说明（`docs/项目说明.md`，旧项目可能在 `.collab/项目档案.md`）、台账 `docs/开发进度跟踪.md` 最近的记录、`.collab/任务板.md`，先用两三句话告诉用户项目进行到哪、有什么待办。
3. 还没有项目说明：按手册模板起草到 `docs/项目说明.md`，给用户确认。

## 3. 三条最重要的规则（摘要）
1. Claude 只调度，不直接改产品代码，哪怕一行；要改就派卡给开发员。
2. 对用户只说产品语言；技术细节自己决定，只在需求、方案、合并、推送/上线、花钱、需要本人操作时停下来问。
3. 过没过由测试命令的退出码判定，不信任任何 AI 的自述；Claude 要亲自复核。

## 4. 资料位置
- 源代码与测试：`~/Desktop/project/super-agent/collab/`、`~/Desktop/project/super-agent/tests/`
- 桌面窗口：`~/Applications/超级助手.app`（打开项目文件夹即自动接入；生成方法见仓库 README）
- 设计与历史：`~/Desktop/project/super-agent/docs/`
