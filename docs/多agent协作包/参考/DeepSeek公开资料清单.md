# DeepSeek Super-agent：公开资料清单（2026-11-04 整理）

- 全文（中文，知乎）：https://zhuanlan.zhihu.com/p/2099987320443237060
- 原文（公众号）：https://mp.weixin.qq.com/s/zoS7GlReWPhfCjENGxXCdg
- 开源原型（MIT 许可）：https://github.com/deepseek-ai/dsh-superagent-prototype
- 正式论文：未查到（截至 2026-11-04）

注意：原型官方声明为“仅供参考的研究原型，并非 DeepSeek 正式产品”，公开版与内部版、文章描述的实现不完全相同。

**A）原文的 9 个章节标题**

1. Super-agent
2. 双层架构
3. 事件驱动
4. 面向人的交互
5. 计算、状态与环境的分离
6. Watchdog
7. 初步实验
8. 开放问题
9. 结语

来源：[知乎原文](https://zhuanlan.zhihu.com/p/2099987320443237060)。直接打开返回 403，随后通过网页提取工具成功读取正文；以上按原文章节整理。

**B）GitHub 仓库与 README**

- **准确网址**：[https://github.com/deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype)
- **许可证**：[MIT](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/LICENSE)，版权标注为 2026 DeepSeek Harness Team。
- **安装，大白话版**：README 建议让编程助手读取仓库的 `SETUP.md`，带你完成部署。助手会一次问一个简短问题，集中展示完整方案，然后执行安装和配置。想手动部署，可看 `docs/006-deployment.md`，其中也有升级和排错说明。
- **用法，大白话版**：它作为 DeepSeek Harness 的插件包，服务于一个 GitHub 仓库，也可接入飞书。获准的用户提出任务后，它会建立工作项，在独立会话和目录里处理，再回到原来的讨论中交付结果，例如提交 PR；网页面板展示需要人处理的事项。它只在收到请求或团队规则允许时行动。
- **开发检查**：`pnpm check` 会运行类型检查、测试和构建。[以上来自 README](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/README.md)。

README 明确声明的原文：

> Research prototype for reference only, not an official DeepSeek product.

意思是：“仅供参考的研究原型，并非 DeepSeek 正式产品。”README 还说明，公开版本与内部版本、文章描述的实现不完全相同，文章中的部分设计尚未实现。[原文位置](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/README.md)

**C）是否有正式论文**

截至 **2026 年 11 月 4 日，没有查到该项目对应的正式论文**，包括 arXiv 预印本或其他学术发表记录。目前可核实的公开材料是设计文章和原型仓库；[README](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/README.md)也将配套文字称为“文章（article）”，未提供论文编号、DOI 或学术发表链接。
