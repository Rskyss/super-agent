# 施工单 B：右栏显示调整（跑卡流程调整 · 第一批）

> 依据 [方案-第一批](方案-第一批.md)（用户 2026-11-17 批准）。写给开发助手的施工方（写测试、写代码、复查）。
> 项目就是开发助手这个仓库。**验收测试已经写好**，施工方**不许改**；写测试的人只**补充**，写进新文件 `tests/test_flowfix_b_extra*.py`。
> **这次的原则：有增有删。** 被新写法取代的旧函数、旧拼法要删掉，不许新旧两套并存。

## 一、要做什么（一句话）

右栏每个环节固定三层（名字和谁在做 / 一句结果 / 小字“轮数 · 累计用时 · 累计用量”），首次显示和刷新只用一套拼法；本卡合计单独一行、细项进鼠标提示；“通过 x/y”把事先写好的验收测试也算上；让干活的人汇报时不写文件名。

## 二、允许改哪些文件

- `desktop/view.py`
- `desktop/static/panel.js`
- `desktop/static/panel.css`
- `collab/dispatch.py`
- `collab/progress.py`
- `tests/test_flowfix_b_extra*.py`（写测试的人新建）

**不许改**：`tests/test_flowfix_b_display.py`、`tests/flowfix_b_panel_check.js`，以及 Claude 已按新口径改好的四个老测试 `tests/test_token_usage_acceptance.py`、`tests/test_token_usage_extra.py`、`tests/token_usage_panel_check.js`、`tests/test_flowfix2_display.py`；上面没列的任何文件。

## 三、具体做法

### 1. `desktop/view.py`：每个环节给出轮数、累计用时、每轮明细

在 `card_view` 给写测试、写代码、复查三个环节（`test`/`dev`/`rev`）设置状态的那段循环里：

- 取这个角色的全部“回复”记录（`kind == "回复"` 且 `src` 是这个角色），按顺序。
- 新增字段：
  - `history`：每条回复一项 `{"seconds": 回复里的 seconds 或 None, "tokens": 回复里 tokens 的 total（tokens 是字典且 ok 为真时）或 None}`；
  - `spent`：各回复 `seconds` 之和；**一条都没有 seconds 时为 None**（不编 0）；
  - `rounds`：交了活的是回复条数；正在跑（或上次中断在这一步）是回复条数 + 1。
- **交了活的环节**：`detail` 只放最后一条回复的 `summary`，**删掉**现在拼上去的 `" · 用了 m:ss"`（`took` 那一行）。
- 没开始的环节（idle）不加这三个字段。
- 写方案、跑测试、确认这几个环节不动。

### 2. `desktop/static/panel.js`：一套拼法

在 `PanelCore` 里：

- **新增** `metaText(s, since)`：第三行文字。`since` 是距上次取数的秒数（不传当 0）。各段用 `" · "` 连接，**空段不出现**：
  - `idle` 或 `s` 为空：`""`；
  - `active`：`rounds > 1` 时先写 `第 N 轮`；`elapsed` 不为空写 `已干 m:ss`（`elapsed + since`）；`quiet` 不为空写 `N 秒前有动静` / `N 分钟前有动静`（`quiet + since`，规则同现有 `ago`）；再接用量段；
  - `done`、`fail`：`rounds > 1` 时先写 `共 N 轮`；`spent` 不为空写 `m:ss`；再接用量段；
  - `wait` 等其他状态：只写用量段；
  - 用量段：没有 `tokens` 不写；`ok` 为假写 `用量 —（原因）`；否则写 `N token`（数字格式沿用 `tokenAmount`，**不带“已用”**），有 `missing` 时后面加 `（有 N 轮没读到）`。
- **新增** `stepTitle(s)`：鼠标提示，按行拼：`cleanDetail(detail)`；`rounds > 1` 时每条 `history` 一行 `第 i 轮 m:ss · N token`（没有用时或用量的段省掉）；最后接 `tokenTitle(tokens)`（为空不接）。空行不出现。
- **新增** `totalTitle(v)`：`other > 0` 时写 `其中交接资料等其他 N token`，否则 `""`。
- **改** `totalText(v)`：去掉括号里的“其中交接资料等其他”，只写 `本卡合计 N token`。
- **删掉** `tokenText`；把 `mmss`、`ago` 挪到 `PanelCore` 外层和 `tokenAmount` 并排（页面里别处还在用就改为调用这一份，**不许留两份**）；**删掉**闭包里的 `clockText`。

页面部分：

- `renderFlow`：`.k` 写 `kind === "idle" ? "" : PanelCore.metaText(s, since())`；`b.title` 写 `PanelCore.stepTitle(s)`；合计元素 `textContent` 写 `totalText`、`title` 写 `totalTitle`。
- `tick`：正在跑的环节 `.k` 写 `PanelCore.metaText(s, since())`——**和 renderFlow 用同一个函数**，删掉现在 tick 里自己拼的那一套。

### 3. `desktop/static/panel.css`

- `.step .d` 的 `-webkit-line-clamp` 由 3 改成 2。
- `.token-total`：上面加一条细分隔线（用文件里已有的线条颜色变量），和环节列表之间、线和文字之间都留出空白，不显得挤。

### 4. `collab/dispatch.py`

- `_REPORT_ASK` 改成要求“汇报”用大白话、**不写文件名、函数名和命令**（必须含“不写文件名”这几个字），其余意思不变。
- 创建 `ProgressJob` 时多传 `test_command=_command_text(test_command) if test_command else ""`。

### 5. `collab/progress.py`：“通过 x/y”按本卡测试命令算

- `ProgressJob.__init__` 加参数 `test_command: str = ""`。
- `compute()`：本卡测试命令按 `&&` 拆开后，**每一段都是** `python3 -m unittest ...`（或 `python -m unittest ...`）时，在副本里逐段跑、用现有 `tally` 相加；超时、跑不起来、导入就报错的处理和现在一样。**否则**（没有命令、或有别的程序）照现在的办法只跑写测试交出的文件。

## 四、不做什么

- 不改复查红叉、确认弹窗、“以后再修”（第二批）。
- 不改跑卡规矩、行政说的话、左栏和中间对话区。
- 不改正在跑时第二行的实时进展文字。
- 不新增文件（测试除外）；不改上面“允许改”以外的文件。

## 五、怎么算做完

- 验收命令全部通过；
- 首次显示和刷新的第三行永远一致，没有多余的“·”；
- 被取代的旧函数、旧拼法已删除（`tokenText`、`clockText`、tick 里那套、说明里的“用了”）；
- 程序跑的全部测试不能因为这次改动变坏。

## 六、验收命令

```
python3 -m unittest discover -s tests -p "test_flowfix_b*.py" && python3 -m unittest discover -s tests -p "test_token_usage*.py"
```

（改动前在现有代码上跑：新测试 13 项里 11 项不过，老测试 4 项因口径更新而不过，已确认红在该红的地方。）
