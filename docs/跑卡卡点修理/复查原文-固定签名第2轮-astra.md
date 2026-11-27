复查了提交 `5384c06`。上一轮两条主要问题都已修复。两条轻微问题里，自测截图卡住已修复，多张证书时选哪张仍取决于排列顺序。这次全程只读，签名分支用假工具验证，截图分支用独立子进程验证，没有重装应用或改动授权。

| 上一轮反馈 | 本轮核对结果 |
|---|---|
| 查证书报错会中断安装 | **已修复。** 模拟查询报错和查不到证书，两种情况都会提示并继续走临时签名。手动指定的证书签名失败时，会给出原因和处理办法，在删除旧安装前停止。[签名脚本](/Users/rs/Desktop/project/开发助手/desktop/mac/build.sh:56) |
| README 漏了首次换签名的处理 | **已补上。** 安装说明写清了“开关亮着，截图却失败”的现象，也给了清除本应用旧录屏授权的办法。[README](/Users/rs/Desktop/project/开发助手/README.md:124) |
| 轻微：多张证书时只取第一张 | **仍在。** 模拟把两张证书的顺序对调，选中的指纹也跟着变。多证书用户可用 `SUPERAGENT_SIGN_ID` 固定指纹。[证书选择](/Users/rs/Desktop/project/开发助手/desktop/mac/build.sh:56) |
| 轻微：自测截图可能卡住 | **两种情况都已修复。** 模拟子进程输出 1 MiB 报错，能读完并取得退出码。工具启动失败时，代码直接记下原因和 `shotExit=-1`，随后继续写 `status.txt`。[截图处理](/Users/rs/Desktop/project/开发助手/desktop/mac/Sources/SuperAgent/main.swift:2698) |

**本次改动里的轻微问题**

- README 的“再打开开发助手重新授权”还差一句。普通双击打开不会触发录屏请求，清掉旧授权后，要再跑一次 `--snapshot` 自测截图才会触发。照文档双击后等弹窗的人会等不到。[README](/Users/rs/Desktop/project/开发助手/README.md:124) · [启动入口](/Users/rs/Desktop/project/开发助手/desktop/mac/Sources/SuperAgent/main.swift:676)
- 截图报错先截前 2000 个字节，再转成文字。如果刚好截在一个汉字中间，整段 `shotErr` 都会变空。用 2400 字节的中文报错复现了，退出码仍会保留。只影响长报错的诊断。[报错处理](/Users/rs/Desktop/project/开发助手/desktop/mac/Sources/SuperAgent/main.swift:2704)

结论：通过