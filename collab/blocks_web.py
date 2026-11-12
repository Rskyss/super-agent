"""积木库：读网页、联网搜索、安装命令核对、安装包底细（62 号单）。

管家自己打不开网页，一律由程序读；“命令在官网原文里”由程序重新读页面核对，不信管家的转述。
读网页只许 https，域名解析后挡住本机、局域网这类私有地址（每次跳转都查），免得借读网页去碰本机服务。
只认三种安装写法：npm 全局安装、Homebrew 官方目录、curl 下载脚本交给 sh/bash；解析成参数列表，不经过 shell。
剩余风险：解析和真正连接之间域名可能被换到别的地址（DNS 重绑定），这一层没堵，靠只许 https 降低。
"""
from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import shlex
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from collab.blocks import BlockError, _COMMAND, _SECRET_TEXT

ALLOW_LOCAL_ENV = "SUPER_AGENT_BLOCKS_ALLOW_LOCAL"  # 只给走查用：放行本机地址和 http，管家和页面都设不了
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
PAGE_TIMEOUT = 15
CLERK_TEXT_LIMIT = 8000
NEW_DAYS = 30
SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"
NPM_REGISTRY = "https://registry.npmjs.org/"
BREW_API = "https://formulae.brew.sh/api/"
_UA = "Mozilla/5.0 (Macintosh) SuperAgent-Blocks"

_NPM_NAME = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*")
_NPM_VERSION = re.compile(r"[A-Za-z0-9.^~*+-]{1,40}")
_BREW_NAME = re.compile(r"[a-z0-9][a-z0-9@._+-]{0,99}")
_CURL_FLAG = re.compile(r"-[fsSL]{1,4}")
_CURL_LONG = {"--fail", "--silent", "--show-error", "--location", "--proto", "=https", "--tlsv1.2"}
_FORBIDDEN = ("sudo", "&&", "||", ";", "`", "$(", ">", "<", "\n", "\r")


class SearchError(BlockError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ---------- 网络底层（测试替换这几个） ----------

def _resolve(host) -> list:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except (socket.gaierror, UnicodeError, OSError):
        return []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _open(url, timeout, limit):
    """发一次请求，不自动跳转。返回 (状态码, 跳转地址, 最多 limit+1 字节的正文)。"""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Location", ""), resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "") if exc.headers else "", b""


def _request_json(url, data=None, headers=None, timeout=PAGE_TIMEOUT):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=dict({"User-Agent": _UA, "Accept": "application/json"},
                                                              **({"Content-Type": "application/json"} if body else {}),
                                                              **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            raw = resp.read(MAX_PAGE_BYTES + 1)
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(64 * 1024) if exc.fp else b"", exc.code
    try:
        return status, json.loads(raw.decode("utf-8", "replace")) if raw else {}
    except ValueError:
        return status, {}


def _get_json(url):
    return _request_json(url)


def _post_json(url, payload, headers):
    return _request_json(url, data=payload, headers=headers, timeout=30)


# ---------- 地址检查和读网页 ----------

def _allow_local() -> bool:
    return os.environ.get(ALLOW_LOCAL_ENV) == "1"


_SHARED = ipaddress.ip_network("100.64.0.0/10")  # 运营商共享地址，ipaddress 不算私有（复查第 5 条）


_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")


def _fake_ip(ip) -> bool:
    try:
        return ipaddress.ip_address(ip) in _FAKE_IP
    except ValueError:
        return False


def _bad_ip(ip) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.version == 4 and addr in _SHARED:
        return True
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
            or addr.is_unspecified or getattr(addr, "is_site_local", False))


def check_url(url) -> urllib.parse.SplitResult:
    """只许 https 的公开网址；域名解析出的任何一个地址是私有地址都不行。"""
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        raise BlockError("网址不对：要写完整的 https:// 网址")
    parts = urllib.parse.urlsplit(url.strip())
    local = _allow_local()
    if parts.scheme != "https" and not (local and parts.scheme == "http"):
        raise BlockError("只能读 https:// 开头的网址")
    host = parts.hostname
    if not host:
        raise BlockError("网址里没有网站名")
    if local:
        return parts
    try:
        ips = [str(ipaddress.ip_address(host))]
    except ValueError:
        # 网站名解析出 198.18 段：是 Clash 这类代理“假地址”模式的转发地址，不是局域网设备，放行；
        # 直接写成数字地址的照旧挡（2026-11-09 用真密钥实测发现：开着代理时所有网站都被挡住）
        resolved = _resolve(host)
        if not resolved:
            raise BlockError(f"找不到 {host} 这个网站")
        ips = [ip for ip in resolved if not _fake_ip(ip)]
        if not ips:  # 全是代理的转发地址
            return parts
    if not ips:
        raise BlockError(f"找不到 {host} 这个网站")
    if any(_bad_ip(ip) for ip in ips):
        raise BlockError(f"{host} 指向本机或局域网地址，不能读")
    return parts


def page_text(raw: bytes) -> str:
    """网页正文：去掉脚本、样式和标签，解码实体，空白压成一个空格。"""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|noscript|template)\b.*?</\1\s*>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def read_page(url, timeout=PAGE_TIMEOUT) -> dict:
    """读一页网页。返回 {url（跳转后的）, domain, text}。"""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parts = check_url(current)
        try:
            status, location, body = _open(current, timeout, MAX_PAGE_BYTES)
        except BlockError:
            raise
        except Exception as exc:  # noqa: BLE001 — 网络出什么错都用一句话说
            raise BlockError(f"打不开这个网页：{parts.hostname} 连不上或超时") from exc
        if status in (301, 302, 303, 307, 308):
            if not location:
                raise BlockError("网页说要跳转，但没说跳到哪")
            current = urllib.parse.urljoin(current, location)
            continue
        if status != 200:
            raise BlockError(f"打不开这个网页（网站返回 {status}）")
        if len(body) > MAX_PAGE_BYTES:
            raise BlockError("这个网页太大，不读")
        return {"url": current, "domain": parts.hostname, "text": page_text(body)}
    raise BlockError("这个网页跳转太多次，不读")


def clerk_text(page) -> str:
    """交给行政看的网页内容：带网址，正文截短，像密钥的字打码。"""
    text = _SECRET_TEXT.sub("[已隐藏]", page["text"])
    if len(text) > CLERK_TEXT_LIMIT:
        text = text[:CLERK_TEXT_LIMIT] + "……（后面截掉了）"
    return f"网址：{page['url']}\n网站：{page['domain']}\n正文：{text}"


def _norm(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def command_on_page(command, text) -> bool:
    """命令要完整出现：前面是开头、空白或非英文字符，后面是结尾、空白或收尾符号。
    只“包含”不够：页面写 foo-evil，交来 foo 也会算找到（复查第 3 条）。"""
    command = _norm(command)
    if not command:
        return False
    return bool(re.search(r"(?:^|(?<=[^A-Za-z0-9@/._-]))" + re.escape(command) + r"(?=$|[\s\"'`<)\]。，,;；:：!！?？])",
                          _norm(text)))


# ---------- 安装命令 ----------

def parse_install(command) -> dict:
    """把官网上的一行安装命令解析成程序能直接执行的参数列表。只认三种写法，别的一律拒收。"""
    if not isinstance(command, str) or not command.strip() or len(command) > 500:
        raise BlockError("安装命令不能为空，最长 500 个字")
    raw = command.strip()
    if any(bad in raw for bad in _FORBIDDEN if bad != "sudo") or "sudo" in re.split(r"[\s|]+", raw):
        raise BlockError("安装命令里有 sudo、&&、;、反引号、$(、重定向或换行，这种不执行")
    if raw.count("|") > 1:
        raise BlockError("安装命令只能有一处“|”")
    if "|" in raw:
        return _parse_script(raw)
    try:
        words = shlex.split(raw)
    except ValueError as exc:
        raise BlockError("安装命令的引号不配对") from exc
    if words[:1] == ["npm"]:
        return _parse_npm(words)
    if words[:1] == ["brew"]:
        return _parse_brew(words)
    raise BlockError("只支持 npm、Homebrew 和“下载脚本直接运行”三种安装方式；pip、pipx 这类这一轮不支持")


def _parse_npm(words) -> dict:
    if len(words) < 3 or words[1] not in ("install", "i"):
        raise BlockError("npm 只认“npm install -g 包名”这种写法")
    rest = words[2:]
    flags = [w for w in rest if w.startswith("-")]
    names = [w for w in rest if not w.startswith("-")]
    if len(flags) != 1 or flags[0] not in ("-g", "--global"):
        raise BlockError("npm 只认全局安装（-g），也不能带别的开关（比如换下载来源）")
    if len(names) != 1:
        raise BlockError("一次只装一个 npm 包")
    spec = names[0]
    at = spec.rfind("@")
    name, version = (spec[:at], spec[at + 1:]) if at > 0 else (spec, "")
    if not _NPM_NAME.fullmatch(name) or len(name) > 214 or (version and not _NPM_VERSION.fullmatch(version)):
        raise BlockError(f"{spec} 不像一个 npm 包名（只能是小写字母、数字和 . _ -，可带 @组织/）")
    return {"type": "npm", "package": name, "version": version, "args": ["npm", "install", "-g", spec],
            "command_text": " ".join(["npm", "install", "-g", spec])}


def _parse_brew(words) -> dict:
    if len(words) < 3 or words[1] != "install":
        raise BlockError("Homebrew 只认“brew install 名字”这种写法")
    rest = words[2:]
    cask = rest[:1] == ["--cask"]
    if cask:
        rest = rest[1:]
    if len(rest) != 1:
        raise BlockError("Homebrew 一次只装一个，也不能带别的开关")
    name = rest[0]
    if "/" in name:
        raise BlockError("这是 Homebrew 的第三方仓库，不在官方目录里，查不了底细，这一轮不支持：管家会带你自己装")
    if not _BREW_NAME.fullmatch(name):
        raise BlockError(f"{name} 不像 Homebrew 里的名字")
    args = ["brew", "install"] + (["--cask"] if cask else []) + [name]
    return {"type": "brew", "name": name, "cask": cask, "args": args, "command_text": " ".join(args)}


def _parse_script(raw) -> dict:
    left, right = (part.strip() for part in raw.split("|"))
    try:
        lw, rw = shlex.split(left), shlex.split(right)
    except ValueError as exc:
        raise BlockError("安装命令的引号不配对") from exc
    if not lw or lw[0] != "curl":
        raise BlockError("“下载脚本直接运行”只认 curl 下载")
    if rw not in (["sh"], ["bash"]):
        raise BlockError("脚本只能直接交给 sh 或 bash，不能带参数，也不能交给别的程序")
    urls = [w for w in lw[1:] if "://" in w]
    others = [w for w in lw[1:] if "://" not in w]
    if len(urls) != 1 or not urls[0].startswith("https://"):
        raise BlockError("脚本地址只能有一个，并且要是 https://")
    if any(not (_CURL_FLAG.fullmatch(w) or w in _CURL_LONG) for w in others):
        raise BlockError("curl 只能带 -f -s -S -L 这类安静下载的开关，不能另存文件或改设置")
    return {"type": "script", "script_url": urls[0], "shell": rw[0],
            "script_host": urllib.parse.urlsplit(urls[0]).hostname or "", "command_text": _norm(raw)}


# ---------- 安装包底细 ----------

def _day(stamp) -> str:
    return stamp[:10] if isinstance(stamp, str) and re.match(r"\d{4}-\d{2}-\d{2}", stamp) else ""


def is_new(created, today=None) -> bool:
    if not created:
        return True
    try:
        made = date.fromisoformat(created)
        now = date.fromisoformat(today) if today else date.today()
    except ValueError:
        return True
    return (now - made).days < NEW_DAYS


def npm_info(package) -> dict:
    url = NPM_REGISTRY + urllib.parse.quote(package, safe="@")
    try:
        status, meta = _get_json(url)
    except Exception as exc:  # noqa: BLE001
        raise BlockError("查不了 npm 上的底细：连不上 npm") from exc
    if status == 404 or not isinstance(meta, dict) or not meta.get("name"):
        raise BlockError(f"npm 上查不到 {package} 这个包：可能是写错了或编出来的，不装")
    if status != 200:
        raise BlockError(f"查不了 npm 上的底细（返回 {status}）")
    def as_dict(v):
        return v if isinstance(v, dict) else {}
    latest = str(as_dict(meta.get("dist-tags")).get("latest") or "")
    bins = as_dict(as_dict(meta.get("versions")).get(latest)).get("bin")
    if isinstance(bins, dict):
        commands = sorted(k for k in bins if isinstance(k, str))
    elif isinstance(bins, str):
        commands = [package.split("/")[-1]]
    else:
        commands = []
    people = meta.get("maintainers") if isinstance(meta.get("maintainers"), list) else []
    publishers = [str(m.get("name")) for m in people if isinstance(m, dict) and m.get("name")]
    created = _day(as_dict(meta.get("time")).get("created"))
    return {"exists": True, "created": created, "latest": latest, "publishers": publishers[:5],
            "commands": commands, "new": is_new(created)}


def brew_info(name, cask) -> dict:
    url = f"{BREW_API}{'cask' if cask else 'formula'}/{urllib.parse.quote(name)}.json"
    try:
        status, meta = _get_json(url)
    except Exception as exc:  # noqa: BLE001
        raise BlockError("查不了 Homebrew 官方目录：连不上") from exc
    if status != 200 or not isinstance(meta, dict):
        raise BlockError(f"Homebrew 官方目录里没有 {name}：可能是写错了，不装")
    return {"exists": True, "desc": str(meta.get("desc") or "")[:200], "new": False}


# ---------- 搜索 ----------

def _search_failure(status) -> SearchError:
    if status in (401, 403):
        return SearchError("联网搜索的密钥不对：换一把再试", status)
    if status == 429:
        return SearchError("搜得太频繁，被 Tavily 暂时挡住了：等一会儿再试", status)
    if status in (432, 433):
        return SearchError("Tavily 的免费额度用完了：等下个月恢复，或换一把密钥", status)
    return SearchError(f"联网搜索出错了（返回 {status}），稍后再试", status)


def _tavily(url, payload, key):
    if not isinstance(key, str) or not key.strip():
        raise BlockError("还没有联网搜索的密钥")
    try:
        status, body = _post_json(url, payload, {"Authorization": f"Bearer {key.strip()}"})
    except Exception as exc:  # noqa: BLE001
        raise SearchError("连不上 Tavily：检查网络后再试") from exc
    if status != 200:
        raise _search_failure(status)
    return body if isinstance(body, dict) else {}


def search(query, key, max_results=5, time_range=None) -> list:
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise BlockError("搜索词不能为空，最长 300 个字")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not (1 <= max_results <= 20):
        raise BlockError("条数必须是 1 到 20 的整数")
    if time_range not in (None, "none", "day", "week", "month"):
        raise BlockError("时间范围不对：只能是 none、day、week 或 month")
    payload = {"query": query.strip(), "max_results": max_results}
    if time_range in ("day", "week", "month"):
        payload["time_range"] = time_range
    body = _tavily(SEARCH_URL, payload, key)
    items = []
    for r in body.get("results") or []:
        if isinstance(r, dict) and isinstance(r.get("url"), str):
            items.append({"title": str(r.get("title") or "")[:120], "url": r["url"][:500],
                          "snippet": _SECRET_TEXT.sub("[已隐藏]", str(r.get("content") or ""))[:300]})
    return items[:max_results]


def tavily_extract(url, key) -> str:
    """请 Tavily 读一页（它在自己的服务器上读，不碰本机网络），只用于核对命令。"""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise BlockError("只能读 https:// 开头的网址")
    body = _tavily(EXTRACT_URL, {"urls": [url]}, key)
    for r in body.get("results") or []:
        if isinstance(r, dict) and isinstance(r.get("raw_content"), str):
            return r["raw_content"][:MAX_PAGE_BYTES]
    return ""


# ---------- 核对一条网上找来的安装命令 ----------

def verify_install(url, install, command=None, search_key=None) -> dict:
    """核对行政找来的安装命令：程序自己读那一页，命令要一字不差在原文里（空白不算）；
    再查安装包底细。都过了返回提议要用的说明；哪一步不过就抛 BlockError，不出提议。
    command 是装好后要找的命令名：npm 不写就用包里登记的；脚本安装必须写。"""
    spec = parse_install(install)
    page = read_page(url)
    found_by = "page"
    if not command_on_page(spec["command_text"], page["text"]) and not command_on_page(install, page["text"]):
        extra = ""
        if search_key:
            try:
                extra = tavily_extract(page["url"], search_key)
            except BlockError:
                extra = ""
        if extra and (command_on_page(spec["command_text"], extra) or command_on_page(install, extra)):
            found_by = "tavily"
        else:
            raise BlockError("在这一页的原文里没找到这条命令：换成写着安装命令的那一页，或者你自己装")
    package = None
    if spec["type"] == "npm":
        package = npm_info(spec["package"])
        default = package["commands"][0] if len(package["commands"]) == 1 else ""
        if not default and package["commands"] and spec["package"].split("/")[-1] in package["commands"]:
            default = spec["package"].split("/")[-1]
        command = command or default
    elif spec["type"] == "brew":
        package = brew_info(spec["name"], spec["cask"])
        command = command or spec["name"]
    else:
        check_url(spec["script_url"])
        if not command:
            raise BlockError("脚本安装要说清装好后的命令名，程序才找得到它")
    if not isinstance(command, str) or not _COMMAND.fullmatch(command):
        raise BlockError("装好后的命令名不对：只能写可执行文件的名字")
    return dict(spec, source=page["url"], domain=page["domain"], command=command, package=package,
                found_by=found_by, danger=spec["type"] == "script")
