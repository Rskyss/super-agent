"""老测试用的复查意见转换（C1.5，2026-11-18 用户批准，见 docs/跑卡流程调整/方案-C1.5.md 第七节）。

老测试用“随手写的复查意见”模拟复查员（“结论：不通过\n1. 【中等】【这次改的】按钮没反应。”）。
C1.5 起程序只认回复末尾的固定清单（```json 代码块）。这里把老意见按 C1 的认法翻成同义的清单附在后面，
老测试的意思不变。程序还没有 read_review_list（C1.5 施工前）时什么都不做，老代码、新代码下都能跑。

不转换的：已经带清单的；没有“结论：通过/不通过”的（比如“还没有结论”，本来就该被当成没结论）。
"""
import json
import re

_VERDICT_LINE = re.compile(r"^\s*[*#>]*\s*结论\s*[：:]\s*\**\s*(?:不通过|通过)\s*\**\s*[。.！!]?\s*$")   # 只去掉结论那一行
_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.S)
_LOC = re.compile(r"([\w./\-]+\.[A-Za-z]{1,5}):(\d+)")


def _enabled() -> bool:
    from collab import dispatch
    return hasattr(dispatch, "read_review_list")


def _where(text: str) -> str:
    """位置：有链接就取链接地址（和 C1 的 _places 一样，显示文字不算），没有再找裸写的 文件:行号。"""
    m = re.search(r"\]\(([^)\s]+?):(\d+)(?::\d+)?\)", text) or _LOC.search(re.sub(r"\[[^\]]*\]\([^)]*\)", " ", text))
    return f"{m.group(1)}:{m.group(2)}" if m else ""


def _issue(text: str, level: str = "", whole: bool = False) -> dict:
    from collab.dispatch import _lead_tags, _level
    tags = [] if whole else _lead_tags(text)   # 整段原文那条不看开头标签：开头那条的标签不能代表整段（复查确认轮 1、2）
    level = level or _level(text)                                           # 和 C1 同一个判法（双标签取 C1 的结果）
    if not whole and not any(w in tags for w in ("严重", "中等", "轻微")) and re.match(r"\s*\**\s*\[P3\]", text):   # 只认开头带方括号的 [P3]
        level = "轻微"   # 没写【】级别、写 [P3] 的：C1 停下时按“小问题”认（P0–P2 C1 按中等收口，不动）
    source = "原来就有的" if "原来就有的" in tags else "这次改的"            # 拿不准的算这次改的（C1 口径）
    kind = "测试写错" if "测试的问题" in tags else "功能问题"
    body = re.sub(r"^\s*(?:【[^】]{1,12}】\s*)+", "", text).strip() or text.strip()
    where = "" if whole else _where(text)   # 整段那条不拿首条的位置充数（复查第 3 轮）
    # 老测试的模拟意见大多没写出处：如实标“占位”，只为满足清单格式，不代表真有依据
    proof = where or "tests/review_fmt.py:1（占位：老测试的模拟复查意见没有真实出处）"
    return {"级别": level, "来源": source, "类型": kind, "现象": body, "位置": where or "（原意见没写位置）",
            "证据": {"方式": "推演", "对照": proof}}


def to_list(text: str):
    """按 C1 的认法把老意见翻成清单；翻不了返回 None。"""
    from collab.dispatch import _review_items, _unparsed_serious, review_verdict
    verdict = review_verdict(text or "")
    if verdict not in ("pass", "reject"):
        return None
    if verdict == "pass":
        return {"结论": "通过", "问题": []}
    items = _review_items(text)
    issues = [_issue(t) for t in items]
    if not items or _unparsed_serious(text, items):
        # C1 认不全时整段原文退回：认出来的几条照旧（保住“测试的问题”分流），另加一条整段原文，
        # 级别取原文里出现过的最重的（保住“严重”，停下提示不能说成小问题）
        found = re.findall(r"【(严重|中等|轻微)】", text or "")
        level = next((w for w in ("严重", "中等", "轻微") if w in found), "中等")
        keep, fenced = [], False
        for ln in (text or "").splitlines():
            if ln.strip().startswith("```"):
                fenced = not fenced
            if fenced or not _VERDICT_LINE.match(ln):   # 代码块里引用的输出原样保留（复查第 3 轮）
                keep.append(ln)
        whole = "\n".join(keep).strip()
        issues.append(_issue(whole or text, level=level, whole=True))
    return {"结论": "不通过", "问题": issues}


def _has_list(text: str) -> bool:
    """最后一个 json 代码块已经是清单（有“结论”）就不再加；普通的 json 示例不算（复查确认轮 3）。"""
    blocks = _BLOCK.findall(text or "")
    if not blocks:
        return False
    try:
        data = json.loads(blocks[-1])
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("结论") in ("通过", "不通过") and isinstance(data.get("问题"), list)


PREV_MARK = "上轮退回的问题（按编号）："


def prev_items(prompt: str) -> list:
    """C1.6：第 2 轮起复查交代里按编号列出上轮退回的问题（“上轮退回的问题（按编号）：”下面一行一条“N. 现象”）。"""
    prompt = str(prompt or "")
    if PREV_MARK not in prompt:
        return []
    out = []
    for line in prompt.split(PREV_MARK, 1)[1].splitlines():
        m = re.match(r"\s*(\d+)[.、]\s*(.+)", line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
        elif out and not line.strip():
            break
    return out


def _check_back(data: dict, prev: list) -> dict:
    """老测试的随手意见没有“上轮核对”：照规矩办事的复查员会怎么答，就替它怎么答——
    这轮又提到的（现象和上轮那条相近）答“没改好”并从新问题里拿掉，没再提的答“改好了”（老测试原来就是这个意思）。"""
    import difflib
    if not prev or data.get("结论") != "不通过":
        return data
    def words(text):   # 只比现象那句：去掉级别标签、链接和“位置：”之后的部分（位置写法相近不算同一个问题）
        text = re.sub(r"【[^】]*】|\[[^\]]*\]\([^)]*\)", "", str(text or ""))
        return re.split(r"位置[:：]", text, maxsplit=1)[0].strip()

    issues = list(data.get("问题") or [])
    # 一对一配：先把最像的一对配上，每条上轮问题、每条本轮问题都只用一次（不让先轮到的编号抢走别人的配对）
    pairs = sorted(((difflib.SequenceMatcher(None, words(p.get("现象")), words(old)).ratio(), number, i)
                    for number, old in prev for i, p in enumerate(issues)), reverse=True)
    matched, used = {}, set()
    for ratio, number, i in pairs:
        if ratio < 0.6 or number in matched or i in used:
            continue
        matched[number], used = i, used | {i}
    checks = [{"编号": number, "结果": "没改好" if number in matched else "改好了"} for number, _ in prev]
    # 配上的本轮条目算“上轮没改好”，从新问题里拿掉；但严重的、测试写错的留着当新问题（照样必退，不能被旧编号吞掉）
    keep = [p for i, p in enumerate(issues)
            if i not in used or p.get("级别") == "严重" or p.get("类型") == "测试写错"]
    return dict(data, 上轮核对=checks, 问题=keep)


def listed(text, force: bool = False, prompt: str = ""):
    if not isinstance(text, str) or _has_list(text) or not (force or _enabled()):
        return text
    data = to_list(text)
    if data is None:
        return text
    data = _check_back(data, prev_items(prompt))
    return f"{text}\n\n复查清单：\n```json\n{json.dumps(data, ensure_ascii=False, indent=1)}\n```\n"


def listed_ask(ask_fn):
    """包一层假的派活：只改复查员（交代里有“只读复查”）的回复，别的原样返回。"""
    def wrapped(adapter, prompt, cwd, **kw):
        result = ask_fn(adapter, prompt, cwd, **kw)
        if "只读复查" not in str(prompt):
            return result
        from collab.dispatch import reply_text
        text = reply_text(result)            # 和程序取复查回复一样：没有 final 就从 output 里取（含 agy 的 JSON 包装）
        new = listed(text, prompt=prompt)
        if new == text:
            return result
        try:
            result.final = new
        except (AttributeError, TypeError):   # 冻结的结果对象：换一个
            from dataclasses import replace
            result = replace(result, final=new)
        return result
    # 程序用 inspect.signature 看假派活认哪些参数（on_quiet 等，dispatch._accepts）；
    # 设了 __wrapped__，signature 看到的是原函数的参数，传进来的也只会是原函数认的
    wrapped.__wrapped__ = ask_fn
    return wrapped


def listed_script(body: str) -> str:
    """命令行式老测试的假工具（shell 脚本）：原脚本照跑，输出里有“结论：通过/不通过”时补上同义清单。
    原脚本的退出码保留；没有结论的输出（“写好了”）原样不动。给 acceptance_helpers.write_script 用。"""
    if "结论" not in body:
        return body
    import shlex
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    run = f"PYTHONPATH={shlex.quote(str(here.parent))} {shlex.quote(sys.executable)} {shlex.quote(str(here / 'review_fmt.py'))}"
    return (f"__out=$(\n(\n{body}\n)\n)\n__rc=$?\n"
            f"printf '%s\\n' \"$__out\" | __REVIEW_PROMPT=\"$*\" {run}\nexit $__rc\n")


if __name__ == "__main__":   # 假工具脚本里用：读标准输入的复查回复，补上清单再打印
    import sys
    import os
    sys.stdout.write(listed(sys.stdin.read().rstrip("\n"), prompt=os.environ.get("__REVIEW_PROMPT", "")) + "\n")
