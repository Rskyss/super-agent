# 积木库施工规矩（写给 Cursor）

用户把这份目录交给你施工。Claude 写施工单和验收测试，你写代码，Claude 验收。

## 开工前必读

1. 本文件。
2. [需求共识](../需求共识.md)、[方案](../方案.md)（尤其是末尾的“技术设计”）、[任务清单](../任务清单.md)。61 号单起是“沙盒接入”这一轮，另读 [沙盒接入/需求共识](../沙盒接入/需求共识.md)、[沙盒接入/方案](../沙盒接入/方案.md)、[沙盒接入/任务清单](../沙盒接入/任务清单.md)，两边冲突以沙盒接入为准。66 号单起是“能力层”这一轮，另读 [能力层/需求共识](../能力层/需求共识.md)、[能力层/方案](../能力层/方案.md)、[能力层/任务清单](../能力层/任务清单.md)。
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
| 61 | 沙盒接入 1 | [61-接法卡扩展和联网搜索积木.md](61-接法卡扩展和联网搜索积木.md) | ✅ Claude 施工（用户 2026-11-09 改：一口气做完） |
| 62–65 | 沙盒接入 2–5 | 没写单，Claude 直接做；验收测试 `test_acceptance_62_*` 到 `65_*`、`tests/sandbox_page_check.js` | ✅ |
| 66 | 能力层 1 | [66-能力卡片格式.md](66-能力卡片格式.md) | ✅ Gemini 3.8 Flash 施工，Codex 复查 3 轮通过，Claude 验收 |
| 67 | 能力层 2 | [67-能力卡片运行.md](67-能力卡片运行.md) | 待施工 |

后面的单写好后会加到这里。**一次只做表里“待施工”的单，做完就停。**

## 分支和提交

- 工作在本地分支 `blocks`，Claude 已经建好，验收测试也在上面。
- 一张单一个本地提交，标题写 `施工单 NN：标题`。
- **禁止：** `git push`、合并到其他分支、改写历史、删除分支。

## 每张单的步骤

读单 → 跑这张单的验收测试，确认失败 → 写代码 → 跑这张单的测试 → 跑下面的检查 → 写回执 → 提交。

## 必须跑的检查

测试节奏（用户 2026-11-08 定；2026-11-11 用户让 Claude 按开发阶段把控，保证质量）：

| 阶段 | 谁跑 | 跑什么 |
|---|---|---|
| 施工中每次修改后 | 施工方 | 这张单的测试，加上改到的那部分的相关测试（见下） |
| **第一次交付前** | 施工方 | 这张单的测试 + **全量**（Python 3.9） |
| 复查后修改（第 2、3 轮） | 施工方 | 这张单的测试 + 相关测试；**这一轮改到了共用文件就跑全量**（见下） |
| 复查（每一轮） | Codex | 这张单的测试 + 相关测试；**判“通过”之前必须跑一次全量**，没跑全量不能判通过 |
| 最终验收 | Claude | 复查通过之后还有新提交就重跑全量，没有就核对 Codex 那次全量的结果 |
| 发版本前 | Claude | Python 3.9 和 3.15 都全量 |

- **共用文件**（很多功能都靠它，改了容易牵连别处）：`collab/blocks.py`、`collab/config.py`、`collab/agents.py`、`desktop/server.py`、`desktop/clerk/` 下的文件、`desktop/static/chat.js`、`desktop/mac/` 下的文件。改到这些，那一轮就跑全量。
- **积木库的相关测试**（几十秒，比全量快得多）：
  ```sh
  python3 -m unittest discover -s tests -p 'test_acceptance_[56]*.py'
  python3 -m unittest discover -s tests -p '*blocks*.py'
  ```
  每张单可以在单子里另写它的相关测试，以单子为准。
- **同一时间只能跑一份全量**，两份会抢同一个临时文件夹，结果不准。跑之前先 `pgrep -fl unittest` 看有没有别人在跑。Python 3.9 和 3.15 也要先后跑。
- 回执里写清这次跑的是哪一档（这张单 / 相关 / 全量），不能把“相关测试过了”写成“全量过了”。

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

**复查的收口规矩（2026-11-11 Claude 作为 PM 定，用户问“会不会无限循环”）：**

- **最多 3 轮**复查。第 3 轮还不通过，交给 Claude：真会出事的自己小改，碰不到的边角情况记为已知取舍放行，不开第 4 轮。
- **每一轮缩小范围**：第 2 轮起，复查员只核对上一轮要求改的那几条，加上这一轮的改动有没有改坏别处，不再全面重查。
- **通过标准**：没有严重问题，也没有正常使用中会碰到的中等问题，就判“通过”。小问题、碰不到的边角情况写在“建议”里，不拦着放行；改不改由 Claude 最终验收时定。
- 复查员报的问题，Claude 按“真会不会出事、修的代价”筛一遍再交施工方，不是每条都改。

Cursor 施工 → Codex 复查 → Cursor 按复查修（最多 3 轮，过不了停下报 Claude）→ Claude 最终验收。Claude 不参与中间的来回。

61 号单，交给 Cursor 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/61-接法卡扩展和联网搜索积木.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 61 号单：只改 `collab/blocks.py` 和 `collab/blocks_env.py`，别的文件和任何测试都不要改。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_61_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 61：接法卡扩展和联网搜索积木`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

61 号单，交给 Codex 复查的话（Cursor 提交后；模型 gpt-6.1-sol，推理 xhigh）：

> 你是复查员，只读不改。读 `docs/积木库/施工单/61-接法卡扩展和联网搜索积木.md`、`docs/积木库/沙盒接入/方案.md` 的技术设计，再看 `blocks` 分支最新一个提交（`git show HEAD`）。对照施工单逐条查：有没有没做到的、做错的、做多了的（改了单子以外的文件、为过测试写的特殊判断）；重点查安全规则能不能绕过（`secrets` 的名字和值、`probe` 的参数、`search` 卡能不能夹带地址或别的字段、老卡和预置卡的“核实过”判断有没有被放宽）；老卡和用户基础层里已有的卡还能不能原样读出和检测。自己跑 `python3 -m unittest discover -s tests -p 'test_acceptance_61_*.py'` 和全量测试（先查有没有残留的测试进程）。结论写成：通过 / 不通过，每个问题写“在哪、什么情况下出错、怎么改”，按严重程度排序。不要改任何文件。

66 号单，交给 Cursor 的话：

> 读 `docs/积木库/施工单/README.md` 和 `docs/积木库/施工单/66-能力卡片格式.md`，严格按它施工。确认当前在 `blocks` 分支、工作区是干净的。只做 66 号单：只新建 `collab/abilities.py`，别的文件和任何测试都不要改。做完先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_66_*.py'`，再跑全量 `python3 -m unittest discover -s tests`，都过了再提交一次（标题 `施工单 66：能力卡片格式`），把回执写进 `docs/积木库/施工单/回执.md` 并一起提交，然后停下。测试不合理或两条测试互相矛盾就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

66 号单，交给 Codex 复查的话（Cursor 提交后；模型 gpt-6.1-sol，推理 xhigh）：

> 你是复查员，只读不改。读 `docs/积木库/施工单/66-能力卡片格式.md`、`docs/积木库/能力层/方案.md` 的技术设计，再看 `blocks` 分支最新一个提交（`git show HEAD`）。对照施工单逐条查：有没有没做到的、做错的、做多了的（改了单子以外的文件、为过测试写的特殊判断）；重点查：模板能不能夹带没声明的占位或做第二次替换、编号和运行编号能不能拼出别的文件夹、密钥能不能写进卡片或记录、`ensure_builtin` 会不会覆盖用户改过的或移出的卡、能力卡片会不会混进基础层。自己跑 `python3 -m unittest discover -s tests -p 'test_acceptance_66_*.py'` 和全量测试（先查有没有残留的测试进程）。结论写成：通过 / 不通过，每个问题写“在哪、什么情况下出错、怎么改”，按严重程度排序。不要改任何文件。

67 号单，交给 Gemini 的话：

> 读 `docs/积木库/施工单/README.md`（尤其“测试节奏”“复查的收口规矩”）和 `docs/积木库/施工单/67-能力卡片运行.md`，严格按它施工。确认当前在 `blocks` 分支、工作区除了根目录的 `未命名绘图.drawio` 是干净的。只做 67 号单：新建 `collab/abilities_run.py`，`collab/blocks_web.py` 只改 `search` 一个函数，`collab/abilities.py` 只改写文章卡片的 `prompt` 一处，别的文件和任何测试都不要改。先跑 `python3 -m unittest discover -s tests -p 'test_acceptance_67_*.py'` 和 `'test_acceptance_66_*.py'`，再跑全量 `python3 -m unittest discover -s tests`（先 `pgrep -fl unittest` 确认没有别的测试在跑），都过了提交一次（标题 `施工单 67：能力卡片运行`，不要提交 `未命名绘图.drawio`），回执写进 `docs/积木库/施工单/回执.md` 一起提交，如实写“和施工单不一样的地方”“拿不准的地方”，然后停下。测试不合理或互相矛盾就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，施工中别在项目文件夹里留下试验文件。

67 号单，交给 Codex 复查的话（第 1 轮；Gemini 提交后）：

> 你是复查员，只读不改。读 `docs/积木库/施工单/README.md` 的“测试节奏”“复查的收口规矩”、`docs/积木库/施工单/67-能力卡片运行.md`、`docs/积木库/能力层/方案.md` 技术设计“运行”，看 Gemini 那次提交（标题“施工单 67：能力卡片运行”，`git log --oneline` 找到后 `git show <编号>`）和它追加的回执。对照施工单逐条查：没做到的、做错的、做多了的（改了单子以外的文件或函数、为过测试写的特殊判断）。重点查：密钥会不会出现在返回结果、记录、成果文件里；命令行工具的参数是不是只来自积木块的接法、交代整段作为一个参数、不经过 shell；临时文件夹是不是一定删；去 Codex 图片文件夹找图时会不会拿到运行前就在的图、会不会读到别处的文件；会不会自动换模型；同一张卡同时只跑一次、出错后会不会一直卡在“正在跑”；`blocks_web.search` 老的叫法是不是完全不变。先 `pgrep -fl unittest` 确认没有别的测试在跑，再跑 `python3 -m unittest discover -s tests -p 'test_acceptance_67_*.py'`、`'test_acceptance_66_*.py'` 和全量 `python3 -m unittest discover -s tests`。**通过标准**：没有严重问题、也没有正常使用中会碰到的中等问题就判“通过”；小问题和碰不到的边角情况写在“建议”里。结论写成：通过 / 不通过，每个问题写“在哪、什么情况下出错（能复现的写出复现办法）、怎么改、严重程度”，按严重程度排序；回执每一句是否属实。不要改任何文件。

67 号单，交给 Gemini 的话（第 2 轮修改；Codex 第 1 轮不通过）：

> 读 `docs/积木库/施工单/README.md`（尤其“测试节奏”“复查的收口规矩”）和 `docs/积木库/施工单/67-能力卡片运行.md` 新加的“第 2 轮补充”7 条，照它改。只改 `collab/abilities_run.py`，别的文件和任何测试都不要改。测试 `tests/test_acceptance_67_ability_run.py` 末尾的 `Round2` 是这轮新加的 10 项，现在全不过。改完跑 `python3 -m unittest discover -s tests -p 'test_acceptance_67_*.py'`（48 项）和 `'test_acceptance_66_*.py'`（85 项），都过了提交一次（标题 `施工单 67：复查第 1 轮修改`，不要提交 `未命名绘图.drawio`）；这轮只动这一个文件，不用跑全量。回执追加到 `docs/积木库/施工单/回执.md`，逐条写 7 条各怎么改的，如实写“和施工单不一样的地方”“拿不准的地方”，上一轮回执里“只保存合格成果”那句写明已按施工单改正，然后停下。测试不合理或互相矛盾就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，别在项目文件夹里留下试验文件。

67 号单，交给 Codex 复查的话（第 2 轮；Gemini 第 2 轮提交后，接着第 1 轮那个对话发）：

> 第 2 轮复查，只读不改。Gemini 按你第 1 轮的 6 条和建议改了，Claude 把要求写成了施工单 67 的“第 2 轮补充”7 条，并在测试末尾加了 `Round2` 10 项。看最新提交（`git show HEAD`）和回执新追加的部分，只核对这 7 条是否做到、这轮改动有没有改坏别处，不再全面重查。先 `pgrep -fl unittest` 确认没有别的测试在跑，再跑 67、66 号单测试和全量 `python3 -m unittest discover -s tests`（判通过前必须跑全量）。通过标准和结论写法同第 1 轮。不要改任何文件。

67 号单，交给 Gemini 的话（第 3 轮修改，最后一轮；Codex 第 2 轮不通过）：

> 读 `docs/积木库/施工单/67-能力卡片运行.md` 新加的“第 3 轮补充”5 条，照它改。只改 `collab/abilities_run.py`，别的文件和任何测试都不要改。测试末尾 `Round3` 是这轮新加的 2 项，现在不过；第 1、2 条测试不好模拟，复查员会读代码和自己造异常核对，一样要做到。改完跑 `python3 -m unittest discover -s tests -p 'test_acceptance_67_*.py'`（50 项）和 `'test_acceptance_66_*.py'`（85 项），都过了提交一次（标题 `施工单 67：复查第 2 轮修改`，不要提交 `未命名绘图.drawio`），不用跑全量。回执追加到 `docs/积木库/施工单/回执.md`，逐条写 5 条各怎么改的；“和施工单不一样的地方”“拿不准的地方”如实写，上一轮写“无”被复查判不成立，这轮没有把握的异常情况要写出来。然后停下。测试不合理就停下告诉我，不准改测试，也不准为了过测试写特殊判断。不要推送，别在项目文件夹里留下试验文件。

67 号单，交给 Codex 复查的话（第 3 轮，最后一轮；Gemini 第 3 轮提交后，接着同一个对话发）：

> 第 3 轮复查（最后一轮），只读不改。看最新的代码提交和回执新追加部分，只核对施工单 67“第 3 轮补充”5 条是否做到，用你上一轮的复现办法再试一遍（读密钥检查出错、图片清单出错、查结果出错、文字卡文件读不了、空文字），再看这轮改动有没有改坏别处。先 `pgrep -fl unittest`，再跑 67、66 号单测试和全量（判通过前必须跑全量）。通过标准同前：碰不到的边角情况写进“建议”，不作为不通过的理由。不要改任何文件。
