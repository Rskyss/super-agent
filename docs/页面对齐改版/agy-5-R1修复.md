# 第五批 R1：空列表刷新失败提示（最小补丁）

只准在根收到独立正式R1统一结论后执行。本单仅TasksProbe.tsx，不改任何测试、Python、DSH、配置、夹具或其他产品，不sync/起服务/提交推送。你仍是唯一产品作者agy Gemini4 Hydrogen。

独立复查真实组件复现：首次cards成功[]，随后3秒poll返回error或throw，loadError被赋值但 loaded && cards.length===0 空态分支没有呈现loadError，因此静默显示空任务。非空分支已有“刷新任务失败：...”role=alert可用，初读失败也不冒充17。需要在真实17空态分支也呈现同样的刷新失败提示（建议在原actionError/DSH图标之前用现有errorBanner+role=alert）。保留最后已成功读到的空态/重试poll/旧数据/CTA，不抹掉接口错误、不重写刷新机制。

根已原样复制独立测试并扩到RemoteResult失败+throw、空+非空：alignment-overview-refresh.client.spec.tsx，实跑2红4绿；另乱序旧错不覆盖新列表、坏日期/firstPass未知为绿。冻结31，85文件全备份。禁止改测试。

请只返回一个适用仓库根路径的最小unified diff（JSON patch字段也可），由根机械应用；无需再读整批/重拍图，不扩大范围。根负责全量、十道和未施工Codex R2。主界面正常09/17应完全不变。
