# 施工单 A：右栏显示每个环节的 token 用量

> 依据 [方案](方案.md)、[任务清单](任务清单.md)（用户 2026-11-17 批准）。写给开发助手的施工方（写测试、写代码、复查）。
> 项目就是开发助手这个仓库。**验收测试已经写好**，施工方**不许改**；写测试的人只**补充**，写进新文件 `tests/test_token_usage_extra*.py`。

## 一、要做什么（一句话）

开发助手跑卡时，右栏每个环节（写方案、写测试、写代码、复查）显示**累计用了多少 token**，在跑时随时涨、跑完定格，环节列表下面显示**本卡合计**。**不显示钱。**

## 二、数据从哪来（已核实）

- Codex（含千问，千问也是 Codex 的壳）每次运行，输出头部有一行 `session id: <会话编号>`（样本见 `tests/token_samples/live_header.log`）。
- Codex 把会话记录存在 `$CODEX_HOME/sessions/年/月/日/rollout-<时间>-<会话编号>.jsonl`（`CODEX_HOME` 没设时是 `~/.codex`）。
- 记录里每完成一轮对话追加一行 `{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{...}}}}`，`total_token_usage` 是**到目前为止的累计**：`input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`、`total_tokens`。`info` 可能是 `null`（还没完成一轮）。
- Codex 自己的辅助会话（自动审查）是另一个文件：第一行 `session_meta` 里 `session_id` 等于主会话编号、`id` 是它自己的编号。
- 样本说明见 `tests/token_samples/README.md`。

## 三、允许改哪些文件

- `collab/token_usage.py`（**新建**）
- `collab/dispatch.py`
- `collab/planner.py`
- `desktop/view.py`
- `desktop/static/panel.js`
- `desktop/static/panel.html`
- `desktop/static/panel.css`（只在需要样式时改）
- `tests/test_token_usage_extra*.py`（写测试的人新建）

**不许改**：`tests/test_token_usage_acceptance.py`、`tests/token_usage_panel_check.js`、`tests/token_samples/` 下任何文件，以及上面没列的任何文件。

## 四、具体做法

### 1. 新建 `collab/token_usage.py`（纯读取，任何情况都不向外抛异常）

| 函数 | 做什么 |
|---|---|
| `codex_home()` | 环境变量 `CODEX_HOME`，没有就 `~/.codex` |
| `session_id_from_text(text)` | 从文本里找 `session id: <编号>`（行首），找不到返回 `""` |
| `find_session_file(uuid)` | 在 `codex_home()/sessions` 下找文件名以 `-<uuid>.jsonl` 结尾的文件；先看最近两天的日期文件夹，没有再整个找；找到的路径缓存起来；找不到返回 `None` |
| `read_total(path)` | **从文件尾部往前读**（先读最后 256 KB，没找到再翻倍往前，直到读完整个文件），找**最后一条完整的** `token_count` 且 `info.total_token_usage` 存在的那行；返回 `{"ok": True, "total", "input", "cached", "output", "reasoning"}`（分别对应 `total_tokens`、`input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`）。读不到（文件不存在、是文件夹、没有用量、格式不认识、坏行）返回 `{"ok": False, "reason": "<大白话原因>"}` |
| `children_total(uuid)` | 在主会话所在的日期文件夹里，只读每个文件的**第一行**，找 `session_meta` 里 `session_id == uuid` 且 `id != uuid` 的文件，把它们 `read_total` 的 `total` 加起来；读不到的不算；返回整数 |
| `usage_from_text(text)` | `session_id_from_text` → `find_session_file` → `read_total`，成功时再加 `"children": children_total(uuid)`；没有会话编号时返回 `{"ok": False, "reason": "输出里没有会话编号"}`；找不到文件时返回 `ok False` 和原因 |
| `usage_from_log(path)` | 只读实时输出文件开头 16 KB，交给 `usage_from_text`；文件不存在返回 `ok False` |
| `new_tokens(t)` | `t["input"] - t["cached"] + t["output"]`（等于 Codex 自己打印的 `tokens used`） |
| `sum_tokens(items)` | 空列表返回 `None`；否则把 `ok` 为真的各项的 `total、input、cached、output、reasoning、children` 相加，`missing` = `ok` 为假的个数，`ok` = 至少有一项为真；全都为假时返回 `{"ok": False, "missing": N, "reason": 第一项的原因}` |

### 2. 记录（`collab/dispatch.py`、`collab/planner.py`）

- `_dispatch`：`ask_fn` 返回后，**只当 `adapter` 是 `codex` 或 `qwen`**，算 `tokens = usage_from_text(result.output)`；不 `ok` 时再试一次 `usage_from_log(实时输出文件)`；把 `tokens=` 加进“回复”那条记录。`agy`、`cursor` 不加这个字段。**算用量出任何问题都不能影响这一步的结果和后续流程。**
- `_make_handover`：同样算一次，把 `tokens=` 加进两种“交接资料”记录（备成和没备成）。
- `planner.plan_card`：Codex 成功返回后，用 `usage_from_text(tool_text)` 算一次，把 `tokens=` 加进“方案”那条记录。

### 3. 右栏取数（`desktop/view.py`）

在 `card_view` 里 `_add_models(...)` 后面加 `_add_tokens(stations, events, cdir)`，并在构造 `view` 字典时加上 `tokens={"total": …, "other": …}`（两个返回点都要有，所以在构造 `view` 时就放进去）。

- **写方案站（`disp`）**：所有“方案”记录的 `tokens` 用 `sum_tokens` 相加。
- **写测试/写代码/复查站（`test`/`dev`/`rev`）**：“回复”记录里 `src` 是 `测试员`/`开发员`/`复查员` 的，各自相加；
  - `tool` 是 `codex`/`qwen` 但记录里没有 `tokens`（老卡）→ 当作 `{"ok": False, "reason": "这一步没有用量记录"}` 参与相加；
  - `tool` 是 `agy`/`cursor` → 跳过；
  - **正在跑**：某条“派活”（`dst` 是这个角色、`tool` 是 `codex`/`qwen`、有 `live`）还没有对应的“回复”（没有哪条回复的 `reply_to` 等于它的 `seq`）→ 用 `usage_from_log(cdir / live)` 现读，一起相加。
  - 一项都没有 → 站位里**不放** `tokens` 字段（界面什么都不显示）。
- **其他（`other`）**：“交接资料”记录的 `total`、`src` 不是上面三个角色的“回复”（比如“起草”）的 `total`、以及所有用量里的 `children`，加起来。
- **本卡合计 `total`**：四个站位的 `total` 加上 `other`。没有任何数据时两项都是 0。
- 站位字典**只新增 `tokens` 字段，不改已有字段**。

### 4. 右栏显示（`desktop/static/panel.js`、`panel.html`）

在 `PanelCore` 里加三个函数（格式必须和 `tests/token_usage_panel_check.js` 完全一致）：

- `tokenText(t)`：`已用 220.8 万 token`；不到 1 万写原数带千分位（`已用 8,512 token`），1 亿以上写 `N.N 亿`；读不到写 `用量 —（原因）`；`null`/`undefined` 写空；有 `missing` 时后面加 `（有 N 轮没读到）`。
- `tokenTitle(t)`：`输入 217.8 万（其中缓存读取 208.3 万）· 输出 3.0 万 · 新消耗 12.5 万（不含缓存读取）`；读不到或空写空。
- `totalText(v)`：`本卡合计 540.0 万 token（其中交接资料等其他 2.0 万）`；`other` 为 0 时不写括号；`null` 或合计为 0 写空。

`renderFlow` 里：

- 计时那一行（`.k`）：原来的计时文字后面接 ` · ` 和 `PanelCore.tokenText(s.tokens)`；只有用量没有计时（已结束的环节）时只写用量；
- 鼠标提示（`b.title`）：原来的内容后面换行接 `PanelCore.tokenTitle(s.tokens)`（为空就不接）；
- 页面上 `<ol class="steps" id="steps"></ol>` 后面加 `<p class="token-total" id="tokenTotal" hidden></p>`，每次刷新写 `PanelCore.totalText(v.tokens)`，为空就隐藏。

## 五、不做什么

- 不显示钱、价格、费用；不做预算上限。
- 不做行政对话的用量（那是 B 卡）。
- 不做 agy、Cursor 的用量。
- 不补算历史卡；不做汇总统计页。
- “写方案”不做实时（它跑完才显示）。
- 不改任何影响 token 消耗的设置。
- 不改上面“允许改”以外的文件；不改验收测试和样本。

## 六、怎么算做完

- 验收命令全部通过；
- 读取模块对任何坏输入都不抛异常；
- 算用量出错不影响卡片流程（验收测试里有这一项）；
- 程序跑的全部测试不能因为这次改动变坏。

## 七、验收命令

```
python3 -m unittest discover -s tests -p "test_token_usage*.py"
```

（会同时跑 Claude 写的验收测试、写测试的人补的测试，以及右栏显示的 node 检查。改动前在现有代码上跑：23 项里 21 项不过，已确认红在该红的地方。）

---

## 可直接粘贴给行政的话

```
按施工单做：/Users/rs/Desktop/project/开发助手/docs/token用量/施工单-A.md
要做什么：开发助手跑卡时，右栏每个环节（写方案、写测试、写代码、复查）显示累计用了多少 token，在跑时随时涨、跑完定格；环节列表下面显示本卡合计。只显示 token，不显示钱。
不做什么：不做行政对话的用量；不做 agy 和 Cursor；不补算历史卡；不做汇总页；不改验收测试和测试样本。
怎么算做完：验收命令全部通过；算用量出错不影响卡片流程；全部测试不因这次改动变坏。
允许改哪些文件：collab/token_usage.py（新建）、collab/dispatch.py、collab/planner.py、desktop/view.py、desktop/static/panel.js、desktop/static/panel.html、desktop/static/panel.css、tests/test_token_usage_extra*.py（新建）
验收命令：python3 -m unittest discover -s tests -p "test_token_usage*.py"
```
