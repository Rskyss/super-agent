# 施工单 B：token 用量第二版——行政对话累计

> 依据 [方案](方案.md)（行政部分）、[任务清单](任务清单.md) T3/T4。写给开发助手的施工方。项目就是开发助手这个仓库。
> **验收测试已写好**：`tests/test_token_b_acceptance.py`、`tests/token_b_chat_check.js`，施工方**不许改**；补充测试写进新文件 `tests/test_token_b_extra*.py`。
> 已核实：对话页顶部本来就有显示用量的位置（`chat.html` 的 `id="usage"`），对话页收到 `type: "usage"` 的事件就会记下数字；行政模式和旧聊天共用这一套，只是行政从来没送过这个事件。

## 一、要做什么（一句话）

行政每次调模型（回答时的每个来回、起标题那一次）拿到的用量加到这段对话上，送给页面显示在顶部，并存进对话记录，重开后还在、接着往上加。

## 二、允许改哪些文件

- `desktop/clerk/desk.py`
- `desktop/static/chat.js`
- `tests/test_token_b_extra*.py`（写测试的新建）

**不许改**：两个验收测试文件、`tests/acceptance_helpers.py`，以及上面没列的文件。

## 三、具体做法

### 1. 累计（`desktop/clerk/desk.py`）

- 每段对话在内存里有一个累计数（开始是没有，不是 0）。
- 每次 `client.chat(...)` 返回后（主对话循环里每个来回，和 `_name` 起标题那一次），返回里的 `tokens` 是正整数就加上；没有、不是数、不大于 0 都不加。
- 加过以后：`conv.emit({"type": "usage", "total": 累计数})`，并用 `_record_line` 写一行 `{"role": "usage", "total": 累计数}` 进对话文件。
- **只有真有数才发事件**：一次都没拿到用量的对话，不发 `usage` 事件（页面就不显示，不写 0）。

### 2. 重开（同一文件）

- `_read_lines`：认 `role == "usage"` 且 `total` 是正整数的行，读成 `("usage", total)`。
- `_conversation` 读回时：遇到 `usage` 行，把累计数设成它（最后一行为准），**不**放进 `conv.messages`（模型看不到），在读完后发一个 `{"type": "usage", "total": 累计数, "history": True}` 事件。
- 重开后接着聊，从读回的数往上加。

### 3. 显示（`desktop/static/chat.js`）

- `ChatCore` 加 `headerUsage(total)`：没有数或不大于 0 → `""`；否则 `"本段对话 <数> token"`，数字格式和右栏一致（不到 1 万写原数带千分位；1 万到 1 亿写“N.N 万”；1 亿以上写“N.N 亿”）。导出到 `ChatCore`（和现有 `usageText` 一样）。
- 顶部那一处（现在是 `ChatCore.usageText(model.usage).replace(...)`）改用 `headerUsage(model.usage)`。旧聊天的 `usageText` 不动。

## 四、不做什么

不做行政的“改要求拆分”“连通检测”的用量（量极小，方案已定不做）；不改卡片右栏；不折钱；不改行政调模型的方式。

## 五、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_token_b*.py" && python3 -m unittest discover -s tests -p "test_acceptance_03_clerk_tools.py" && python3 -m unittest discover -s tests -p "test_acceptance_05_proposal.py" && python3 -m unittest discover -s tests -p "test_acceptance_22_card_chat.py" && python3 -m unittest discover -s tests -p "test_desktop_chat_page.py"`
- 两个验收测试文件全过；全部测试不因这次改动变坏。
- 老测试如果有因新口径必须变的，**不许改**，在汇报里写明是哪一个，由 Claude 判断。
- 施工方只跑验收命令，不要跑全部测试。
- 汇报用大白话，不写文件名。
