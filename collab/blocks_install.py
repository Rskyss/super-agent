"""积木库：核实名单和安装执行（53 号单）。

只有名单里的安装方式能由程序执行，每条都核对过官方来源（2026-11-08）；管家只能按编号选，不能自己写命令。
Cursor 官方只给“下载脚本直接执行”的方式，agy 没查到官方安装说明，都不进名单，由管家带用户自己装。
执行：先查前提工具；按参数执行，不经过 shell；输入关着，要密码的会直接失败而不是卡住；
限时，超时先请整组自己退出，过一会儿还不退就强行关掉；要管理员权限就停下说明，不自动提权；装完再找一次；
结果写记录，不存原始输出；同一时间只装一个（npm、brew 都不喜欢同时装）。
Homebrew 不自动更新目录、不顺手清理别的软件（用户 2026-11-08 定）。不接受别的环境设置：
BASH_ENV、NODE_OPTIONS 这类设置能借安装跑任意代码；要装进临时文件夹只能给 prefix（只对 npm）。
安装输出：安装只管把输出写进临时文件；页面隔一会儿用 output_tail() 取最后几行（打过码），像终端一样滚动。
不把每一行推给页面：推得慢就要略过、补显示，还会拖长限时（第四轮复查前一直在这上面出问题）。

"""
from __future__ import annotations

import math
import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time

from collab import blocks, blocks_web
from collab.blocks import BlockError
from collab.blocks_check import _mask_cut
from collab.blocks_env import _clean_path, _which, scratch_folder

INSTALLS = {
    "codex-npm": {"tool": "codex", "command": "codex", "label": "用 npm 装 Codex", "needs": "npm",
                  "args": ["npm", "install", "-g", "@openai/codex"], "minutes": 2,
                  "source": "https://www.npmjs.com/package/@openai/codex"},
    "codex-brew": {"tool": "codex", "command": "codex", "label": "用 Homebrew 装 Codex", "needs": "brew",
                   "args": ["brew", "install", "--cask", "codex"], "minutes": 3,
                   "source": "https://github.com/openai/codex"},
    "node-brew": {"tool": "node", "command": "node", "label": "用 Homebrew 装 Node（自带 npm）", "needs": "brew",
                  "args": ["brew", "install", "node"], "minutes": 5,
                  "source": "https://formulae.brew.sh/formula/node"},
}
DEFAULT_TIMEOUT = 600
SCRIPT_LIMIT = 1024 * 1024  # 安装脚本最多下载这么大
TERM_GRACE = 5  # 超时后先请它自己退出，等这么多秒还不退才强行关掉
LINE_LIMIT = 2000  # 每行最多显示这么多字，超长的一行既卡打码也撑版面
TAIL_BYTES = 64 * 1024  # 判断结果只看最后这么多输出，整份不读进内存
SHOW_TAIL_BYTES = 16 * 1024  # 页面取进度时，从最后这么多输出里
SHOW_TAIL_LINES = 50  # 取最后这么多行
LONG_LINE = "……这一行太长，不显示……"
_BREW_ENV = {"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_ENV_HINTS": "1"}
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_NEEDS_HOW = {
    "npm": "先装 Node（自带 npm）：跟管家说“帮我装 Node”",
    "brew": "Homebrew 要输电脑密码，只能在终端里装：管家会一步步带你做",
}
_NEEDS_NAME = {"npm": "npm（装 Node 时自带）", "brew": "Homebrew"}
# 要管理员权限的说法按工具分开认：brew 会提醒“不要用 sudo 跑 brew”，npm 包名里也可能有 sudo，单个词不算
_ADMIN_WORDS = {
    "npm": ("eacces", "eperm", "permission denied", "operation not permitted"),
    "script": ("permission denied", "operation not permitted", "password is required", "password:",
               "need sudo", "sudo:", "a terminal is required", "must be run as root"),
    "brew": ("permission denied", "operation not permitted", "is not writable", "need sudo access",
             "a terminal is required", "a password is required"),
}
_ADMIN_HOW = {
    "npm": "换一种安装方式（比如用 Homebrew 装），或者让管家带你在终端里处理",
    "script": "安装脚本要电脑密码或管理员权限，超级助手不替你输：按官网说明在终端里自己装",
    "brew": "Homebrew 的文件夹权限不对，要在终端里修：让管家带你一步步做",
}

_lock = threading.Lock()
_busy = {"on": False}
_live = {"fd": None, "last": []}  # 正在装的那份输出；装完留下最后几行，给页面装完后再取一次


def installing() -> bool:
    with _lock:
        return _busy["on"]


def _item(install_id) -> dict:
    if not isinstance(install_id, str) or install_id not in INSTALLS:
        raise BlockError(f"安装方式 {install_id!r} 不在核实名单里：名单外的工具要你按官网自己装，管家带着你做")
    return INSTALLS[install_id]


def options(tool) -> list:
    """某个工具在核实名单里的安装方式，给管家挑。"""
    return [describe(k) for k in sorted(INSTALLS) if INSTALLS[k]["tool"] == tool]


def describe(install_id) -> dict:
    """安装提议上要显示的：装什么、来源、原样的命令、前提、大概多久。"""
    item = _item(install_id)
    return {"id": install_id, "tool": item["tool"], "label": item["label"], "source": item["source"],
            "needs": item["needs"], "minutes": item["minutes"], "command": shlex.join(item["args"])}


def _signal_group(pid, sig) -> bool:
    """给整组发信号；整组都没了返回 False。"""
    try:
        os.killpg(pid, sig)
        return True
    except OSError:
        return False


def _stop_group(proc) -> None:
    """先请整组自己退出（npm、brew 能收拾好半截文件），等 TERM_GRACE 秒还在就强行关掉。"""
    if _signal_group(proc.pid, signal.SIGTERM):
        deadline = time.monotonic() + TERM_GRACE
        while time.monotonic() < deadline:
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                continue
            if not _signal_group(proc.pid, 0):
                break
            time.sleep(0.1)
    _signal_group(proc.pid, signal.SIGKILL)
    proc.wait()


def _tail_lines(data: bytes, start: int, before: bytes = b"") -> list:
    """从文件中间某处读到的一段拆成行。start 不在开头、前一个字也不是换行时，第一行只有半截（可能是半截密钥），丢掉。
    \r 是原地刷新（进度条）：一行里只留最后那一帧，和终端看到的一样。"""
    lines = data.decode("utf-8", "replace").replace("\r\n", "\n").split("\n")
    if start > 0 and before not in (b"\n", b"\r"):
        lines = lines[1:]
    if lines and lines[-1] == "":
        lines.pop()
    return [next((part for part in reversed(line.split("\r")) if part.strip()), "") for line in lines]


def _read_tail(fd, done) -> list:
    """输出的最后几行，打过码。还在装时，最后那行可能没写完（可能是半截密钥），先不显示。"""
    size = os.fstat(fd).st_size
    begin = max(0, size - SHOW_TAIL_BYTES)
    data = os.pread(fd, size - begin, begin)
    if not done:
        end = max(data.rfind(b"\n"), data.rfind(b"\r"))
        data = data[:end + 1]
    before = os.pread(fd, 1, begin - 1) if begin else b""
    lines = [line for line in _tail_lines(data, begin, before) if line.strip()]
    if not lines and data.strip():
        return [LONG_LINE]
    return [_mask_cut(line, LINE_LIMIT) for line in lines[-SHOW_TAIL_LINES:]]


def output_tail() -> list:
    """页面隔一会儿来取：正在装的（或刚装完的）最后几行。"""
    with _lock:
        if _live["fd"] is None:
            return list(_live["last"])
        try:
            return _read_tail(_live["fd"], done=False)
        except OSError:
            return []


def _run(args, env, timeout):
    """输出写临时文件，页面用 output_tail() 取；返回（退出码或 None 表示超时，最后一段输出）。
    在新建的空文件夹里跑，跑完就删：npm 会读当前文件夹里的设置（.npmrc 能改下载来源），安装程序也可能留下文件。"""
    with tempfile.TemporaryFile() as out, scratch_folder("积木库安装-") as folder:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                                start_new_session=True, cwd=folder)
        fd = out.fileno()
        with _lock:
            _live.update(fd=fd, last=[])
        try:
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                code = None
        finally:
            try:
                _stop_group(proc)  # 超时的整组关掉；正常结束也把留在后台的小程序关掉
            finally:
                with _lock:  # 文件关掉之前一定把句柄清掉，不然页面可能读到之后别的文件
                    try:
                        last = _read_tail(fd, done=True)
                    except OSError:
                        last = []
                    _live.update(fd=None, last=last)
        size = os.fstat(fd).st_size
        begin = max(0, size - TAIL_BYTES)
        before = os.pread(fd, 1, begin - 1) if begin else b""
        return code, "\n".join(_tail_lines(os.pread(fd, size - begin, begin), begin, before))


def _check_timeout(timeout) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) \
            or timeout <= 0:
        raise BlockError("安装限时要是正数（秒）")


def _extra_env(item, prefix) -> dict:
    """只有程序自己定的设置：brew 不自动更新、不清理；npm 可以装进指定的完整路径（走查用）。"""
    env = dict(_BREW_ENV) if item["needs"] == "brew" else {}
    if prefix is not None:
        if item["needs"] != "npm":
            raise BlockError("只有用 npm 装的才能指定装到哪个文件夹")
        if not isinstance(prefix, str) or not os.path.isabs(prefix) or os.pathsep in prefix:
            raise BlockError("装到哪个文件夹要写完整路径")
        env["npm_config_prefix"] = prefix
    return env


def run_install(install_id, root=None, path=None, timeout=DEFAULT_TIMEOUT, prefix=None,
                force=False, record=True) -> dict:
    """按核实名单装一个工具。返回 {ok, status, reason, how, seconds, found}。
    status：ok / already（已经装了，没跑）/ missing_prereq / needs_admin / timeout / fail。
    path 是找命令的路径，只能由程序给（超级助手自己的或终端的），不能来自管家或页面。
    prefix 只给走查用：npm 装进这个文件夹，装没装也在它的 bin 里找。"""
    item = _item(install_id)
    _check_timeout(timeout)
    extra = _extra_env(item, prefix)
    lookup = _clean_path(os.environ.get("PATH", "") if path is None else path)
    target = os.path.join(prefix, "bin") if prefix is not None else lookup  # 装进 prefix 的，只在 prefix 里找
    result = _install(item, item["args"], lambda: dict(os.environ, PATH=lookup, **extra), lookup, target,
                      timeout, force)
    if record:
        blocks.add_record(item["tool"], dict(type="install", install=install_id, **result), root=root)
    return result


def _install(item, args, make_env, lookup, target, timeout, force, prepare=None) -> dict:
    """安装的共同部分：同一时间只装一个；已装的不跑；先查前提；跑完按说法判断。
    args[0] 是前提程序名（npm / brew），会换成找到的完整路径；prepare 给脚本安装用（下载后返回参数）。"""
    with _lock:
        if _busy["on"]:
            raise BlockError("正在安装别的工具，等它装完再装")
        _busy["on"] = True
        _live["last"] = []  # 上一次安装的输出不能留给这一次（已经装了、缺前提时根本不跑）
    try:
        started = time.time()
        if not force and _which(item["command"], target):
            result = {"ok": True, "status": "already", "found": True,
                      "reason": f"{item['tool']} 已经装好了，不用再装", "how": ""}
        elif prepare is None and not _which(item["needs"], lookup):
            result = {"ok": False, "status": "missing_prereq", "found": False,
                      "reason": f"要先有 {_NEEDS_NAME[item['needs']]} 才能这样装", "how": _NEEDS_HOW[item["needs"]]}
        elif prepare is not None:
            with scratch_folder("积木库脚本-") as holder:
                code, output = _run(prepare(holder), make_env(), timeout)
            result = _judge(item, code, output, target)
        else:
            code, output = _run([_which(item["needs"], lookup)] + list(args[1:]), make_env(), timeout)
            result = _judge(item, code, output, target)
        result["seconds"] = round(time.time() - started, 1)
    finally:
        with _lock:
            _busy["on"] = False
    return result


def _download_script(url) -> bytes:
    """下载安装脚本：和读网页同一套地址检查（只许 https、挡私有地址、每次跳转都查），最多 SCRIPT_LIMIT。"""
    current = url
    for _ in range(blocks_web.MAX_REDIRECTS + 1):
        blocks_web.check_url(current)
        try:
            status, location, body = blocks_web._open(current, blocks_web.PAGE_TIMEOUT, SCRIPT_LIMIT)
        except Exception as exc:  # noqa: BLE001
            raise BlockError("下载不了安装脚本：连不上或超时") from exc
        if status in (301, 302, 303, 307, 308) and location:
            import urllib.parse
            current = urllib.parse.urljoin(current, location)
            continue
        if status != 200:
            raise BlockError(f"下载不了安装脚本（网站返回 {status}）")
        if len(body) > SCRIPT_LIMIT:
            raise BlockError("安装脚本太大，不运行")
        if not body.strip():
            raise BlockError("下载到的安装脚本是空的")
        return body
    raise BlockError("安装脚本的地址跳转太多次")


def _web_targets(lookup, prefix) -> str:
    """装好后去哪找：npm 装进 prefix 的只在 prefix；脚本常装进 ~/.local/bin。"""
    if prefix is not None:
        return os.path.join(prefix, "bin")
    home = os.environ.get("HOME", "")
    extra = [os.path.join(home, ".local", "bin")] if home else []
    return _clean_path(os.pathsep.join([lookup] + extra))


def run_web_install(spec, root=None, path=None, timeout=DEFAULT_TIMEOUT, prefix=None, force=False,
                    record=True) -> dict:
    """按网上找来、核对过的安装命令装（沙盒接入 63 号单）。不信 spec 里存的参数：用 command_text 重新解析。
    npm、brew 和名单内一样执行；脚本先下载到空文件夹再交给 /bin/sh 或 /bin/bash，环境只给 PATH 和 HOME。"""
    if not isinstance(spec, dict):
        raise BlockError("安装说明不对")
    parsed = blocks_web.parse_install(spec.get("command_text"))
    command = spec.get("command")
    if not isinstance(command, str) or not blocks._COMMAND.fullmatch(command):
        raise BlockError("装好后的命令名不对")
    _check_timeout(timeout)
    lookup = _clean_path(os.environ.get("PATH", "") if path is None else path)
    if parsed["type"] == "script":
        if prefix is not None:
            raise BlockError("只有用 npm 装的才能指定装到哪个文件夹")
        item = {"tool": command, "command": command, "needs": "script"}
        home = os.environ.get("HOME", "")

        def prepare(holder):
            body = _download_script(parsed["script_url"])
            file = os.path.join(holder, "install.sh")
            with open(file, "wb") as fh:
                fh.write(body)
            return [f"/bin/{parsed['shell']}", file]

        result = _install(item, [], lambda: {"PATH": lookup, "HOME": home}, lookup,
                          _web_targets(lookup, None), timeout, force, prepare=prepare)
    else:
        item = {"tool": command, "command": command, "needs": parsed["type"]}
        extra = _extra_env(item, prefix)
        result = _install(item, parsed["args"], lambda: dict(os.environ, PATH=lookup, **extra), lookup,
                          _web_targets(lookup, prefix), timeout, force)
    if record:
        record_id = re.sub(r"[^a-z0-9-]+", "-", command.lower()).strip("-")[:40] or "web-install"
        blocks.add_record(record_id, dict(type="install", how_installed=parsed["command_text"], command=command,
                                        source=str(spec.get("source") or "")[:500], **result), root=root)
    return result


def _judge(item, code, output, lookup) -> dict:
    text = (output or "").lower()
    if code is None:
        return {"ok": False, "status": "timeout", "found": False, "reason": "安装超时：规定时间内没装完",
                "how": "网络可能太慢，稍后再试一次"}
    if code != 0:
        tool = item["needs"]
        if any(w in text for w in _ADMIN_WORDS[tool]):
            return {"ok": False, "status": "needs_admin", "found": False,
                    "reason": "要管理员权限才能装，超级助手不会自动提权", "how": _ADMIN_HOW[tool]}
        lines = [ln.strip() for ln in _LINE_BREAK.split(output or "") if ln.strip()]
        tail = _mask_cut(lines[-1], 120) if lines else "没有输出"
        how = f"稍后再试一次；还不行就跟管家说。安装程序最后说：{tail}"
        if tool == "brew":
            how = ("Homebrew 的软件目录可能太旧：在终端里运行一次 brew update 再试；"
                   f"还不行就跟管家说。安装程序最后说：{tail}")
        return {"ok": False, "status": "fail", "found": False, "reason": "安装没成功", "how": how}
    found = bool(_which(item["command"], lookup))
    return {"ok": True, "status": "ok", "found": found, "reason": f"{item['tool']} 装好了",
            "how": "" if found else "装好了，但超级助手还没认到：关掉超级助手再重新打开，然后检测一次"}
