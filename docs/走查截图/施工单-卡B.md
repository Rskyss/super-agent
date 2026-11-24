# 施工单 卡B：卡片里显示走查截图（含只读看图接口与大图对照）

> 依据 [方案](方案.md)（用户 2026-11-21 批准）、[任务清单](任务清单.md) B-1。
> 拆卡判断：本单是第 2/2 张，前一张：卡 A（CARD-022，已放进项目 f465e71，并已重装应用生效）。
> 写给开发助手的施工方。**验收测试已经写好**（`tests/test_walk_card_b_acceptance.py` 与 `tests/walk_card_b_dom.js`），**走查脚本已经写好**（`tests/walk_卡B.py`），施工方都**不许改**；写测试的只**补充**，写进新文件 `tests/test_walk_card_b_extra*.py` 和 `tests/walk_card_b_extra*.js`。**只按本施工单做，没要求的功能、写法不要自己加。**

## 一、要做什么（一句话）

卡 A 跑完走查的卡片，在中间卡片原文下面多一块“走查截图”（默认展开，显示这次交活最后一次通过的走查）：头部写清张数和收起/展开按钮，下面每一步一行显示步骤名和“本次截图”（有设计图时左右并排“设计图”），点任何一张图弹出大图浮层查看并支持关闭；后端新增只读看图接口，只放行本卡走查截图目录里的 png 和项目内（或卡片副本内）的 png；没走查的卡不显示这一块，中间区界面和现在一模一样。

## 二、允许改哪些文件

- `desktop/view.py`
- `desktop/server.py`
- `desktop/static/chat.js`
- `desktop/static/chat.css`
- `tests/test_walk_card_b_extra*.py`
- `tests/walk_card_b_extra*.js`

**不许改**：`desktop/static/chat.html`、`desktop/static/panel.js`、`desktop/static/panel.css`、`tests/test_walk_card_b_acceptance.py`、`tests/walk_card_b_dom.js`、`tests/walk_卡B.py`、`tests/walk_tool.py`、`tests/test_walk_tool.py`、`tests/fixtures/walk_cards/` 下所有文件、`agents/` 下所有文件，以及上面没列的任何文件。

## 三、具体做法

动手前先看第四节状态表和第五节原有行为清单。

### P1 `card_view` 返回本次交活的走查截图列表与共用路径校验（`desktop/view.py`）

- **共用只读路径解析函数**（供 P1 的 `card_view` 和 P2 的 `/api/clerk/card/image` 共用，保证安全边界与取图优先级 100% 一致）：在 `desktop/view.py` 中新增 `resolve_walk_image(project, card_id, kind, rel) -> Path | None`：
  - **项目与卡片绑定**：`project` 必须非空且为现存目录，`card_id` 必须非空且匹配 `^CARD-\d{3,}$`。令 `proj_real = Path(project).resolve()`，要求 `collab_dir = proj_real / ".collab"`、`cards_dir = collab_dir / "cards"`、`cdir = cards_dir / card_id` 均存在为目录、且**三者自身均不是软链接**（`not p.is_symlink()`），且 `(cdir / "状态.json").is_file()`（绑定到真实已接入项目的真实卡片，否则直接返回 `None`）。
  - **相对路径初筛**：`rel = str(rel or "").strip()` 必须非空、不以 `/` 开头、`not Path(rel).is_absolute()`、不含 `\`、`parts = PurePosixPath(rel).parts` 非空、各段不含 `"."` 或 `".."`、原始后缀 `PurePosixPath(rel).suffix.lower() == ".png"`。
  - **`kind == "shot"`**：要求 `parts[0] == "走查截图"` 且 `len(parts) >= 2`；要求 `shots_dir = cdir / "走查截图"` 存在、是目录且**自身非软链接**（`not shots_dir.is_symlink()`），`shots_real = shots_dir.resolve()` 必须等于 `(cdir.resolve() / "走查截图")`；令 `target = (cdir / rel).resolve()`，要求 `target.relative_to(shots_real)` 成功且 `target != shots_real`、`target.is_file()`、`target.suffix.lower() == ".png"`；全部满足返回 `target`，否则返回 `None`（挡住整目录软链接、外链文件及 `.png` 软链接指向非图片文件）。
  - **`kind == "design"`**：
    - 原始初筛：`parts[0].lower() not in (".git", ".collab")`。
    - **查找顺序（副本优先、主项目回退）**：
      1. 若 `wt_base = proj_real.parent / f"{proj_real.name}-worktrees"` 与 `wt_dir = wt_base / card_id` 均存在、是目录且**自身均非软链接**，先在 `wt_dir.resolve()` 中查找（与卡 A 复查员看的副本设计图完全一致，待验收时同名设计图在本卡更新过不会看到旧版）；
      2. 若副本不存在或副本中未命中，再回退到主项目 `proj_real` 中查找（卡片合并后副本已删或历史老卡自动看主项目）。
    - 对每个候选根目录 `root_real`：令 `cand = (root_real / rel).resolve()`，要求 `rel_resolved = cand.relative_to(root_real)` 成功且 `len(rel_resolved.parts) >= 1`、**`rel_resolved.parts[0].lower() not in (".git", ".collab")`**（挡住大小写不敏感绕过及软链接落进 `.collab`/`.git`）、`cand.is_file()`、**`cand.suffix.lower() == ".png"`**（挡住 `.png` 软链接指向非图片文件）。第一个命中的返回 `cand`，均未命中返回 `None`。
  - 其他 `kind` 一律返回 `None`。
- `card_view` 返回字典（包括未接入项目、还没选卡、走 `_chat_view` 或无流转记录的卡）中始终包含 `"walk_shots"` 键，默认为空列表 `[]`。
- 选出哪一次走查记录：
  - 计算起始边界 `since`：取所有满足 `(e.get("kind") == "回复" and e.get("src") == "开发员") or e.get("kind") == "追加要求"` 的记录中最大的 `seq`（没有则 `since = 0`）。
  - 在 `seq > since` 的流转记录中筛选 `kind == "页面检查"` 且 `walk == True` 的记录，取**最后一条**。
  - 只有这条记录存在、其 `result == PASS`、且 `shots` 是非空列表时才构建列表；否则 `"walk_shots"` 为 `[]`（同一次交活先过后又没过，或点“继续改”记入追加要求后还没重新走查，都不显示旧截图）。
- 逐条校验并组装 `shots` 中的对象（非对象跳过）：
  - `step = str(item.get("步骤") or "").strip()`，为空则跳过；
  - `shot = str(item.get("截图") or "").strip()`：调用 `resolve_walk_image(project, card_id, "shot", shot)`，返回 `None` 则跳过该条；
  - `design = str(item.get("设计图") or "").strip()`：允许为空 `""`；非空时调用 `resolve_walk_image(project, card_id, "design", design)`，返回 `None` 时将 `design` 置为 `""`（该步仍保留本次截图）。
  - 每条只输出固定 3 个相对字段：`{"step": step, "shot": shot, "design": design}`（绝不返回本机绝对路径）。

### P2 新增只读看图接口 `/api/clerk/card/image`（`desktop/server.py`）

- 必须在 `_clerk_get` 函数体**最开头、`if blocks_tools.is_blocks_home(project):` 建目录逻辑之前**优先处理 `if path == "/api/clerk/card/image": ... return`（天然走调用 `_clerk_get` 前的 `_chat_allowed(query)` 一次性口令校验；无口令或错口令返回 403）。**绝不能放在建积木库目录之后**，保证看图接口严格只读——即使传入尚不存在的积木库路径也绝不创建目录，直接按不存在目录返回 `400`。
- 查询参数校验：`project` 必须非空且为已存在目录；`card_id` 必须非空且匹配现有的 `_CARD_ID.fullmatch(card_id)`；`kind` 只能是 `"shot"` 或 `"design"`；`rel = str(query.get("path") or "").strip()` 必须非空。任一不满足返回 `400` JSON 错误 `{"error": "..."}`。
- 安全边界校验（直接复用 P1 的共用函数，绝不自己另拼路径放宽）：
  - 调用 `target = resolve_walk_image(project, card_id, kind, rel)`；
  - 返回 `None` 时一律返回 `404` `{"error": "没有这张图"}`（覆盖未接入项目、不存在的卡片、越界、软链接外链、大小写绕过 `.collab`/`.git`、非 png 或文件不存在）；
  - 校验通过则读取字节并返回 `self._send(200, target.read_bytes(), "image/png")`（保持现有 `_chat_get` 里 `/api/chat/image` 一样的返回方式，不动 `/api/chat/image`）。

### P3 卡片原文下方展示“走查截图”和点开大图（`desktop/static/chat.js`、`desktop/static/chat.css`）

- **不改 `desktop/static/chat.html` 与 `.chat` 的四行网格高度规则**（`test_card_info_fold.py` 守着）。
- **DOM 位置与结构**（全部在 `chat.js` 内动态创建，所有文字用 `textContent`，禁止 `innerHTML` 拼步骤名）：
  - 在 `openCard` 中找到 `box`（`#cardInfo`）和它的展开按钮 `#cardInfoFold`（如果已有），在它正下方插入容器 `<section id="cardWalk" hidden>`（只在第一次创建，之后复用；不要把新函数插在 `function cardFold(` 和 `var nowCard` 之间，以免打断 `test_card_info_fold.py` 的文本切片检查）。
  - `#cardWalk` 内部固定包含：
    - 头部 `.walk-head`：标题 `<span id="cardWalkTitle">`（文本写 `走查截图（N 张）`，N 为条目数）和折叠按钮 `<button type="button" id="cardWalkFold">收起截图</button>`（**按钮文字必须写“收起截图”和“展开截图”，严禁使用单纯的“收起”“展开”**，避免与老测试 `tests/rightfix_page_check.js` 找卡片状态行按钮的正则冲突）。
    - 内容列表 `<div id="cardWalkList">`：点击 `#cardWalkFold` 切换收起状态时，**必须设置 `walkList.hidden = walkFold`**（同步切类 `.fold`），同时更新按钮文字为 `walkFold ? "展开截图" : "收起截图"`（这样无论真实浏览器还是页面脚本检查的 `visible()` 都能准确判定收起/展开）。
  - 每一步渲染一个 `.walk-row`：
    - 步骤标题 `.walk-step`：`第 n 步：<step>`；
    - 图片并排区 `.walk-pair`（最多两栏）：
      - 左侧栏 `.walk-col`：标签 `<span class="walk-label">本次截图</span>` + `<button type="button" class="walk-thumb">` 内含 `<img class="walk-img">`（设置 `alt` 属性为 `<step> 本次截图`，`src` 为 `/api/clerk/card/image?` 拼好 `project`、`card`、`kind=shot`、`path`、`token`）；
      - 若 `item.design` 非空，右侧并排第二栏 `.walk-col`：标签 `<span class="walk-label">设计图</span>` + `<button type="button" class="walk-thumb">` 内含 `<img class="walk-img">`（`kind=design`）。`item.design` 为空时不画第二栏。
- **数据加载、刷新不重置、无走查卡隔离与防窜卡**：
  - 直接复用 `openCard` 里的 `showNow()` 已经发出的 `/api/view` 响应字段 `v.walk_shots`（**不另起新定时器**），在现有的 `if (forCard !== nowCard || myOpen !== nowOpen || !viewingCard) return;` 保护之后调用渲染函数，彻底防慢请求窜卡。
  - **仅在有走查截图显示时才加限高类 `.has-walk`**：取得卡片头部容器 `var chatHead = head && head.parentNode;`。当有有效 `shots` 并设 `walkBox.hidden = false` 时，加上 `if (chatHead && chatHead.classList) chatHead.classList.add("has-walk");`；当 `!Array.isArray(shots) || !shots.length`、`openCard` 初始化切入新卡、`hideCard()` 退出卡片时，一律移除 `if (chatHead && chatHead.classList) chatHead.classList.remove("has-walk");`（空值与 `classList` 保护兼容 `tests/chat_card_check.js` 极简模拟元素）。这样没有走查截图的卡片和主对话的 `.chat-head` 样式与原来一像素不差，长原文展开时的「收起」按钮绝不会被裁切。
  - 用当前卡号和 `JSON.stringify(shots)` 做快照对比（例如 `lastWalkKey`）：如果本次卡号与 `shots` 序列化字符串和上次完全一样，**直接跳过重绘**，保留用户已点击的“收起截图/展开截图”状态、列表滚动位置以及已打开的大图浮层；只有数据真变了或变成空数组时才更新 DOM。
  - 当 `shots` 相对上次快照发生变化且非空、触发重绘后，如果 `#walkLightbox` 当前正在打开，必须检查当前正在展示的大图地址（例如记录的当前 URL）是否仍在本次新渲染的所有缩略图地址中；**若已不在新列表中（例如重跑走查换了截图或某步设计图被移除），立刻关闭 `#walkLightbox` 并清空 `img.src`**；若仍在则保持打开。
  - 若 `!Array.isArray(shots) || !shots.length`：清空内容、设 `walkBox.hidden = true`、移除 `.has-walk`、清空快照并关闭大图浮层。
- **点击查看大图浮层 `#walkLightbox`**：
  - 点击任意 `.walk-thumb` 时打开（若尚未创建则创建并挂载：`var mount = document.body || (head && head.parentNode); if (mount) mount.appendChild(lb);`，**绝不能挂在 `.chat-head` 内部**，防止被 `.chat-head` 滚动裁剪；无 `document.body` 时回退仅服务极简模拟 DOM）。
  - 浮层内包含：遮罩本身（点击浮层背景空白处可关闭）、卡片面板 `.walk-lightbox-card`（点击内部不冒泡关闭）、标题 `#walkLightboxTitle`（写 `第 n 步：<step> · 本次截图` 或 `· 设计图`）、关闭按钮 `<button type="button" id="walkLightboxClose">关闭</button>`、高清大图 `<img id="walkLightboxImg">`。
  - 打开时设 `lb.hidden = false`，记录当前大图地址、写入标题和 `img.src`；关闭时设 `lb.hidden = true`、`img.src = ""`。
  - 按键盘 `Escape` 键关闭大图：绑定前必须检查 `if (document.addEventListener)`（老测试 `tests/chat_card_check.js` 里的简陋 `document` 没有 `addEventListener`，无保护会报异常）。
- **切卡与退出卡片清理**：
  - `openCard` 每次切入新卡时：立刻隐藏并清空 `#cardWalk`、移除 `.has-walk`、重置 `walkFold = false`、重置 `lastWalkKey = ""`、关闭 `#walkLightbox`（等待新卡的 `showNow()` 回来渲染）。
  - `hideCard()` 中同步隐藏清空 `#cardWalk`、移除 `.has-walk` 并关闭 `#walkLightbox`（**放在原有 `if (foldBtn) foldBtn.hidden = true;` 之后**，保持前 800 字内容，兼容 `tests/chat_card_check.js` 模拟元素）。
- **样式（`desktop/static/chat.css`）**：
  - 全部颜色、字号、圆角、阴影使用 `theme.css` 变量，不写死色值；
  - 不修改 `.chat`、`#cardHead`、`#cardNow`、`#cardInfo`、`#cardInfo.fold`、`#cardInfo.open`、`#cardInfo p`、`#cardFold:focus-visible` 的已有规则，**也不给通用 `.chat-head` 加全局 `max-height`**；
  - 仅给有走查截图时的头部加高度上限与滚动规则：`.chat-head.has-walk { max-height: min(55vh, max(120px, calc(100vh - 315px))); overflow-y: auto; align-content: flex-start; }`。其中 `55vh` 保证标准窗口（如 `642×800` 下 `440px`）给长卡原文展开和截图区足够空间，`max(120px, calc(100vh - 315px))` 保证矮窗（如 `642×480` 下 `165px`）即便写满最高 `160px` 长草稿（`.composer` 约 `200px`），下方对话区 `.msgs` 外框仍稳留 `115px`、扣掉上下内边距保留 `79px` 可读空间（与老页面持平）、与 `.composer` 零重叠；没有 `.has-walk` 的普通卡片完全不受影响；
  - `#cardWalk` 设 `flex: 1 0 100%;`、`#cardWalk[hidden] { display: none !important; }`，`#cardWalkList` 限高 `max-height: 26vh; overflow-y: auto;`（标准高度下原文收起时列表直接在内部滚动，不触发头部双层滚动）；`.walk-pair` 两栏并排（窄屏自适应不溢出横向）；`#walkLightbox` 固定全屏居中遮罩、`#walkLightbox[hidden] { display: none !important; }`、大图 `max-width: 90vw; max-height: 82vh; object-fit: contain;`。

## 四、状态表

| 什么时候 | 谁的东西 | 存什么 | 恢复成什么 / 显示什么 |
|---|---|---|---|
| 正常情况：打开有走查截图的卡 | 这张卡的中间区 | 不存新文件（从流转读最后一次通过的走查，记录当前渲染快照和收起状态） | 卡片原文下显示“走查截图（N 张）”，默认展开、并排显示（无设计图只显示左边）；点图出大图浮层 |
| 正常情况：打开没走查的卡 | 中间区 | 无 | `#cardWalk` 保持隐藏，画面和现在一模一样 |
| 收到切卡（`openCard`）时，或切卡快于接口返回 | 中间区 | 记录当前 `nowCard` 与 `nowOpen` 序号 | 收到 `openCard` 时立刻清空隐藏上一张卡的截图和大图（不等 `/api/view` 返回）；晚到的旧卡 `/api/view` 响应直接丢弃，绝不画到新卡上 |
| 正在看卡时切到别的卡、主对话或新对话 | 中间区 | 清空 `#cardWalk`、关闭大图浮层 | 主对话和新对话不留截图块和大图，主对话未发的草稿照旧保留 |
| 删掉截图文件、走查后又失败或追加了新要求 | 这张卡 | `card_view` 返回空或过滤掉丢失/越界的图 | 有剩余有效图就照旧显示，若当前正在看的大图已不在新列表里也同步关闭大图浮层；全无有效图或有新追加要求/新交活未走查时隐藏截图块并关掉大图 |
| 3 秒定时刷新时，用户已点“收起截图”或正打开大图查看 | 中间区 | 保留内存里的折叠标志 `walkFold` 与浮层打开状态 | 若 `/api/view` 返回的走查列表未变，不重绘 DOM、不自动展开、不关掉正在看的大图 |
| 退出应用时（关闭或重开） | 这张卡 | 截图与流转本身已在卡片目录持久保存（折叠、大图状态为临时查看态不落盘） | 重新打开卡片自动按流转重绘“走查截图”展开样子 |

## 五、原有行为清单

| 老行为（用户看得到的） | 现在由哪个测试守着 |
|---|---|
| 没走查的卡打开、切回主对话、开新对话：中间区无多余截图，主对话与任务对话互不窜话、草稿不丢 | `tests/test_acceptance_22_card_chat.py`、`tests/chat_card_check.js`、`tests/test_projswitch_extra_claude.py`、`tests/test_walk_card_b_acceptance.py`（`test_card_without_walk_keeps_existing_view_fields_unchanged`） |
| 卡片原文默认收起 2 行、点“展开/收起”切换、展开限高约一半、对话区保留剩余空间 | `tests/test_card_info_fold.py` |
| 卡片标题下那一行“此刻在干什么”收起展开、3 秒刷新不收起 | `tests/test_rightfix_acceptance.py`、`tests/rightfix_page_check.js`、`tests/test_flowfix_12_now_line.py`、`tests/test_flowfix2_display.py` |
| 聊天图片现有接口 `/api/chat/image` 与走查工具本身行为不变 | `tests/test_desktop_chat.py`（`test_open_send_and_read_events_over_http`）、`tests/test_walk_tool.py` |

修退回问题时如果要改这里的行为：停下不改，写进汇报。

## 六、不做什么

不改右栏和左栏、不改 `desktop/static/chat.html`、不改 `collab/` 下的跑卡流程和走查命令、不改 `tests/walk_tool.py`、不把走查截图搬进 `docs/`、不做图片像素比对或标注编辑、不修改 `/api/chat/image` 原接口、不碰 Mac 外壳。

## 七、老测试

开工前跑过与卡片中间区、页面折叠、草稿切换、走查工具、聊天图片接口相关的 8 组老测试（`test_card_info_fold`、`test_acceptance_22_card_chat`、`test_rightfix_acceptance`、`test_flowfix_12_now_line`、`test_flowfix2_display`、`test_projswitch_extra_claude`、`test_walk_tool`、`test_desktop_chat`）：共 166 项全过（11-21，卡 A 放进项目并重装后）。本单仅在 `card_view` 增加 `walk_shots` 字段、在 `server.py` 新增 `/api/clerk/card/image` 路径、在 `chat.js`/`chat.css` 卡片原文下追加默认隐藏的 `#cardWalk` 和大图浮层（不使用纯“展开”“收起”按钮文字，兼容简陋模拟 DOM），**预计没有老测试要变**。施工方不许改老测试；真有老测试因本单变红，停下写进汇报。

## 八、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_walk_card_b*.py"`
- 走查命令：`python3 tests/walk_卡B.py`
- 走查步骤（走查脚本照这张表截图，复查员照这张表对照）：

  | 步骤（走查脚本里 shot 的步骤名） | 页面上要看到的字（expect） | 对照设计图 |
  |---|---|---|
  | 没走查的卡正常打开 | 普通卡片 | 无 |
  | 卡片显示走查截图 | 走查截图（2 张） | 无 |
  | 点开截图看大图 | 关闭 | 无 |

- 测试写法：前端交互用真实执行 `chat.html` + `chat.js` 的页面检查（复用 `tests/ability_69b_dom.js`），看图接口用真实 `make_server` 发真实 HTTP 请求，不要逐字比对产品代码。
- **只跑验收命令和走查命令，不要跑全部测试**（全部测试由程序在复查时跑）。
- 汇报用大白话，不写文件名。

## 九、粘给行政的话（用户原样复制）

```
按施工单做：docs/走查截图/施工单-卡B.md
要做什么：有走查截图的卡片，在中间卡片原文下面增加默认展开的“走查截图”区域并支持收起展开、并排显示本次截图和设计图、点击弹窗查看大图；后端新增带口令且严格防越界的只读看图接口；没走查的卡保持不变。
不做什么：不改右栏和左栏、不改 chat.html、不改跑卡流程和走查工具、不把截图搬进 docs、不做像素比对、不改 /api/chat/image 原接口、不碰 Mac 外壳。
怎么算做完：验收命令和走查命令全部通过。
允许改哪些文件：
- desktop/view.py
- desktop/server.py
- desktop/static/chat.js
- desktop/static/chat.css
- tests/test_walk_card_b_extra*.py
- tests/walk_card_b_extra*.js
验收命令：python3 -m unittest discover -s tests -p "test_walk_card_b*.py"
```
