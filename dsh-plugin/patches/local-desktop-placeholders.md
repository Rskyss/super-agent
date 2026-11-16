# 本地临时桌面版“超级助手”核心占位记录（不修改 DSH 核心）

本文件记录施工单第 12 项中因**严禁修改 DSH 核心代码（`dsh-base`）**而保留的占位说明与公开替代方案：

1. **DSH 原生 Welcome 窗口（额外前置入口）**
   - **现状**：`apps/desktop/src/main.ts` 在 `needsWelcome({ loggedIn, hasApiKey })` 为真时会先弹出原生 Welcome 窗口；当前没有通过公开配置彻底禁用的开关。
   - **处理**：不修改 DSH 核心，也不伪造 `hasApiKey` 或写入假凭据；在原生 Welcome 窗口点击“先看看 / 跳过”后，即可进入超级助手第 11 项首次模型配置。

2. **关于面板（About Panel）与空白会话 Hero 文案**
   - **现状**：`apps/desktop/src/main.ts` 第 1161 行硬编码 `app.setAboutPanelOptions({ applicationName: 'DeepSeek Harness', ... })`，以及原生空白会话 Hero 标题属于内置包文案。
   - **处理**：不修改 DSH 核心代码；主窗口标题、macOS 菜单栏应用名、Bundle 显示名与网页左侧栏品牌标/名称已分别通过 Electron 公开 API（`app.setName`、`BrowserWindow` `page-title-updated`）和 DSH 公共插槽（`sidebar.brand.mark`、`sidebar.brand.name`）替换为“超级助手”与碗标。

3. **本地开发壳性质**
   - **现状**：临时 `超级助手.app` 保持 `app.isPackaged === false`，通过只读复用 `dsh-base` 的 Electron、built `lib/main.js`、`desktop-host/lib`、`primary-runtime` 与已构建插件产物运行。
   - **处理**：仅用于本机隔离验证与 OS 走查，不是可分发安装包。
