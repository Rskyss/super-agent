# 第五批根自查：无定义主题变量（仅最小补丁）

在你的第五批主施工结束后执行，不与同一产品文件的其他writer并行。根新增 alignment-overview-tokens.spec.ts 已真实先红，使用只读DSH design-platform.css+base.css核对所有启用变量，3个红项：--dsw-alias-bg-elevated、--dsw-specific-card、--dsw-alias-fill-hover。前两个是statCard后备值（主值layer-2已存在），第三个启用在tableRow hover/focus-visible，真实无效。根首版遗漏base.css产生半径误报，已修测试读完整基础+主题，当前仅这3个红；冻结30（84文件，全部备份）。禁止修改测试。

只需 TasksProbe.module.css 最小修改：statCard直接用真实 --dsw-alias-bg-layer-2，原后备表达式注释保留；tableRow hover/focus-visible改用真实 --dsw-alias-interactive-bg-hover（原表达式注释保留）。该变量实际在DSH主题浅/深两处定义。不能删其他功能、顺手改结构/控件/Python/DSH/保护文件，不能提交推送/sync/起服务。根负责最终全量和UI。

请返回一个适用仓库根路径 dsh-plugin/core/src/client/TasksProbe.module.css 的最小unified diff（可以用JSON patch字段），根机械应用即可，不必再次大范围读图。本单不是正式复查轮次。
