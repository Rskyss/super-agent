# 积木库施工规矩（写给 Cursor）

用户把这份目录交给你施工。Claude 写施工单和验收测试，你写代码，Claude 验收。

## 开工前必读

1. 本文件。
2. [需求共识](../需求共识.md)、[方案](../方案.md)（尤其是末尾的“技术设计”）、[任务清单](../任务清单.md)。61 号单起是“沙盒接入”这一轮，另读 [沙盒接入/需求共识](../沙盒接入/需求共识.md)、[沙盒接入/方案](../沙盒接入/方案.md)、[沙盒接入/任务清单](../沙盒接入/任务清单.md)，两边冲突以沙盒接入为准。
3. 当前这张单，见下方“单子列表”。

## 单子列表

| 单号 | 任务清单第几项 | 文件 | 状态 |
|---|---|---|---|
| 50 | 1 | [50-资料夹和接法卡.md](50-资料夹和接法卡.md) | ✅ Gemini 施工，Claude 按复查修过 |
| 51–53 | 2–4 | 没写单，Claude 直接做（体检、检测、安装） | ✅ |
| 54 | 5 | [54-积木库行政.md](54-积木库行政.md) | ✅ Gemini 施工，Claude 按复查修过 |
| 55 | 6 | [55-本机服务接口.md](55-本机服务接口.md) | ✅ Gemini 施工，Claude 按复查修过 |
| 56 | 7 前半 | [56-积木库页面上.md](56-积木库页面上.md) | ✅ Gemini 交付，Claude 验收修改 |
| 57 | 7 中 | [57-积木库提议卡.md](57-积木库提议卡.md) | ✅ Gemini 交付，Claude 验收修改 |
| 58 | 7 后半 | [58-体检卡和基础层详情.md](58-体检卡和基础层详情.md) | ✅ Gemini 交付，Claude 验收修改 |
| 59 | 8 上半 | [59-积木库窗口.md](59-积木库窗口.md) | ✅ Gemini 交付不通过、撤掉，Claude 重做 |
| 60 | 8 下半 | [60-存过的密钥免填.md](60-存过的密钥免填.md) | ✅ Claude 施工，按复查修过 |
| 61 | 沙盒接入 1 | [61-接法卡扩展和联网搜索积木.md](61-接法卡扩展和联网搜索积木.md) | ⏳ 待施工（Cursor），Codex 复查 |

后面的单写好后会加到这里。**一次只做表里“待施工”的单，做完就停。**

## 分支和提交

- 工作在本地分支 `blocks`，Claude 已经建好，验收测试也在上面。
- 一张单一个本地提交，标题写 `施工单 NN：标题`。
- **禁止：** `git push`、合并到其他分支、改写历史、删除分支。

## 每张单的步骤

读单 → 跑这张单的验收测试，确认失败 → 写代码 → 跑这张单的测试 → 跑下面的检查 → 写回执 → 提交。

## 必须跑的检查

测试节奏（用户 2026-11-08 定）：

- **每次修改后：** 只跑和这次改动相关的测试。
- **每张单提交前：** 全量跑一次，只用 Python 3.9。
- **发版本前：** Python 3.9 和 3.15 都全量跑。两个版本先后跑，不要同时跑（会抢同一个临时文件夹）。

```sh
# 这张单的验收测试（NN 换成单号）
python3 -m unittest discover -s tests -p 'test_acceptance_NN_*.py'
# 提交前：全量，Python 3.9
python3 -m unittest discover -s tests
# 发版本前再加：Python 3.15 全量
/opt/homebrew/bin/python3.15 -m unittest discover -s tests
# 改了 main.swift 才需要
cd desktop/mac && swift build
```

不能运行命令时，回执里写“未运行”，不许写“已通过”。

## 硬规矩

- **不准改验收测试。** 测试不合理，或者要过测试就得改单子以外的东西，必须停下，写进回执，告诉用户。不准绕过测试，不准放宽旧测试。
- **不准顺手改单子以外的东西。** 项目窗口、施工流程、团队设置这次都不动。
- **Claude 订阅不参与。** 代码里不出现 Claude 连接，测试和试跑都不启动 Claude。
- **不碰用户的真实资料。** 测试一律用临时目录，不写用户的积木库资料夹、钥匙串、`~/.codex`、`~/.agents`。
- **调用外部程序一律按参数列表执行，不经过 shell。** 不用 `shell=True`，不拼字符串命令。
- **同一个问题试两次不过：** 换方法。第三次还不过就停下，在回执里写清卡在哪。

## 回执

每张单做完，在 [回执.md](回执.md) 追加：

```markdown
## 单号 NN 标题
- 改了哪些文件：
- 这张单的测试：通过 / 失败（失败贴原文）
- 全量测试 Python 3.9：通过 / 失败 / 未跑
- Mac 编译：通过 / 失败 / 未跑 / 不涉及
- 和施工单不一样的地方：
- 拿不准的地方：
```

台账 `docs/开发进度跟踪.md` 由 Claude 更新，你不用改。

## 用户交给 Cursor 的话

> 读 `docs/积木库/施工单/README.md`，严格按它施工。确认当前在 `blocks` 分支，只做 50 号单，做完提交一次，写好回执后停下。测试不合理就停下告诉我，不准改测试。不要推送。

54 号单，交给 Gemini 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/54-积木库行政.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 54 号单：不要改 `desktop/clerk/roles/积木库.md`、`collab/blocks*.py` 和任何测试。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_54_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 54：积木库行政`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理就停下告诉我，不准改测试。不要推送。

55 号单，交给 Gemini 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/55-本机服务接口.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 55 号单：不要改 `collab/blocks*.py`、`desktop/clerk/tasks.py`、`desktop/clerk/blocks_tools.py`、`desktop/clerk/roles/` 和任何测试。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_55_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 55：积木库的本机服务接口`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送。

56 号单，交给 Gemini 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/56-积木库页面上.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 56 号单：项目窗口的页面文件（`chat.*`、`nav.*`、`panel.*`、`team.*`）、`collab/`、`desktop/clerk/` 和任何测试都不要改。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_56_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 56：积木库页面（上）`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

57 号单，交给 Gemini 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/57-积木库提议卡.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 57 号单：只改 `desktop/static/chat.js`、`desktop/static/chat.css`、`desktop/server.py`；项目窗口提议卡的显示和行为不能变；`blocks.*`、`nav.*`、`panel.*`、`team.*`、`collab/`、`desktop/clerk/` 和任何测试都不要改。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_57_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 57：积木库提议卡`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

58 号单，交给 Gemini 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/58-体检卡和基础层详情.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 58 号单：只改 `desktop/static/blocks.html`、`desktop/static/blocks.js`、`desktop/static/blocks.css`；`chat.*`、`nav.*`、`panel.*`、`team.*`、`desktop/server.py`、`collab/`、`desktop/clerk/` 和任何测试都不要改。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_58_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 58：体检卡和基础层详情`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

59 号单，交给 Gemini 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/59-积木库窗口.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 59 号单：只新建 `desktop/mac/Sources/SuperAgent/BlocksWindow.swift`，只改 `desktop/mac/Sources/SuperAgent/main.swift`、`desktop/static/nav.html`、`nav.js`、`nav.css`；`blocks.*`、`chat.*`、`panel.*`、`team.*`、`desktop/server.py`、`collab/`、`desktop/clerk/` 和任何测试都不要改。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_59_*.py'`，再跑 `desktop/mac/build.sh` 确认编译打包通过，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 59：积木库窗口`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。不要打开真实窗口去试（会弹钥匙串授权），走查由 Claude 做。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

## 61 号单起的流程（用户 2026-11-09 定）

Cursor 施工 → Codex 复查 → Cursor 按复查修（最多 3 轮，过不了停下报 Claude）→ Claude 最终验收。Claude 不参与中间的来回。

61 号单，交给 Cursor 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/61-接法卡扩展和联网搜索积木.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 61 号单：只改 `collab/blocks.py` 和 `collab/blocks_env.py`，别的文件和任何测试都不要改。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_61_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 61：接法卡扩展和联网搜索积木`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

61 号单，交给 Codex 复查的话（Cursor 提交后；模型 gpt-6.1-sol，推理 xhigh）：

> 你是复查员，只读不改。读 `docs/积木库/施工单/61-接法卡扩展和联网搜索积木.md`、`docs/积木库/沙盒接入/方案.md` 的技术设计，再看 `blocks` 分支最新一个提交（`git show HEAD`）。对照施工单逐条查：有没有没做到的、做错的、做多了的（改了单子以外的文件、为过测试写的特殊判断）；重点查安全规则能不能绕过（`secrets` 的名字和值、`probe` 的参数、`search` 卡能不能夹带地址或别的字段、老卡和预置卡的“核实过”判断有没有被放宽）；老卡和用户基础层里已有的卡还能不能原样读出和检测。自己跑 `python3 -m unittest discover -s tests -p 'test_acceptance_61_*.py'` 和全量测试（先查有没有残留的测试进程）。结论写成：通过 / 不通过，每个问题写“在哪、什么情况下出错、怎么改”，按严重程度排序。不要改任何文件。
