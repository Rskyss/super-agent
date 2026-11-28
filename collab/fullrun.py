"""复查的同时跑全量测试（流程修理第四轮 59）。

69b 复查通过后，Claude 跑全量才发现改坏了 3 项老测试；流程自己只跑本卡测试和“相关老测试”。
这里把 Claude 最后那一步搬进流程：
- 在一份副本里跑（不碰写代码的那份），副本里新建 git 仓库（有些老测试要用）；
- PATH 里只放 python3、node 和系统目录，真的 AI 工具（codex、cursor、agy……）找不到，老测试不会误启动它们；
- 只认 Python 项目（tests/ 下有 test_*.py），别的项目跳过；
- 没过时逐个文件在“改之前”（施工副本的 HEAD）再跑一次，只把这次改坏的算上（照第三批 _broken_by_change 的办法）。
"""
from __future__ import annotations

import fnmatch
import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from collab.judge import ENV_ERROR, FAIL, PASS, STALE, TIMEOUT, Verdict, _stop_group

FULL_TIMEOUT = 1800
FILE_TIMEOUT = 300
STOPPED = "停了"
_EXCLUDES = (".git", "node_modules", ".collab", "__pycache__", "*.app", ".venv", "venv")


def applicable(worktree) -> bool:
    """和全量实际跑的范围一致（discover -s tests）：顶层的 test_*.py，加上带 __init__.py 的子文件夹里的。
    没有 __init__.py 的子文件夹 unittest 不进去，项目自己的测试命令也跑不到，这里也不算（复查第 3 轮）。"""
    tests = Path(worktree) / "tests"
    if not tests.is_dir():
        return False
    for path in tests.rglob("test_*.py"):
        parent = path.parent
        while parent != tests and (parent / "__init__.py").is_file():
            parent = parent.parent
        if parent == tests:
            return True
    return False


_GROUPS: set = set()   # 本进程拉起的独立进程组（复制、跑测试）；停工时一把收掉


def _note_group(env, pgid: int) -> None:
    """把独立进程组的号登记到外部给的清单里：接受进程被强杀、来不及自己收尾时，
    桌面能照这份清单把已经跑起来的测试一并收掉（复查 54 第 4 条）。"""
    path = (env if env is not None else os.environ).get("SUPERAGENT_GROUPS_FILE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{pgid}\n")
    except OSError:
        pass


_EXITING = {"on": False}


def kill_all_groups() -> None:
    """停工信号来了：之后不再起新的准备命令；登记的独立进程组连同它们另开的进程组一次性强停，不等
    （复查第 3 轮：一组组温和停再等，总时长会超过桌面 8 秒的强杀期限）。"""
    import signal
    _EXITING["on"] = True
    for pgid in list(_GROUPS):
        for pid in _descendants(pgid) + [pgid]:
            for kill in (os.killpg, os.kill):
                try:
                    kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        _GROUPS.discard(pgid)


def _descendants(pid: int) -> list:
    """一个进程的所有后代（包括它们自己另开新进程组的，例如测试里起的工具）。"""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    kids: dict = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], [pid]
    while todo:
        for child in kids.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


_APP_PYTHON_CACHE: str | None = None


def app_python() -> str:
    """统一选开发助手自己的 Python：COLLAB_PYTHON -> 登录环境查一次 -> sys.executable。"""
    global _APP_PYTHON_CACHE
    env_py = (os.environ.get("COLLAB_PYTHON") or "").strip()
    if env_py and os.path.isfile(env_py) and os.access(env_py, os.X_OK):
        env_py = os.path.expanduser(env_py)
        # 相对路径接上当前目录、但不消掉“..”：目录是链接时 abspath 会指到别处（复查 54 第 4 轮中等 1）
        return env_py if os.path.isabs(env_py) else os.path.join(os.getcwd(), env_py)
    if _APP_PYTHON_CACHE is not None:
        return _APP_PYTHON_CACHE
    found = ""
    try:
        res = subprocess.run(
            ["/bin/zsh", "-lc", "command -v python3"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0:
            lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
            cand = lines[-1] if lines else ""
            if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
                found = cand
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    _APP_PYTHON_CACHE = os.path.abspath(os.path.expanduser(found or sys.executable))
    return _APP_PYTHON_CACHE


def _safe_bin(folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("python3", "node"):
        real = app_python() if name == "python3" else shutil.which(name)
        if real and name != "python3":
            real = os.path.abspath(real)   # PATH 里是相对目录时，临时目录里的链接才指得对
        if real and not (folder / name).exists():
            os.symlink(real, folder / name)
    return f"{folder}:/usr/bin:/bin:/usr/sbin:/sbin"


def _is_excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in (*_EXCLUDES, "*.pyc"))


def _snapshot(root) -> dict:
    """拍工作区文件指纹（相对路径 -> (大小, 纳秒修改时间)），跳过依赖/运行缓存等排除项。"""
    base = Path(root)
    snap = {}
    for dirpath, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if not _is_excluded(d)]
        for fname in filenames:
            if _is_excluded(fname):
                continue
            full = Path(dirpath) / fname
            try:
                st = full.lstat()
            except OSError:
                continue
            snap[full.relative_to(base).as_posix()] = (st.st_size, st.st_mtime_ns)
    return snap


# 第三批复查：只按名字拦，连接串里的密码漏网；只放白名单，又把项目测试自己要的设置拦掉（确认复查 2 + 全部测试查出）。
# 定成两条简单规矩：名字像密钥的不带；任何“账号:密码@”的地址去掉账号密码。其余照带（和人自己跑测试时一样）。
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL", re.I)
_USERINFO = re.compile(r"([a-zA-Z][\w+.-]*://)[^/@\s:]*:[^/@\s]*@")   # 只去“账号:密码@”；ssh://git@ 这种只有用户名的不动
_QUERY_SECRET = re.compile(r"([?&](?:password|passwd|pass|pwd|secret|token|key|apikey|api_key)=)[^&#\s]*", re.I)


def _no_userinfo(value: str) -> str:
    return _QUERY_SECRET.sub(r"\1", _USERINFO.sub(r"\1", value or ""))   # 地址里 ?password=… 也清掉（收尾确认复查 C1）


def safe_env(folder: Path) -> dict:
    """测试副本里的环境：真 AI 工具找不到；名字像密钥的不带，地址里的账号密码去掉（第三批复查第 1～3 轮、确认复查）。"""
    env = {k: _no_userinfo(v) for k, v in os.environ.items()
           if k != "SUPERAGENT_QWEN_KEY" and not _SECRET_NAME.search(k)}
    env.update(PATH=_safe_bin(folder), SUPERAGENT_NO_HANDOVER="1", SUPERAGENT_NO_FULL="1",
               SUPERAGENT_NO_PROGRESS="1", SUPERAGENT_NO_DRAFT="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def copy_tree(src, dst, stop=None) -> None:
    """复制一份再在副本里建一个新仓库。是 git 仓库就只复制仓库里的文件和新建的（不含 .gitignore 忽略的
    编译产物、密钥文件——本项目 desktop/mac 下的编译产物有 1.6G）；不是就 rsync 并排除 .git、依赖、运行记录。
    每条命令都有时限、会响应叫停。"""
    Path(dst).mkdir(parents=True, exist_ok=True)
    code, listed = _quick(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=str(src), stop=stop,
                          capture=True)
    args = ["rsync", "-a"]
    for ex in _EXCLUDES:
        args += ["--exclude", ex]
    if code == 0:
        names = [n for n in listed.split(b"\0") if n and (Path(src) / n.decode("utf-8", "replace")).exists()]
        with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
            fh.write(b"\0".join(names))
            listing = fh.name
        try:
            _quick(args + ["--from0", f"--files-from={listing}", f"{src}/", f"{dst}/"], check=True, stop=stop)
        finally:
            os.unlink(listing)
    else:
        _quick(args + [f"{src}/", f"{dst}/"], check=True, stop=stop)
    quiet = ["-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "core.fsmonitor=false"]  # 不留后台整理进程
    for cmd in (["git", "init", "-q"], ["git", *quiet, "add", "-A"],
                ["git", *quiet, "-c", "user.name=collab", "-c", "user.email=collab@local", "commit", "-qm", "副本"]):
        _quick(cmd, cwd=str(dst), stop=stop)


COPY_TIMEOUT = 300


def _quick(cmd, cwd=None, check=False, stop=None, capture=False):
    """复制副本、git 这类准备命令：有时限、自成一组、登记在案、会响应叫停（复查第 2 轮）。"""
    if _EXITING["on"]:   # 停工了：不再起新的（清理留给下次 git worktree prune）
        raise subprocess.CalledProcessError(-1, cmd, stderr="停工了".encode())
    out = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=out if capture else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    _GROUPS.add(proc.pid)
    _note_group(None, proc.pid)
    try:
        deadline = time.time() + COPY_TIMEOUT
        while proc.poll() is None:
            if (stop is not None and stop.is_set()) or time.time() > deadline:
                raise subprocess.CalledProcessError(-1, cmd, stderr="叫停或超时".encode())
            time.sleep(0.1)
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)
        out.seek(0)
        return proc.returncode, out.read()
    finally:
        _kill_group(proc.pid)
        _GROUPS.discard(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        out.close()


def _kill_group(pgid: int) -> None:
    """正常退出也把整组收掉：测试自己拉起的后台进程不能留下（复查：sleep 20 还活着）；
    它们另开新进程组的后代也一并收（复查第 3 轮：测试里用 start_new_session 起的工具）。"""
    import signal
    for pid in _descendants(pgid):
        try:
            pgid_of = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        if pgid_of != pgid and pgid_of != os.getpgid(0):
            try:
                os.killpg(pgid_of, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError, OSError):
                return
            time.sleep(0.1)


def run_command(cmd, cwd, env, timeout, stop=None, log=None) -> Verdict:
    """整组跑；被叫停或超时就连同子进程一起停掉。输出全留（全量的报错在末尾，不能截到 4000 字）。"""
    out = open(log, "w+", encoding="utf-8") if log else tempfile.TemporaryFile("w+", encoding="utf-8")
    proc = None
    seen = set()
    try:
        if _EXITING["on"]:
            return Verdict(STOPPED, None, "")
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT, text=True,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError as exc:
            return Verdict(ENV_ERROR, None, f"找不到命令：{exc}")
        _GROUPS.add(proc.pid)
        _note_group(env, proc.pid)
        if _EXITING["on"]:   # 登记前后正好在清场：登记后再看一眼，自己收掉（清场先亮信号再收登记的，1-9 复查第 2 轮）
            return Verdict(STOPPED, None, "")
        deadline = time.time() + timeout
        seen = set()   # 跑的过程中看到的后代进程组：父进程先退了，它们成了孤儿也要收（复查第 3 轮）
        last_scan = 0.0
        while proc.poll() is None:
            if time.time() - last_scan > 1:
                last_scan = time.time()
                for pid in _descendants(proc.pid):
                    try:
                        seen.add(os.getpgid(pid))
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
            if stop is not None and stop.is_set():
                _stop_group(proc)
                return Verdict(STOPPED, None, "")
            if time.time() > deadline:
                _stop_group(proc)
                out.seek(0)
                return Verdict(TIMEOUT, None, out.read()[-8000:])
            time.sleep(0.5)
        out.seek(0)
        text = out.read()
    finally:
        if proc is not None:
            _kill_group(proc.pid)   # 不管怎么结束（正常、出错、被叫停、外层异常），整组都收掉
            for pgid in seen - {proc.pid, os.getpgid(0)}:
                _kill_group(pgid)
            _GROUPS.discard(proc.pid)
        out.close()
    if proc.returncode == 0:
        return Verdict(PASS, 0, text)
    return Verdict(ENV_ERROR if proc.returncode in (126, 127) else FAIL, proc.returncode, text)


# 认 unittest“哪几项没过”只在这里写一份（跑卡卡点修理卡 B1：原来 fullrun、dispatch、progress 各认各的，
# 合到这里后各入口的结果和合并前一字不差——旧口径里的毛病也原样保留，修毛病不在本卡）。
_UT_LINE = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]*)\)(.*)$", re.M)
# unittest 一条失败块的开头（截取输出、摘要日志时用；和上面同一处定义）
UT_FAIL_START = re.compile(r"^(?:FAIL|ERROR): ", re.M)


def _ut_failed(text: str) -> list:
    """FAIL / ERROR 行：[(原名, 括号里的位置, 子测试参数)]，同一项坏几次就几条。只认这两种（复查第 3 轮：多认
    “意外通过”时，没收尾的一行会跨行吞掉下一条真失败）。"""
    return _UT_LINE.findall(text or "")


def _ut_method(name: str, where: str) -> str:
    """统一成“模块.类.方法”：老写法 test_a (m.T) 和新写法 test_a (m.T.test_a) 认成同一个。"""
    where = where.strip()
    suffix = "." + name
    base = where[:-len(suffix)] if where.endswith(suffix) else where
    return f"{base}.{name}" if base else name


def failed_pairs(output: str) -> set:
    """没过的“原名 (位置)”（不带子测试参数、不统一新旧写法）：施工进度计数、相关老测试比较用（原 progress.tally、
    dispatch._failing 的口径）。"""
    return {f"{n} ({w})" for n, w, _p in _ut_failed(output)}


def failing_methods(output: str) -> list:
    """没过的方法“模块.类.方法”，同一方法几个子测试只算一次、新旧写法认成同一个（先红名单用，原 progress.failing_names）。"""
    names = []
    for n, w, _p in _ut_failed(output):
        full = _ut_method(n, w)
        if full not in names:
            names.append(full)
    return names


def more_failures(now_out: str, base_out: str) -> bool:
    """这次比改之前多坏了：多出改之前没有的失败行，或汇总里的失败总数变多；两边都认不出名字时只比总数。
    （原 dispatch._more_failures 原样搬来，口径不变。）"""
    def total(text):
        m = re.search(r"FAILED \(([^)]*)\)", text or "")
        return sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", m.group(1))) if m else None

    # 先读总数再比名字：顺序照旧（复查第 4 轮：总数读不出来时旧版在这里就报错，外层按老规矩全列）
    now_names, now_total = failed_pairs(now_out) or None, total(now_out)
    base_names, base_total = failed_pairs(base_out) or None, total(base_out)
    if now_names is not None and base_names is not None and now_names - base_names:
        return True
    return now_total is not None and base_total is not None and now_total > base_total


def _test_file(test: str, where: str, root) -> str:
    """unittest 写的位置 → tests/ 下的测试文件。兼容老写法 test_a (mod.Class) 和新写法 test_a (mod.Class.test_a)
    （复查：本机 Python 3.15 是新写法）；导入就报错（unittest.loader._FailedTest）时项名就是模块名。
    给了项目就按 tests/ 下真有的文件找最长的那段模块名。"""
    loader = where.startswith("unittest.loader")
    parts = (test if loader else where).split(".")
    if root is not None:
        for n in range(len(parts), 0, -1):
            base = "tests/" + "/".join(parts[:n])
            if (Path(root) / (base + ".py")).is_file():
                return base + ".py"
            # 包在导入时就出错时，项名是包名（tests/shop），实际文件是 tests/shop/__init__.py
            if (Path(root) / base / "__init__.py").is_file():
                return base + "/__init__.py"
    if not loader:
        if len(parts) > 1 and parts[-1] == test:   # 新写法末尾是方法名
            parts = parts[:-1]
        if len(parts) > 1 and parts[-1][:1].isupper():   # 再去掉类名
            parts = parts[:-1]
    return "tests/" + "/".join(parts) + ".py"


def failing_files(output: str, root=None) -> dict:
    """没过的项按测试文件分组：{tests/x.py: [项, …]}。"""
    files: dict = {}
    for n, w, p in _ut_failed(output):
        files.setdefault(_test_file(n, w, root), []).append(f"{n} ({w}){p.rstrip()}")
    return files


def _test_files(root) -> list:
    """全量要跑的测试文件：和 discover -s tests 找到的一致（顶层 test_*.py，加上带 __init__.py 的子文件夹里的）。"""
    tests = Path(root) / "tests"
    out = []
    for path in sorted(tests.rglob("test_*.py")):
        parent = path.parent
        while parent != tests and (parent / "__init__.py").is_file():
            parent = parent.parent
        if parent == tests:
            out.append(path.relative_to(root).as_posix())
    return out


def _test_command(rel: str, copy, siblings=()) -> tuple:
    """一个测试文件单独跑：用标准的 discover 按文件名找（包里的 load_tests、包导入出错都按 unittest 自己的规矩算）。
    同名文件在不同子文件夹里会一起跑到、共用一份时限——这是标准跑法的样子，不另造一套加载（CARD-010 Claude 接手：
    复查 54 第 4 轮的 3 条严重全出在自制加载上，整层砍掉）。tests/ 之外的文件照旧从所在目录发现。"""
    path = Path(rel)
    if path.parts[:1] == ("tests",):
        return (["python3", "-m", "unittest", "discover", "-s", "tests", "-p", path.name], Path(copy))
    return (["python3", "-m", "unittest", "discover", "-s", path.parent.as_posix(), "-p", path.name],
            Path(copy))


_SUMMARY = re.compile(r"^Ran (\d+) tests? in [^\n]*\n\n(OK|FAILED|NO TESTS RAN)(?: \(([^)]*)\))?", re.M)


def _finished(v: Verdict) -> Verdict:
    """一个文件跑出的结果：有汇总才算跑完；没有汇总（半路退出，哪怕退出码是 0）算跑不起来；
    文件里一项测试都没有（新版 Python 写 NO TESTS RAN、退出码 5）算过（1-9 复查第 2 轮）。"""
    if v.result not in (PASS, FAIL):
        return v
    found = list(_SUMMARY.finditer(v.output or ""))
    if not found:
        return Verdict(ENV_ERROR, v.returncode, (v.output or "") + "\n（没跑完就退出了，没有汇总）")
    m = found[-1]   # 只认最后的总汇总（测试里自己跑了小 suite 也会打汇总，复查第 3 轮）
    if m.group(2) == "NO TESTS RAN":   # 只有新版 Python“文件里没测试”的正常写法（Ran 0、退出码 5）才算过
        if m.group(1) == "0" and v.returncode == 5:
            return Verdict(PASS, 0, v.output)
        return Verdict(ENV_ERROR, v.returncode, (v.output or "") + "\n（汇总写没有测试，但退出得不对，算没跑完，没有汇总）")
    return v


def _merge(results: list, took: float) -> Verdict:
    """各文件分开跑的结果拼成一份，末尾补一个总汇总（和一次跑完的样子一样，认没过的项照旧）。
    结果按最坏的算：停了 > 超时 > 跑不起来 > 没过 > 过（第一期 1-9）。"""
    total = failures = errors = skipped = unexpected = 0
    parts = []
    unfinished = []
    results = [(rel, _finished(v)) for rel, v in results]
    for rel, v in results:
        parts.append(f"===== {rel} =====\n{v.output or ''}")
        if v.result == ENV_ERROR and "没有汇总" in (v.output or ""):
            unfinished.append(rel)   # 没跑完就退出、没有汇总：不能让别处的老失败把它盖过去（复查第 1、2 轮）
        for m in list(_SUMMARY.finditer(v.output or ""))[-1:]:   # 每个文件只认最后的总汇总
            total += int(m.group(1))
            extra = m.group(3) or ""
            failures += sum(int(n) for n in re.findall(r"failures=(\d+)", extra))
            errors += sum(int(n) for n in re.findall(r"errors=(\d+)", extra))
            skipped += sum(int(n) for n in re.findall(r"skipped=(\d+)", extra))
            unexpected += sum(int(n) for n in re.findall(r"unexpected successes=(\d+)", extra))
    order = [STOPPED, STALE, TIMEOUT, ENV_ERROR, FAIL, PASS]
    worst = min((v.result for _, v in results), key=lambda r: order.index(r) if r in order else 0, default=PASS)
    if unfinished:
        parts.append("这些测试文件没跑完就退出了（没有汇总）：" + "、".join(unfinished))
    bits = [f"failures={failures}"] * bool(failures) + [f"errors={errors}"] * bool(errors) \
        + [f"skipped={skipped}"] * bool(skipped) + [f"unexpected successes={unexpected}"] * bool(unexpected)
    word = "OK" if worst == PASS else "FAILED"
    tail = f"\n{'-' * 70}\nRan {total} tests in {took:.3f}s\n\n{word}" + (f" ({', '.join(bits)})" if bits else "")
    code = 0 if worst == PASS else next((v.returncode for _, v in results if v.result == worst), None)
    return Verdict(worst, code, "\n".join(parts) + tail + "\n")


class FullRunner:
    """run(worktree, stop) 跑全量；base(worktree, files, stop) 在改之前的样子上逐个跑这些文件；
    alone(worktree, files, stop) 在现在的样子上单独跑这些文件（只在一起跑时坏的，不算改坏，第一期 1-9）。
    全量按文件分开、几个同时跑：每个文件单独一个进程，互不干扰，也快（1-9：一个个跑要 13 分钟）。"""

    def __init__(self, timeout: int = FULL_TIMEOUT, workers=None, file_timeout: int = FILE_TIMEOUT):
        self.timeout = timeout
        self.workers = workers or max(1, min(6, (os.cpu_count() or 2) // 2))
        self.file_timeout = file_timeout

    @staticmethod
    def _patterns(files) -> list:
        """按文件名去重：同名文件在不同子文件夹里，discover -p 一次就都跑到，分开跑会重复算（复查第 1 轮）。
        每个名字取路径最靠前的那一份当代表。"""
        first = {}
        for f in sorted(Path(x).as_posix() for x in files):
            first.setdefault(Path(f).name, f)
        return sorted(first.values())

    def _run_files(self, copy, files, env, stop) -> Verdict:
        from concurrent.futures import ThreadPoolExecutor
        import threading
        t0 = time.time()
        deadline = t0 + self.timeout
        halt = _Either(stop, threading.Event())

        targets = self._patterns(files)

        def one(rel):
            if halt.is_set():
                return rel, Verdict(STOPPED, None, "")
            left = deadline - time.time()
            if left <= 0:
                return rel, Verdict(TIMEOUT, None, "")
            limit = min(self.file_timeout, left)
            cmd, cwd = _test_command(rel, copy, targets)
            v = run_command(cmd, cwd, env, limit, halt)
            return rel, v

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(one, targets))
        if halt.is_set() or any(v.result == STOPPED for _, v in results):
            return Verdict(STOPPED, None, "")
        if any(v.result == TIMEOUT for _, v in results):
            # 结果表在前，每个文件实际打印的进度和报错都跟在后面：
            # 超时的、没过的、跑不起来的都要留，不能只剩一行“文件名：超时”（复查 54 第 3 条）。
            lines = [f"{Path(rel).name}：{v.result}" for rel, v in results]
            head = "\n".join(lines)
            outputs = []
            for rel, v in results:
                if not v.output:
                    continue
                if v.result == TIMEOUT:
                    # 超时文件被截下的是最后一段：里面有它已经打印的进度和报错
                    outputs.append((rel, v.output))
                elif v.result in (FAIL, ENV_ERROR):
                    # 同一轮里已经跑完但报错的文件：从第一条报错带起，别把正常输出灌满交接
                    cut = UT_FAIL_START.search(v.output)
                    outputs.append((rel, v.output[cut.start():] if cut else v.output))
            room = max(0, 7600 - len(head))   # 整段留在交接最后 8000 字以内
            share = max(1, room // len(outputs)) if outputs else 0   # 每个文件都留一段，不能只有最后一份
            sections = []
            for rel, text in outputs:
                if len(text) > share:
                    keep = max(0, share - 12)
                    text = ("……（前面省略）……\n" + text[-keep:]) if keep else ""
                sections.append(f"===== {rel} =====\n{text}")
            body = "\n".join(sections)
            return Verdict(TIMEOUT, None, head + ("\n" + body if body else ""))
        return _merge(results, time.time() - t0)

    def run(self, worktree, stop=None, without=()) -> Verdict:
        """without：副本里先拿掉这些文件再跑（卡 B2′：已知没过名单登记的等卡测试文件）。"""
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-"))   # 先建好再准备，准备出错也收得掉（复查第 1 轮）
        try:
            before = _snapshot(worktree)
            copy = tmp / "copy"
            copy_tree(worktree, copy, stop)
            if (stop is not None and stop.is_set()) or _EXITING["on"]:
                return Verdict(STOPPED, None, "")
            self.last_removed = remove_from_copy(copy, without)
            env = safe_env(tmp / "bin")
            if without:
                env.update(_fresh_cache(tmp))
            verdict = self._run_files(copy, _test_files(copy), env, stop)
            if verdict.result == STOPPED or (stop is not None and stop.is_set()) or _EXITING["on"]:
                return Verdict(STOPPED, None, "")
            if _snapshot(worktree) != before:
                return Verdict(STALE, None, "跑的时候代码变了，这次结果作废，要重新跑一遍全部测试")
            return verdict
        except subprocess.CalledProcessError as exc:
            if (stop is not None and stop.is_set()) or _EXITING["on"]:
                return Verdict(STOPPED, None, "")
            return Verdict(ENV_ERROR, None, f"复制副本没成：{exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def alone(self, worktree, files, stop=None) -> dict:
        """在现在的样子上按文件单独跑这些文件：每个文件自己一个进程、一份时限。"""
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-alone-"))
        results = {}
        try:
            copy = tmp / "copy"
            copy_tree(worktree, copy, stop)
            env = safe_env(tmp / "bin")
            by_file = {}
            deadline = time.time() + self.timeout
            targets = self._patterns(files)
            for rel in targets:
                if (stop is not None and stop.is_set()) or _EXITING["on"]:
                    break
                left = deadline - time.time()
                if left <= 0:
                    by_file[rel] = Verdict(TIMEOUT, None, "")
                    continue
                limit = min(self.file_timeout, left)
                cmd, cwd = _test_command(rel, copy, targets)
                by_file[rel] = _finished(run_command(cmd, cwd, env, limit, stop))
            by_name = {Path(rel).name: v for rel, v in by_file.items()}
            return {f: by_name[Path(f).name] for f in files if Path(f).name in by_name}
        except subprocess.CalledProcessError:
            return results
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def base(self, worktree, files, stop=None) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-base-"))
        head = tmp / "head"
        results = {}
        try:
            code, _ = _quick(["git", "worktree", "add", "-q", "--detach", str(head), "HEAD"], cwd=str(worktree),
                             stop=stop)
            if code != 0:
                return {}
            copy = tmp / "copy"
            copy_tree(head, copy, stop)
            env = safe_env(tmp / "bin")
            deadline = time.time() + self.timeout
            targets = self._patterns(files)
            for rel in targets:
                p = Path(rel)
                if (stop is not None and stop.is_set()) or _EXITING["on"]:
                    break
                if not (copy / p).is_file():
                    results[rel] = Verdict(ENV_ERROR, None, "改之前没有这个测试文件")
                    continue
                left = deadline - time.time()
                if left <= 0:
                    results[rel] = Verdict(TIMEOUT, None, "")
                    continue
                limit = min(self.file_timeout, left)
                # 和全量一样按文件单独跑，同名文件才不会挤到同一个进程、共用一份时限
                cmd, cwd = _test_command(rel, copy, targets)
                results[rel] = run_command(cmd, cwd, env, limit, stop)
            return results
        except subprocess.CalledProcessError:
            return results
        finally:
            for cmd in (["git", "worktree", "remove", "--force", str(head)], ["git", "worktree", "prune"]):
                try:
                    _quick(cmd, cwd=str(worktree))
                except subprocess.CalledProcessError:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)


_PROJECT_TOOLS = ("node", "npm", "npx", "pnpm", "yarn", "bun", "deno", "go", "cargo", "make")


def _command_env(folder: Path) -> dict:
    """项目自己配的全部测试命令用的环境：和 Python 全量一样藏起真 AI 工具，只多放几样常用的项目工具
    （网页项目要 pnpm、npm 这些）。只链接这几个名字，不把它们所在的整个目录放进来（那里常常也装着 codex）。"""
    env = safe_env(folder)
    for name in _PROJECT_TOOLS:
        real = shutil.which(name)
        if real and not (folder / name).exists():
            os.symlink(os.path.abspath(real), folder / name)
    return env


def _link_node_modules(src: Path, copy: Path) -> None:
    """副本里不复制 node_modules（几百兆），直接链接到施工副本那一份（它本身多半也链接到项目里）。"""
    real = Path(src) / "node_modules"
    if real.exists() and not (copy / "node_modules").exists():
        os.symlink(os.path.realpath(real), copy / "node_modules")


class CommandRunner:
    """第三批 ⑯：项目在配置里写了“全部测试命令”就用它（网页项目也有全部测试兜底）。
    认不出是哪一项没过，所以“改之前过不过”按整条命令比：base_all 在改之前的版本上再跑一次。"""

    def __init__(self, command: str, timeout: int = FULL_TIMEOUT):
        self.command = command
        self.timeout = timeout

    def applicable(self, worktree) -> bool:
        return True

    def _run_in(self, src, stop, check_stale: bool, without=()) -> Verdict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-cmd-"))
        try:
            before = _snapshot(src) if check_stale else None
            copy = tmp / "copy"
            copy_tree(src, copy, stop)
            if (stop is not None and stop.is_set()) or _EXITING["on"]:
                return Verdict(STOPPED, None, "")
            self.last_removed = remove_from_copy(copy, without)
            _link_node_modules(Path(src), copy)
            env = _command_env(tmp / "bin")
            if without:
                env.update(_fresh_cache(tmp))
            verdict = run_command(["/bin/bash", "-o", "pipefail", "-c", self.command], copy, env, self.timeout, stop)
            if verdict.result == STOPPED or (stop is not None and stop.is_set()) or _EXITING["on"]:
                return Verdict(STOPPED, None, "")
            if check_stale and _snapshot(src) != before:
                return Verdict(STALE, None, "跑的时候代码变了，这次结果作废，要重新跑一遍全部测试")
            return verdict
        except subprocess.CalledProcessError as exc:
            if (stop is not None and stop.is_set()) or _EXITING["on"]:
                return Verdict(STOPPED, None, "")
            return Verdict(ENV_ERROR, None, f"复制副本没成：{exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def run(self, worktree, stop=None, without=()) -> Verdict:
        """without：副本里先拿掉这些文件再跑（卡 B2′）。"""
        return self._run_in(worktree, stop, check_stale=True, without=without)

    def base_all(self, worktree, stop=None) -> Verdict:
        """在改之前的版本（施工副本的 HEAD）上跑同一条命令。"""
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-cmd-base-"))
        head = tmp / "head"
        try:
            code, _ = _quick(["git", "worktree", "add", "-q", "--detach", str(head), "HEAD"], cwd=str(worktree),
                             stop=stop)
            if code != 0:
                return Verdict(ENV_ERROR, None, "改之前的版本取不出来")
            _link_node_modules(Path(worktree), head)
            return self._run_in(head, stop, check_stale=False)
        except subprocess.CalledProcessError as exc:
            return Verdict(ENV_ERROR, None, f"改之前的版本准备没成：{exc}")
        finally:
            for cmd in (["git", "worktree", "remove", "--force", str(head)], ["git", "worktree", "prune"]):
                try:
                    _quick(cmd, cwd=str(worktree))
                except subprocess.CalledProcessError:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)


def _through_link(root: Path, rel: str) -> bool:
    """路径中间哪一层是符号链接（目录快捷方式）：不认，免得拿掉的是别处的文件（复查第 3 轮）。"""
    cur = Path(root)
    for part in Path(rel).parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def remove_from_copy(copy, files) -> list:
    """在副本里拿掉这些文件（卡 B2′），返回真的拿掉了哪些：只认副本里面的普通文件——目录、符号链接、
    副本里本来就没有的（被 .gitignore 挡掉没复制进来）都不算拿掉（复查第 2 轮）。"""
    root = Path(copy).resolve()
    removed = []
    for rel in files or ():
        target = Path(copy) / rel
        if target.is_symlink() or not target.is_file() or _through_link(Path(copy), rel):
            continue
        try:
            real = target.resolve()
        except OSError:
            continue
        if root not in real.parents:
            continue
        target.unlink()
        removed.append(rel)
    return removed


_UT_SUMMARY = re.compile(r"^Ran (\d+) tests? in [^\n]*\n\n(?:OK|FAILED|NO TESTS RAN)(?: \(([^)]*)\))?", re.M)
_NODE_SUMMARY = re.compile(r"^\s*(?:ℹ|#) tests (\d+)\s*$(?P<rest>(?:\n\s*(?:ℹ|#) \w+ [\d.]+\s*$)*)", re.M)
_PYTEST_SUMMARY = re.compile(r"^=*\s*((?:\d+ \w+(?:, )?)+) in [\d.]+s\b.*$", re.M)


_NODE_FILE_LINE = re.compile(r"^(?:✔|✖|ok \d+ -|not ok \d+ -) (?:/\S*|\S+\.[cm]?[jt]sx?)(?: \(|\s*$)", re.M)


def _node_count(text: str) -> int:
    """node 的汇总相加；“只是文件加载成功”的那一行（顶格、名字是文件路径）不算测试正文（复查第 4、5 轮）。
    TAP 一段里写了“一项都没有”（1..0）的，这一段算 0，别的段照算。"""
    segments = re.split(r"^TAP version \d+\s*$", text, flags=re.M) if "TAP version" in text else [text]
    total = 0
    for seg in segments:
        if re.search(r"^\s*1\.\.0\s*$", seg, re.M):
            continue
        part = 0
        for m in _NODE_SUMMARY.finditer(seg):
            rest = dict(re.findall(r"(?:ℹ|#) (\w+) ([\d.]+)", m.group("rest") or ""))
            part += int(m.group(1)) - int(float(rest.get("skipped", 0))) - int(float(rest.get("todo", 0)))
        part -= len(_NODE_FILE_LINE.findall(seg))
        total += max(0, part)
    return total


def executed_count(output: str, merged: bool = False):
    """这次实际执行了几项测试正文（跳过、待办、只标了预期失败没跑的、只是文件加载成功的都不算）；
    一段汇总都认不出返回 None。merged=True 是程序自己逐文件跑的输出：unittest 只认末尾总汇总（不重复加）；
    否则是项目自配的命令，几段 unittest 各算各的再相加（复查第 5 轮：不靠输出里像不像分段标题来猜）。
    pytest 认带不带 === 的汇总行，算 passed、failed、error、xpassed。负数按 0。"""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output or "")
    total, found = 0, False
    ut = list(_UT_SUMMARY.finditer(text))
    if ut:
        found = True
        for m in (ut[-1:] if merged else ut):
            skipped = sum(int(n) for n in re.findall(r"skipped=(\d+)", m.group(2) or ""))
            total += max(0, int(m.group(1)) - skipped)
    if _NODE_SUMMARY.search(text):
        found = True
        total += _node_count(text)
    for m in _PYTEST_SUMMARY.finditer(text):
        parts = dict((w, int(n)) for n, w in re.findall(r"(\d+) (\w+)", m.group(1)))
        if not any(w in parts for w in ("passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed",
                                        "deselected")):
            continue
        found = True
        total += sum(parts.get(w, 0) for w in ("passed", "failed", "error", "errors", "xpassed"))
    return total if found else None


_NODE_FAILING_HEAD = re.compile(r"^✖ failing tests:\s*$", re.M)
_NODE_TEST_AT = re.compile(r"^test at (.+?)(?::\d+:\d+)?\s*$")
_NODE_TREE_LINE = re.compile(r"^( *)(▶|✖|✔|﹣|⚠) (.+?)(?: \([\d.]+m?s\))?(?: # .*)?\s*$")
_NODE_FAIL_ITEM = re.compile(r"^✖ (.+?)(?: \([\d.]+m?s\))?\s*$")
_TAP_SUBTEST = re.compile(r"^( *)# Subtest: (.+?)\s*$")
_TAP_NOT_OK = re.compile(r"^( *)not ok \d+ - (.+?)(\s+#\s*(?:TODO|SKIP)\b.*)?\s*$", re.I)
_TAP_LOCATION = re.compile(r"^\s*location: '(.+?)(?::\d+:\d+)?'\s*$")
_PYTEST_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+?(?:\[[^\]]*\])?)(?: - .*)?$", re.M)   # 参数 id 里的“ - ”不截
_UT_TOTAL = re.compile(r"FAILED \(([^)]*)\)")


def _copy_relative(path: str) -> str:
    """测试副本在临时目录里，路径每次不同：只留副本里的相对路径（…/copy/tests/x.ts → tests/x.ts）。"""
    m = re.search(r"/(?:copy|head)/(.+)$", path)
    return m.group(1) if m else path


def _test_id(where: str, path) -> str:
    """一项测试的身份：文件 + 分组路径。分组名里本身带“ > ”也不会和多层分组撞上（复查第 2 轮严重 2）。"""
    return f"{where}::" + json.dumps(list(path), ensure_ascii=False)


def id_words(test_id: str) -> str:
    """给人看的写法：tests/a.ts::A > 名字（身份里的分组路径是 JSON，不好读）。"""
    where, _, rest = str(test_id).partition("::")
    try:
        path = json.loads(rest)
    except ValueError:
        return str(test_id)
    shown = " > ".join(str(p) for p in path) if isinstance(path, list) else rest
    return f"{where}::{shown}" if where else shown


def _sum_lines(pattern: str, text: str):
    found = re.findall(pattern, text, re.M)
    return sum(int(n) for n in found) if found else None


def _node_spec_names(text: str, head):
    """node / tsx 的 spec 输出：正文是一棵树（▶ 分组、缩进两格一层），末尾“✖ failing tests:”段有文件但没有分组。
    两边按顺序对上：失败段每一项在正文里往后找同名的失败项，拿它的分组路径。对不上、重名，返回 None。"""
    stack, fails = [], []
    for line in text[:head.start()].splitlines():
        m = _NODE_TREE_LINE.match(line)
        if not m:
            continue
        level, mark, name = len(m.group(1)) // 2, m.group(2), m.group(3)
        del stack[level:]
        if mark == "▶":
            stack.append(name)
        elif mark == "✖":
            fails.append(tuple(stack) + (name,))
    entries, where = [], ""
    for line in text[head.end():].splitlines():
        at = _NODE_TEST_AT.match(line)
        if at:
            where = _copy_relative(at.group(1))
            continue
        item = _NODE_FAIL_ITEM.match(line)
        if item:
            entries.append((where, item.group(1)))
    names, i = [], 0
    for where, name in entries:
        while i < len(fails) and fails[i][-1] != name:
            i += 1
        if i >= len(fails):
            return None
        names.append(_test_id(where, fails[i]))
        i += 1
    total = _sum_lines(r"^\s*ℹ fail (\d+)\s*$", text)
    if total is None or len(entries) < total:
        return None
    return names


def _tap_names(text: str):
    """TAP：按缩进（四格一层）和“# Subtest:”拼出分组路径，文件取 location；TODO、SKIP 和分组本身（type: 'suite'）不算。"""
    lines = text.splitlines()
    stack, names = [], []
    for k, line in enumerate(lines):
        sub = _TAP_SUBTEST.match(line)
        if sub:
            level = len(sub.group(1)) // 4
            del stack[level:]
            stack.append(sub.group(2))
            continue
        m = _TAP_NOT_OK.match(line)
        if not m or m.group(3):
            continue
        level, name = len(m.group(1)) // 4, m.group(2)
        where, suite = "", False
        for follow in lines[k + 1:k + 40]:   # 紧跟着的 YAML 块
            if re.match(r"^\s*\.\.\.\s*$", follow):
                break
            if re.match(r"^\s*type: 'suite'\s*$", follow):
                suite = True
            loc = _TAP_LOCATION.match(follow)
            if loc:
                where = _copy_relative(loc.group(1))
        if suite:
            continue
        names.append(_test_id(where, stack[:level] + [name]))
    return names


def failing_ids(output: str):
    """没过的是哪几项（v0.96 B1：项目自配的全部测试命令也能比“改之前就不过的”）。
    认得出 node/tsx 的 spec 和 TAP、unittest、pytest；名字带文件和分组（同名的两个测试不会认成一个），不带用时、行列号。
    认不全就当认不出、返回 None，不当旧问题放过（复查第 1 轮）：认出的条数少于汇总里的失败数、有重名、
    一种都认不出。认得出、一项都没失败返回空集合（调用的地方自己判断空集合配不配“没过”）。"""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output or "")
    names, recognised = [], False
    if (_sum_lines(r"^\s*(?:ℹ|#) cancelled (\d+)\s*$", text) or 0) > 0:
        return None   # 有被取消的（比如分组的 before() 坏了，下面的测试根本没跑）：分不清，照旧停（复查第 2 轮严重 3）
    head = _NODE_FAILING_HEAD.search(text)
    if head:
        found = _node_spec_names(text, head)
        if found is None:
            return None
        names += found
        recognised = True
    elif re.search(r"^\s*# fail \d+\s*$", text, re.M):
        found = _tap_names(text)
        if len(found) < (_sum_lines(r"^\s*# fail (\d+)\s*$", text) or 0):
            return None
        names += found
        recognised = True
    ut = _ut_failed(text)
    if ut or _UT_SUMMARY.search(text):
        recognised = True
        expected = 0
        for m in _UT_TOTAL.finditer(text):   # “expected failures=1”是预期中的，不算（复查第 1 轮轻微）
            expected += sum(int(n) for n in re.findall(r"(?<!expected )(?:failures|errors|unexpected successes)=(\d+)",
                                                       m.group(1)))
        found = [_ut_method(n, w) + p.rstrip() for n, w, p in ut]
        if len(found) < expected:
            return None
        names += found
    pytest_summary = [m for m in _PYTEST_SUMMARY.finditer(text) if re.search(r"\d+ (?:failed|errors?)\b", m.group(1))]
    if pytest_summary:
        recognised = True
        found = _PYTEST_FAILED.findall(text)
        expected = 0
        for m in pytest_summary:
            parts = dict((w, int(n)) for n, w in re.findall(r"(\d+) (\w+)", m.group(1)))
            expected += parts.get("failed", 0) + parts.get("error", 0) + parts.get("errors", 0)
        if len(found) < expected:
            return None
        names += found
    if not recognised or len(set(names)) != len(names):
        return None
    return set(names)


def _fresh_cache(tmp: Path) -> dict:
    """拿掉文件再跑的那一遍用一份干净的缓存和临时目录：不吃第一遍留下的缓存（复查第 2 轮）。"""
    out = {}
    for key, name in (("XDG_CACHE_HOME", "cache"), ("TMPDIR", "tmp"), ("npm_config_cache", "npm-cache")):
        path = Path(tmp) / name
        path.mkdir(parents=True, exist_ok=True)
        out[key] = str(path)
    return out


def runner_for(project):
    """这个项目用哪种全部测试：配了“全部测试命令”用它，没配用 Python 那一套（不是 Python 项目会跳过）。"""
    from collab.config import ConfigError, load_full_test
    try:
        command = load_full_test(project)
    except ConfigError:
        command = ""
    return CommandRunner(command) if command else FullRunner()


def summary_count(output: str):
    """unittest 汇总里让它判失败的项数（失败、出错、意外通过）；没有汇总返回 None。
    全量按文件分开跑时末尾有一个总汇总，只认最后那个（1-9）。"""
    found = re.findall(r"FAILED \(([^)]*)\)", output or "")
    if not found:
        return None
    return sum(int(n) for n in re.findall(r"(?:failures|errors|unexpected successes)=(\d+)", found[-1]))


def zero_tests(output: str) -> bool:
    """末尾总汇总显示实际只跑了 0 项（例如只有一个空测试文件），不能当通过放行。"""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output or "")   # 去掉颜色码再认（确认复查）
    counts = re.findall(r"^\s*Ran (\d+) tests?\b", text, re.M)
    if counts:
        return int(counts[-1]) == 0
    node = re.findall(r"^\s*(?:ℹ|#) tests (\d+)\s*$", text, re.M)   # node --test / tsx --test 的汇总（复查第 3 轮新 3）
    if node:
        return int(node[-1]) == 0
    return bool(re.search(r"^=+ no tests ran\b|^collected 0 items\b", text, re.M | re.I))   # pytest


class _Either:
    """两个叫停信号任一个亮就算叫停（run_command 只认 is_set）。"""

    def __init__(self, a, b):
        self.a, self.b = a, b

    def is_set(self) -> bool:
        return bool(_EXITING["on"] or (self.a is not None and self.a.is_set()) or self.b.is_set())


def culprits(now: Verdict, base: dict, root=None) -> list:
    """现在没过的文件里，哪些是这次改坏的：改之前过、跑不起来、没有这个文件，或者同一文件里多坏了（按项数比，
    同一个方法里的子测试多坏一个也算，复查第 2 条）。"""
    from collections import Counter
    out = []
    for rel, names in sorted(failing_files(now.output, root).items()):
        before = base.get(rel)
        if before is None or before.result != FAIL:
            out.append(rel)
            continue
        was = Counter(n for names_b in failing_files(before.output, root).values() for n in names_b)
        if Counter(names) - was:
            out.append(rel)
    return out
