"""开发助手走查工具：给改界面的卡用的“走查脚本”调它（P1 W0-1，Claude 写；写法见规则包《施工单怎么写》五之二）。

做的事：
- 起一份后台服务：用这份代码（卡片副本里就是改好的代码）、临时设置文件夹、假项目、固定口令。
  隔离做法（W0 复查第 1 轮严重 1）：PATH 最前面放一个假工具文件夹，codex、agy、cursor、security（钥匙串命令）等
  都是一跑就失败的假的；关掉后台服务启动时“从登录环境补 PATH”和“读终端 PATH”两处；千问、腾讯云的密钥文件指到
  临时文件夹里的空文件，不会去钥匙串取。所以不会调用模型、不碰你的真实设置和钥匙串。
- 后台服务跟着走查走：带 --watch-parent，走查脚本被强停（比如程序跑走查超时），后台服务自己退出。
- 在后台浏览器（本机 Chrome，无界面）里按真实窗口的默认宽度打开左栏、中间、右栏，都按“已配好行政”的样子开；
- 每一步截一张图存进 WALK_SHOTS_DIR（程序跑卡时给；单独跑时新建一个不会自动删的临时文件夹并打印位置），
  文件名“两位序号-步骤名.png”；
- 最后写步骤清单 走查.json：[{"步骤", "截图", "设计图"}]，设计图是项目（这份代码）里 png 的相对路径，没有就空。

**环境问题**（缺 playwright、打不开 Chrome、本机环境拒绝绑定 127.0.0.1 端口）抛 WalkEnvError，脚本退出码 127，程序判“环境出错”、不怪写代码的；其他后台服务起不来算代码问题（多半是副本里的代码改坏了），抛 WalkError 并带上后台的报错，脚本退出码 1，程序当走查没过、退回写代码的。

**会让走查失败（脚本退出码 1，程序当走查没过）的情况**：点不到、等不到的东西（5 秒）；截图时 expect 写的字
没出现、或被收起框/别的东西挡住截图里看不到；页面脚本报错；页面向后台要数据时后台回了错误（4xx、5xx）或根本连不上。

**已知取舍**（W0 复查到顶后用户定，不再补）：一行字只被裁掉下半截、行中间还露着，仍算看见；遮挡物只挡住跨行处的空格，会误判看不见。
expect 写短、写同一行里的几个字就碰不上；复查员还会逐张看截图对照设计图。

**走不了的**：要交给 Mac 外壳处理的点击（左栏点卡片、切项目、菜单，页面里是 superagent:// 开头的地址），后台浏览器里
点了没反应。这类步骤用 open(..., card=卡号)、open_card(卡号) 直接打开到目标样子，不要模拟点击；真点了，后面的 expect 会看不到而失败。

写走查脚本的样子（放 tests/ 下，施工单“走查命令”就是跑它；样板 tests/walk_example_card021.py）。
步骤名写简短中文（不空、至少含一个汉字、不带空格/斜杠/中文双引号、不超过 20 字）；带点击或等待操作的步骤用 `with w.step("步骤名"):` 包住，点不到按钮也能准确报出卡在哪一步：

    import walk_tool
    project = walk_tool.make_project(tmp_dir, cards=["CARD-021"])   # 卡片资料在 tests/fixtures/walk_cards/
    with walk_tool.Walk(project) as w:
        page = w.open("panel", card="CARD-021")
        w.shot(page, "右栏打开卡片", expect="右栏停下提示", design="docs/借鉴DSH/页面对齐/05-你验收.png")
        with w.step("点开交接"):
            page.click("text=交接")
            w.shot(page, "点开交接", expect="全部交接")
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]   # 这份代码的根（卡片副本里跑就是副本）
# 走查用的卡片资料放这里（提交进项目）。.collab 不提交，卡片副本里没有，所以不能从 .collab 里取
CARDS = ROOT / "tests" / "fixtures" / "walk_cards"
MANIFEST = "走查.json"
TOKEN = "walk"
ACTION_TIMEOUT_MS = 5000
SETTLE_MS = 1500
# 真实窗口的默认宽度：desktop/mac/Sources/SuperAgent/main.swift 里窗口 1280×800、navWidth 264、rightWidth 372，
# 三栏展开时中间再扣两条 1 像素分隔线：1280 - 264 - 372 - 2 = 642
PANES = {"nav": (264, 800), "chat": (642, 800), "panel": (372, 800)}
# 后台服务里一律换成假的命令：干活工具、钥匙串命令、Claude
BLOCKED = ("codex", "agy", "agent", "cursor", "cursor-agent", "qwen", "claude", "gemini", "security")

# 起后台服务的小启动器：先关掉补 PATH 的两处，再写一份“服务看到的 PATH 和 which 结果”给测试核对，最后照常启动
# 若只在绑定 127.0.0.1 端口时遇到系统权限/端口受限错误，标出 WALK_ENV_BIND_ERROR 并退出 127，和业务代码报错分开
_LAUNCHER = r"""
import errno, json, os, shutil, sys
sys.path.insert(0, sys.argv[1])
import desktop.server as server
server.adopt_terminal_path = lambda *a, **k: False
server.terminal_path = lambda *a, **k: None
_cls = getattr(server, "LocalServer", None)
if _cls is not None and hasattr(_cls, "server_bind"):
    _orig_bind = _cls.server_bind
    def _wrapped_bind(self, *a, **k):
        try:
            return _orig_bind(self, *a, **k)
        except OSError as exc:
            if exc.errno in (errno.EPERM, errno.EACCES, errno.EADDRINUSE, errno.EADDRNOTAVAIL):
                print(f"WALK_ENV_BIND_ERROR: {exc}", file=sys.stderr, flush=True)
                sys.exit(127)
            raise
    _cls.server_bind = _wrapped_bind
_make = server.make_server
def make_server(*args, **kwargs):   # 在 main 补完 PATH 之后、开始服务之前记下服务真正看到的环境
    info = {"path": os.environ.get("PATH", ""), "which": {n: shutil.which(n) for n in sys.argv[3].split(",")}}
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)
    return _make(*args, **kwargs)
server.make_server = make_server
sys.exit(server.main(sys.argv[4:]))
"""


def _single_line(text: str, limit: int = 300) -> str:
    """把多行报错压成单行，保证 WalkError 永远在 traceback 最后一行，不丢关键诊断。"""
    parts = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    joined = " | ".join(parts)
    return joined[:limit]


def _valid_step_name(step: str) -> str:
    """校验步骤名：不空、至少含一个汉字、不含斜杠/空白/中文双引号、不超过 20 字（和程序识别步骤名的规则一致）。"""
    name = str(step or "").strip()
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in name)
    if (not name or not has_cjk or "/" in name or "\\" in name or any(ch.isspace() for ch in name)
            or "“" in name or "”" in name or len(name) > 20):
        raise WalkError(f"步骤名要是不超 20 字、至少含一个汉字的简短文字，不能带空格、斜杠或中文双引号：{step!r}")
    return name


class WalkError(RuntimeError):
    """走查没走成：工具用错了，或者页面坏了（这时脚本退出码不是 0，程序当走查没过）。"""


class WalkEnvError(WalkError):
    """环境问题：缺 playwright、打不开 Chrome、本机禁止绑定 127.0.0.1。脚本退出码 127，程序判“环境出错”，不怪写代码的。"""


def _exit_127_on_env_error(kind, value, tb, _before=sys.excepthook):
    _before(kind, value, tb)
    if isinstance(value, WalkEnvError):
        sys.stderr.flush()
        os._exit(127)


sys.excepthook = _exit_127_on_env_error   # 走查脚本没接住环境问题时，退出码换成 127（judge 把 126/127 算环境出错）


def tiny_png() -> bytes:
    """一张 1×1 的 png，给测试当假设计图。"""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b""))


def clean_stale_homes(max_age_seconds: int = 3600) -> None:
    """走查被强停时来不及删临时设置文件夹：起新走查前，把一小时以前留下的 walk-home-* 删掉。"""
    import shutil
    import time
    base = Path(tempfile.gettempdir())
    for old in base.glob("walk-home-*"):
        try:
            if old.is_dir() and time.time() - old.stat().st_mtime > max_age_seconds:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass


def make_project(dest, cards=(), cards_dir=None) -> Path:
    """建一个假项目（git 仓库）；cards 列出的卡片资料从 cards_dir（默认 tests/fixtures/walk_cards）复制进假项目的 .collab/cards/。
    每张卡在假项目旁边建一个空的卡片副本文件夹（<假项目>-worktrees/<卡号>）：开跑过的卡没有副本时，程序会显示
    “改好的版本不在了”，待验收的卡就看不到验收按钮（W0 复查第 2 轮）。"""
    import shutil
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(dest), "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True, env=env)
    subprocess.run(["git", "-c", "user.name=walk", "-c", "user.email=walk@example.invalid",
                    "commit", "-q", "--allow-empty", "-m", "init"], cwd=dest, check=True, env=env)
    (dest / ".collab" / "cards").mkdir(parents=True, exist_ok=True)
    for card in cards:
        src = Path(cards_dir or CARDS) / card
        if not src.is_dir():
            raise WalkError(f"找不到卡片资料：{src}")
        shutil.copytree(src, dest / ".collab" / "cards" / card)
        (dest.parent / f"{dest.name}-worktrees" / card).mkdir(parents=True, exist_ok=True)
    return dest


class Walk:
    def __init__(self, project, shots_dir=None, source_root=None):
        self.project = Path(project).resolve()
        self.source_root = Path(source_root or ROOT).resolve()
        given = shots_dir or os.environ.get("WALK_SHOTS_DIR")
        # 没给就新建一个不会自动删的文件夹：单独跑完还要回看截图（W0 复查第 1 轮中等 4）
        self.shots_dir = Path(given) if given else Path(tempfile.mkdtemp(prefix="walk-shots-"))
        self.items = []
        self.problems = []
        self._home = None
        self._info_path = None
        self._server = None
        self._pw = None
        self._browser = None
        self.port = None
        self.server_env = {}

    # ---------- 起停 ----------

    @property
    def home(self) -> str:
        return self._home.name if self._home else ""

    @property
    def server_pid(self) -> int:
        return self._server.pid if self._server else 0

    @staticmethod
    def _fake_bin(home: Path) -> Path:
        folder = home / "假工具"
        folder.mkdir()
        for name in BLOCKED:
            path = folder / name
            path.write_text("#!/bin/sh\necho \"走查里不许跑 $0\" >&2\nexit 1\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return folder

    def start(self) -> "Walk":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise WalkEnvError("没装自动点页面的工具 playwright（给跑卡用的那份 Python 装上）") from exc
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        clean_stale_homes()
        self._home = tempfile.TemporaryDirectory(prefix="walk-home-")
        home = Path(self._home.name)
        empty = home / "空密钥"
        empty.write_text("", encoding="utf-8")
        self.server_env = {"HOME": str(home), "PATH": f"{self._fake_bin(home)}:/usr/bin:/bin",
                           "LANG": "en_US.UTF-8", "TMPDIR": str(home), "SHELL": "/bin/false",
                           "SUPERAGENT_TENCENT_KEY_FILE": str(empty), "SUPERAGENT_QWEN_KEY_FILE": str(empty)}
        self._info_path = home / "服务看到的环境.json"
        # 不另开进程组：程序跑走查超时停这一组时连它一起停；再加 --watch-parent，走查脚本没了它自己退出
        errors = home / "后台报错.txt"   # 报错写文件不接管道：后台一直在写日志，管道没人读会把它卡住
        with open(errors, "w", encoding="utf-8") as err:
            self._server = subprocess.Popen(
                [sys.executable, "-c", _LAUNCHER, str(self.source_root), str(self._info_path), ",".join(BLOCKED),
                 "--port", "0", "--token", TOKEN, "--watch-parent"],
                cwd=str(self.source_root), env=self.server_env, stdout=subprocess.PIPE, stderr=err, text=True)
        line = self._server.stdout.readline().strip()
        if not line.startswith("PORT="):
            try:
                self._server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            code = self._server.returncode
            said = errors.read_text(encoding="utf-8", errors="replace").strip()[-1500:]
            self.stop()
            if code == 127 and "WALK_ENV_BIND_ERROR:" in said:
                detail = said.split("WALK_ENV_BIND_ERROR:", 1)[1].strip()
                raise WalkEnvError(f"本机环境不允许启动本地服务（无法绑定 127.0.0.1）：{_single_line(detail)}")
            # 其他后台起不来多半是副本里的代码改坏了：算代码问题（退回写代码的），不算环境出错（W0 后卡 A 施工单复查第 3 轮）
            raise WalkError(f"后台服务没起来（多半是代码改坏了）：{said or line or '没有输出'}")
        self.port = int(line.split("=", 1)[1])
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001 — 缺浏览器要说清楚
            self.stop()
            raise WalkEnvError(f"打不开本机 Chrome（无界面）：{exc}") from exc
        return self

    def server_info(self) -> dict:
        """后台服务启动时看到的 PATH 和各假命令找到的位置（给测试核对隔离）。"""
        return json.loads(self._info_path.read_text(encoding="utf-8"))

    def stop(self) -> None:
        for close in (lambda: self._browser and self._browser.close(), lambda: self._pw and self._pw.stop()):
            try:
                close()
            except Exception:  # noqa: BLE001 — 收尾尽量做完
                pass
        self._browser = self._pw = None
        if self._server and self._server.poll() is None:
            self._server.send_signal(signal.SIGTERM)
            try:
                self._server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._server.kill()
        if self._server and self._server.stdout:
            self._server.stdout.close()
        self._server = None
        if self._home:
            self._home.cleanup()
            self._home = None

    def __enter__(self) -> "Walk":
        return self.start()

    def __exit__(self, kind, value, tb) -> None:
        try:
            if kind is None:
                self.finish()
        finally:
            self.stop()

    # ---------- 页面 ----------

    def url(self, pane: str, card: str = "") -> str:
        """和真窗口一样的地址（desktop/mac/Sources/SuperAgent/main.swift loadPanel、loadChat），按已配好行政的样子。"""
        base = f"http://127.0.0.1:{self.port}"
        q = f"project={quote(str(self.project))}&dispatcher=&token={TOKEN}&clerk=1"
        if pane == "panel":
            return f"{base}/?{q}&card={quote(card)}"
        if pane == "nav":
            mode = "card" if card else "main"
            return f"{base}/static/nav.html?{q}&selected={quote(card)}&mode={mode}&chat=1&projects=[]"
        if pane == "chat":
            return f"{base}/static/chat.html?{q}&mode=fresh&opening=0&thread=&draft="
        raise WalkError(f"没有这一栏：{pane}（只有 nav、chat、panel）")

    def open(self, pane: str, card: str = "", prepare=None):
        """按真实窗口这一栏的宽度打开页面，等它画完；返回页面，脚本接着点。prepare(page) 在打开前调（测试拦请求用）。"""
        if not self._browser:
            raise WalkError("还没起后台：先 start()，或用 with Walk(...) as w")
        if pane not in PANES:
            raise WalkError(f"没有这一栏：{pane}（只有 nav、chat、panel）")
        if pane == "chat" and card:
            raise WalkError("中间打开卡片用 open_card(卡号)；open(\"chat\") 只开空白对话")
        width, height = PANES[pane]
        page = self._browser.new_page(viewport={"width": width, "height": height})
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        page.on("pageerror", lambda exc: self.problems.append(f"{pane} 页面脚本报错：{exc}"))
        page.on("response", lambda r: self._check_response(pane, r))
        page.on("requestfailed", lambda r: self._check_failed(pane, r))
        if prepare:
            prepare(page)
        page.goto(self.url(pane, card))
        page.wait_for_timeout(SETTLE_MS)
        return page

    def _check_response(self, pane, response) -> None:
        if "/api/" in response.url and response.status >= 400:
            self.problems.append(f"{pane} 向后台要数据出错：{response.status} {response.url.split('?')[0]}")

    def _check_failed(self, pane, request) -> None:
        # 页面自己取消的请求（ERR_ABORTED：换页、关页、长等待被主动断开）不算；连不上、没回应才算
        if request.url.startswith(f"http://127.0.0.1:{self.port}/") and "ERR_ABORTED" not in str(request.failure or ""):
            self.problems.append(f"{pane} 连不上后台：{request.url.split('?')[0]}（{request.failure}）")

    def open_card(self, card: str):
        """中间打开一张卡：和真窗口一样，先问后台要这张卡的资料，再让中间页显示（窗口里点左栏的卡就是这样）。"""
        page = self.open("chat")
        answer = page.evaluate("""async ([project, card, token]) => {
            const r = await fetch("/api/clerk/card/open", {method: "POST",
                headers: {"Content-Type": "application/json", "X-Chat-Token": token},
                body: JSON.stringify({project: project, card: card})});
            return await r.json();
        }""", [str(self.project), card, TOKEN])
        if not isinstance(answer, dict) or answer.get("error"):
            raise WalkError(f"中间打不开卡 {card}：{answer}")
        page.evaluate("info => window.SuperAgentChat.openCard(info)", answer)
        page.wait_for_timeout(SETTLE_MS)
        return page

    # ---------- 截图和清单 ----------

    def check_design(self, design: str) -> str:
        """设计图只认项目（这份代码）里真有的 png，返回相对路径。施工单写的就是项目里的相对路径。"""
        rel = str(design or "").strip()
        target = (self.source_root / rel).resolve()
        if (not rel or self.source_root not in target.parents or target.suffix.lower() != ".png"
                or not target.is_file()):
            raise WalkError(f"设计图要是项目里真有的 png：{design}")
        return target.relative_to(self.source_root).as_posix()

    def _raise_problems(self, step: str) -> None:
        if self.problems:
            detail = _single_line("；".join(self.problems), limit=500)
            raise WalkError(f"走查在“{step}”这一步之前页面出了问题：{detail}")

    @contextmanager
    def step(self, step: str):
        """把这一步的操作（点按钮、等元素等）和截图包在一起：里面任意一步失败，都会抛出带单行步骤名的 WalkError。"""
        name = _valid_step_name(step)
        try:
            yield name
        except WalkEnvError:
            raise
        except WalkError as exc:
            msg = str(exc)
            if f"“{name}”这一步" in msg and "\n" not in msg:
                raise
            raise WalkError(f"“{name}”这一步失败：{_single_line(msg)}") from exc
        except Exception as exc:
            detail = _single_line(str(exc)) or type(exc).__name__
            raise WalkError(f"“{name}”这一步失败：{detail}") from exc

    def shot(self, page, step: str, design: str = "", expect: str = "", settle_ms: int = 500) -> Path:
        """截这一步的图。expect：这一步页面上必须看得到的字（等 5 秒，看不到就失败）；design：对照的设计图。"""
        name = _valid_step_name(step)
        design_rel = self.check_design(design) if design else ""
        page.wait_for_timeout(settle_ms)
        self._raise_problems(name)
        if expect:
            self._expect_seen(page, name, expect)
        self._raise_problems(name)
        filename = f"{len(self.items) + 1:02d}-{name}.png"
        path = self.shots_dir / filename
        page.screenshot(path=str(path))
        self.items.append({"步骤": name, "截图": filename, "设计图": design_rel})
        self._write()
        return path

    _SEEN_JS = """(el, text) => {
        // 这几个字在元素里每出现一次就量一次它自己占的位置（跨几行就有几块）；
        // 某一次出现的每一块都真看得见，才算看到（W0 确认复查：跨收起线只露一半不算；第二次出现看得见也算）
        const shown = (r) => {
            if (!r.width || !r.height) return true;   // 空块（换行处）不算
            const x = r.left + r.width / 2, y = r.top + r.height / 2;
            if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
            const hit = document.elementFromPoint(x, y);
            return !!hit && (hit === el || el.contains(hit));
        };
        let found = false;
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            for (let at = node.data.indexOf(text); at >= 0; at = node.data.indexOf(text, at + 1)) {
                found = true;
                const range = document.createRange();
                range.setStart(node, at);
                range.setEnd(node, at + text.length);
                const rects = [...range.getClientRects()].filter(r => r.width && r.height);
                if (rects.length && rects.every(shown)) return true;
            }
        }
        if (!found) return shown(el.getBoundingClientRect());   // 字跨了几个片段：退回看整个元素的中间
        return false;
    }"""

    def _expect_seen(self, page, step: str, expect: str) -> None:
        """等这几个字出现在截图里真看得见的地方：不光要在页面上，还不能被收起框裁掉、被别的东西挡住、跑出窗口外；
        页面上有好几处同样的字（比如藏起来的同名按钮），只要有一处真看得见就算（W0 复查第 3 轮）。"""
        import time
        deadline = time.monotonic() + ACTION_TIMEOUT_MS / 1000
        found = False
        while True:
            for item in page.get_by_text(expect).all():
                try:
                    if item.is_visible():
                        found = True
                        if item.evaluate(self._SEEN_JS, expect):
                            return
                except Exception:  # noqa: BLE001 — 页面正在变，下一轮再看
                    pass
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(200)
        if found:
            raise WalkError(f"“{step}”这一步“{expect}”在页面里，但截图里看不到（被收起、挡住或在窗口外）")
        raise WalkError(f"“{step}”这一步页面上没看到“{expect}”")

    def _write(self) -> None:
        (self.shots_dir / MANIFEST).write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")

    def finish(self) -> list:
        """写最终清单；最后一张截图之后页面又出了问题，也算没走成。"""
        self._write()
        self._raise_problems("收尾")
        return list(self.items)
