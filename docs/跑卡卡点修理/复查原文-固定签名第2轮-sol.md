只读复查了 `5384c06`。签名分支用假命令验证，截图分支用同一套 macOS 接口验证。本次未重装、未签名，也未改动文件、系统设置或钥匙串。

1. **查证书出错会中断安装：已修。** 模拟查询失败后，脚本会提示改用临时签名并继续。手动指定的证书签不上时，会给出提示，并在删除旧应用之前停下。[签名脚本](/Users/rs/Desktop/project/开发助手/desktop/mac/build.sh:55)
2. **老用户换签名后的授权说明：还缺一步。** README 补了怎么清旧授权，但普通打开开发助手不会申请录屏权限。照着“再打开开发助手重新授权”走，用户可能等不到弹窗。应写明清掉后，在同一个设置页面点“＋”，把新版开发助手加回来并打开录屏权限。[README](/Users/rs/Desktop/project/开发助手/README.md:124) · [截图入口](/Users/rs/Desktop/project/开发助手/desktop/mac/Sources/SuperAgent/main.swift:676) · [Apple 设置说明](https://support.apple.com/zh-cn/guide/mac-help/mchld6aa7d23/27/mac/27)

**轻微**

- **多张证书时仍然取第一张。** 如果排第一的证书换了，macOS 可能又要求授权。用 `SUPERAGENT_SIGN_ID` 指定证书指纹可以固定选择。[签名脚本](/Users/rs/Desktop/project/开发助手/desktop/mac/build.sh:56) · [Apple 签名说明](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements/)
- **自测截图的两种卡住情况已修。** 模拟工具输出 1 MiB 报错，能读完并拿到退出码。工具启动不了时，代码会直接把原因记进 `status.txt`。[截图代码](/Users/rs/Desktop/project/开发助手/desktop/mac/Sources/SuperAgent/main.swift:2699)
- **这次新增的回退提示分不清查询失败和确实没证书。** 模拟查询报错时，屏幕上只显示“没找到证书”，原始错误被丢掉了。安装能继续，但用户看不出为什么这次用了临时签名。[签名脚本](/Users/rs/Desktop/project/开发助手/desktop/mac/build.sh:56)

结论：不通过