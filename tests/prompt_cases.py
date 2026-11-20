"""规则包第③步：把程序发给模型的话按各种情况拼出来，用来证明“搬家前后一字不差”。

每个角色一组情况；临时文件夹路径换成 <TMP>，别的原样。快照存在 tests/fixtures/交代快照/<角色>.json，
由 tests/test_prompt_snapshots.py 比对。要有意改说法时，改完用
`python3 tests/prompt_cases.py <角色>` 重新生成快照，并在提交里写明改了什么。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURE = HERE / "fixtures" / "card019"
SNAPSHOTS = HERE / "fixtures" / "交代快照"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


class _Place:
    """一个临时项目：git 仓库（副本）+ 卡片文件夹。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)
        self.wt = self.root / "副本"
        self.wt.mkdir()
        _git(self.wt, "init", "-q")
        (self.wt / "tests").mkdir()
        (self.wt / "tests" / "test_old.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")
        (self.wt / "app.py").write_text("x = 1\n", encoding="utf-8")
        _git(self.wt, "add", ".")
        _git(self.wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.cdir = self.root / "卡片"
        self.cdir.mkdir()
        (self.cdir / "需求卡.md").write_text("# 改按钮\n\n## 要做什么\n改颜色\n", encoding="utf-8")
        self.needs = self.cdir / "需求卡.md"

    def with_card019_flow(self):
        shutil.copy(FIXTURE / "流转.jsonl", self.cdir / "流转.jsonl")
        return self

    def norm(self, text: str) -> str:
        real = str(Path(self.tmp).resolve())
        return str(text).replace(real, "<TMP>").replace(self.tmp, "<TMP>")

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def review_cases() -> dict:
    from collab import dispatch as d
    out = {}

    p = _Place()
    try:
        out["每轮复查·第1轮·干净"] = p.norm(d._review_prompt(p.cdir, p.needs, "", "", p.wt, False))
        out["每轮复查·第1轮·带测试命令"] = p.norm(d._review_prompt(
            p.cdir, p.needs, d._plan_hint(p.cdir), d._run_hint(["python3", "-m", "unittest", "tests.test_a"]), p.wt, False))
        out["每轮复查·退回后·没有上轮核对"] = p.norm(d._review_prompt(p.cdir, p.needs, "", "", p.wt, True))
        (p.cdir / "方案.md").write_text("# 拆解方案\n按施工单做：docs/x/施工单-x.md\n", encoding="utf-8")
        (p.cdir / "交接资料.md").write_text("## 要改的文件\n", encoding="utf-8")
        out["每轮复查·第1轮·带方案和交接资料"] = p.norm(d._review_prompt(
            p.cdir, p.needs, d._plan_hint(p.cdir), d._run_hint("python3 -m unittest tests.test_a"), p.wt, False))
        (p.cdir / "方案.md").unlink()
        (p.cdir / "交接资料.md").unlink()
        (p.cdir / "测试文件.json").write_text(json.dumps(["tests/test_new.py"], ensure_ascii=False), encoding="utf-8")
        out["每轮复查·带测试员的文件"] = p.norm(d._review_prompt(p.cdir, p.needs, "", "", p.wt, False))
        out["补格式·没有上轮"] = p.norm(d._reformat_prompt(p.cdir, "原回复全文", ["缺复查清单", "结论不对"], p.needs, p.wt))
        # 老测试被改、新加跳过
        backup = p.cdir / d._TEST_BACKUP / "tests"
        backup.mkdir(parents=True)
        (backup / "test_kept.py").write_text("def test_k():\n    assert 1\n", encoding="utf-8")
        (p.wt / "tests" / "test_kept.py").write_text("def test_k():\n    assert 0\n", encoding="utf-8")
        (p.wt / "tests" / "test_old.py").write_text(
            "import unittest\n\n@unittest.skip('x')\ndef test_a():\n    assert 1\n", encoding="utf-8")
        out["老测试被改"] = p.norm(d._old_tests_changed_note(p.cdir, p.wt))
        out["新加跳过"] = p.norm(d._new_skips_note(p.wt))
        out["每轮复查·老测试被改且新加跳过"] = p.norm(d._review_prompt(p.cdir, p.needs, "", "", p.wt, False))
    finally:
        p.close()

    p = _Place().with_card019_flow()
    try:
        out["每轮复查·退回后·核对上轮"] = p.norm(d._review_prompt(p.cdir, p.needs, "", "", p.wt, True))
        out["补格式·有上轮"] = p.norm(d._reformat_prompt(p.cdir, "原回复全文", ["缺复查清单"], p.needs, p.wt))
    finally:
        p.close()
    return out


def _review_reply(verdict, items=()):
    block = json.dumps({"结论": verdict, "问题": list(items)}, ensure_ascii=False, indent=1)
    return f"结论：{verdict}\n\n复查过程：看了改动。\n\n复查清单：\n```json\n{block}\n```\n"


def _item(level="严重", kind="功能问题", what="保存后草稿丢失", where="app.py:1"):
    return {"级别": level, "来源": "这次改的", "类型": kind, "碰上": "常见", "现象": what, "位置": where,
            "证据": {"方式": "实跑", "命令": "python3 -m unittest tests.test_a", "输出": "FAIL: test_draft"}}


def _run_flow(name, reviews, verdicts, need_extra="", plan=None, dev="cursor", test_command=("true",),
              old_tests=(), conflicts=(), guard_fails=False, red_check=False):
    """真的跑一张卡（假工具、假判定），按顺序记下每个角色收到的话。"""
    from collab.agents import AgentResult
    from collab.dispatch import run_card
    from collab.judge import FAIL, PASS, Verdict
    from collab.state import Card
    from collab.workspace import init_collab
    p = _Place()
    try:
        collab = init_collab(str(p.wt))
        cdir = collab / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        (cdir / "需求卡.md").write_text("# 改按钮\n\n## 要做什么\n改颜色\n" + need_extra, encoding="utf-8")
        if plan:
            (cdir / "方案.md").write_text(plan, encoding="utf-8")
        if old_tests:   # 之前已经写好的测试
            (cdir / "测试文件.json").write_text(json.dumps(list(old_tests), ensure_ascii=False), encoding="utf-8")
        if conflicts:   # 同步项目新内容时有冲突的文件
            (cdir / "同步冲突.json").write_text(json.dumps(list(conflicts), ensure_ascii=False), encoding="utf-8")
        if need_extra:   # “继续改”加的要求：程序从流转里的“追加要求”读
            from collab.flow import record
            record(cdir, "追加要求", src="你", dst="调度员", summary="按钮再大一点", text="按钮再大一点")
        got, n = {}, [0]
        reviews = list(reviews)
        verdicts = list(verdicts)

        def ask(adapter, prompt, cwd, **kw):
            n[0] += 1
            role = "复查员" if "只读复查" in prompt else ("写代码" if adapter == dev else "写测试")
            got[f"{name}·{n[0]:02d}·{role}"] = p.norm(prompt)
            if role == "复查员":
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0) if reviews else _review_reply("通过"))
            if role == "写测试" and guard_fails and "复查退回、要改产品代码的问题" in prompt:
                return AgentResult(ok=False, output="没写成", attempts=1, final="")
            if role == "写测试":
                (p.wt / "tests" / "test_new.py").write_text("def test_n():\n    assert 1\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="汇报：写了测试")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        def judge(cmd, cwd, timeout):
            v = verdicts.pop(0) if verdicts else ("pass", "")
            return Verdict(PASS, 0, v[1]) if v[0] == "pass" else Verdict(FAIL, 1, v[1])

        run_card(Card("CARD-001", state="待派发"), collab_dir=collab, worktree=p.wt,
                 test_command=list(test_command) if isinstance(test_command, tuple) else test_command,
                 dev_adapter=dev, test_adapter="codex", ask_fn=ask, judge_fn=judge, reviewer_adapter="agy",
                 red_check=red_check)
        return got
    finally:
        p.close()


def flow_cases() -> dict:
    out = {}
    fail = ("fail", "FAIL: test_draft (tests.test_a.T)\nAssertionError: 草稿没了\n\nRan 1 test\nFAILED (failures=1)")
    out.update(_run_flow("测试没过再过·复查退回一次", [_review_reply("不通过", [_item()]), _review_reply("通过")],
                         [fail, ("pass", "OK")]))
    out.update(_run_flow("复查说测试写错", [_review_reply("不通过", [_item(level="中等", kind="测试写错",
                                                                              what="【测试的问题】测试把颜色写反了",
                                                                              where="tests/test_new.py:2")]),
                                          _review_reply("通过")], []))
    out.update(_run_flow("带方案和追加要求·不是 cursor", [], [],
                         need_extra="按钮再大一点", plan="# 拆解方案\n照做\n", dev="qwen",
                         test_command="python3 -m unittest tests.test_new"))
    out.update(_run_flow("已有测试再加要求·有冲突文件", [], [], need_extra="按钮再大一点",
                         old_tests=["tests/test_old.py"], conflicts=["app.py"]))
    out.update(_run_flow("补测试没成功", [_review_reply("不通过", [_item()]), _review_reply("通过")], [],
                         guard_fails=True))
    out.update(_run_flow("测试一跑就过·要重写", [], [("pass", "OK"), ("pass", "OK"), ("pass", "OK")], red_check=True))
    return out


def small_cases() -> dict:
    """写方案、备交接资料、起草交代。"""
    from collab import dispatch as d
    from collab.planner import _build_prompt
    out = {}
    p = _Place().with_card019_flow()
    try:
        need = "# 改按钮\n\n## 要做什么\n改颜色\n"
        out["写方案·普通"] = p.norm(_build_prompt(p.cdir, need, False, project=p.wt))
        out["写方案·要写项目说明"] = p.norm(_build_prompt(p.cdir, need, True, project=p.wt))
        (p.wt / "docs" / "x").mkdir(parents=True)
        (p.wt / "docs" / "x" / "施工单-x.md").write_text("# 施工单\n", encoding="utf-8")
        out["写方案·有施工单"] = p.norm(_build_prompt(p.cdir, "按施工单做：docs/x/施工单-x.md\n" + need, False, project=p.wt))
        out["备交接资料·没有方案"] = p.norm(d._handover_prompt(p.needs, p.cdir))
        (p.cdir / "方案.md").write_text("# 拆解方案\n", encoding="utf-8")
        out["备交接资料·有方案"] = p.norm(d._handover_prompt(p.needs, p.cdir))
        got = {}

        def ask(adapter, prompt, cwd, **kw):
            from collab.agents import AgentResult
            got["话"] = prompt
            return AgentResult(ok=True, output="", attempts=1, final="草稿")

        d._draft_revise(ask, p.cdir, "codex", None, p.wt, p.needs, lambda wt: ["app.py", "chat.js"], 3)
        out["起草交代·有几轮复查结论"] = p.norm(got.get("话", ""))
        got.clear()
        d._draft_revise(ask, p.cdir, "codex", None, p.wt, p.needs, lambda wt: [], 3)
        out["起草交代·看不出改了哪些文件"] = p.norm(got.get("话", ""))
    finally:
        p.close()
    return out


def clerk_cases() -> dict:
    """行政：身份说明、起标题、系统提醒、看卡时的补充、团队没配好、改要求拆分、工具说明。"""
    from types import SimpleNamespace
    from desktop.clerk import desk, tools, revision
    from collab.state import Card
    from collab.workspace import init_collab
    out = {}
    p = _Place()
    try:
        out["身份说明"] = desk.ROLE.format(name="开发助手")
        out["起标题"] = desk.NAMING.format(user="帮我改按钮", reply="好的")
        out["步骤用完"] = desk._LAST_ROUND
        out["没出提议却说出了"] = desk._claim_reminder([])
        out["没出提议却说出了·上面还有提议"] = desk._claim_reminder([{"title": "改按钮"}, {"name": "高德"}])
        out["工具说明"] = json.dumps(tools.TOOLS + [tools.PROPOSE_REVISE], ensure_ascii=False, indent=1)
        out["改要求拆分·有标准"] = json.dumps(revision.build_revision_prompt(["按钮变红", "字变大"], "第一条改成蓝色"),
                                         ensure_ascii=False, indent=1)
        out["改要求拆分·没标准·带截图"] = json.dumps(revision.build_revision_prompt([], "照截图改", 2),
                                             ensure_ascii=False, indent=1)
        project = str(p.wt)
        collab = init_collab(project)
        out["团队没配好"] = p.norm(tools.team_incomplete_hint(project))
        (collab / "配置.json").write_text("{坏的", encoding="utf-8")
        out["团队配置读不出"] = p.norm(tools.team_incomplete_hint(project))
        (collab / "配置.json").unlink()
        cdir = collab / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        (cdir / "需求卡.md").write_text("# 改按钮\n\n## 要做什么\n改颜色\n", encoding="utf-8")
        Card("CARD-001", state="需Claude介入").save(cdir / "状态.json")
        conv = SimpleNamespace(project=project, id="c1", manuals=[], messages=[])
        fake = SimpleNamespace(_card_of=lambda c: "", _index_entry=lambda pr, cid: {})
        out["整段系统提示·主对话"] = p.norm(desk.ClerkDesk._prompt(fake, conv)[0]["content"])
        fake = SimpleNamespace(_card_of=lambda c: "CARD-001", _index_entry=lambda pr, cid: {"card": "CARD-001"})
        out["整段系统提示·看卡"] = p.norm(desk.ClerkDesk._prompt(fake, conv)[0]["content"])
    finally:
        p.close()
    return out


def butler_cases() -> dict:
    """积木库管家：身份说明、4 本手册、工具说明、积木库窗口里的整段系统提示、能力卡片试做提示。"""
    from types import SimpleNamespace
    from unittest import mock
    from desktop.clerk import blocks_tools, desk
    from desktop.clerk import tasks as clerk_tasks
    out = {"身份说明": blocks_tools.role_text()}
    for name in sorted(blocks_tools.MANUALS):
        out[f"手册·{name}"] = blocks_tools.manual_block(name)
    out["工具说明"] = json.dumps(blocks_tools.TOOLS, ensure_ascii=False, indent=1)
    p = _Place()
    try:
        home = str(p.wt)
        folder = Path(home) / clerk_tasks.FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(blocks_tools.blocks, "home", lambda *a, **k: Path(home)):
            conv = SimpleNamespace(project=home, id="c1", manuals=["cli"], messages=[])
            fake = SimpleNamespace(_card_of=lambda c: "", _index_entry=lambda pr, cid: {})
            out["整段系统提示·没有提议"] = p.norm(desk.ClerkDesk._prompt(fake, conv)[0]["content"])
            fake = SimpleNamespace(_card_of=lambda c: "", _index_entry=lambda pr, cid: {"block": "b1"})
            (folder / "p1.json").write_text(json.dumps({"id": "p1", "conversation": "c1", "kind": "connect", "state": "open",
                                                        "name": "高德", "time": 1}, ensure_ascii=False), encoding="utf-8")
            out["整段系统提示·从积木块开的·有提议"] = p.norm(desk.ClerkDesk._prompt(fake, conv)[0]["content"])
        (folder / "p1.json").unlink()
        (folder / "a1.json").write_text(json.dumps({"id": "a1", "conversation": "c1", "kind": "ability", "state": "confirmed",
                                                    "name": "翻译", "time": 2, "decided": 2,
                                                    "trial": {"ok": True}}, ensure_ascii=False), encoding="utf-8")
        out["试做提示·成了"] = clerk_tasks.latest_ability_hint(home, "c1")
        (folder / "a1.json").write_text(json.dumps({"id": "a1", "conversation": "c1", "kind": "ability", "state": "confirmed",
                                                    "name": "翻译", "time": 2, "decided": 2,
                                                    "trial": {"ok": False, "reason": "密钥不对"}}, ensure_ascii=False),
                                        encoding="utf-8")
        out["试做提示·没成"] = clerk_tasks.latest_ability_hint(home, "c1")
    finally:
        p.close()
    return out


def extra_cases() -> dict:
    """附图提示、同步冲突提示（Codex 复查 T2～T6 第 3 条补搬）。"""
    from collab import dispatch as d
    from collab.card_attachments import attachment_prompt
    from collab.flow import record
    out = {}
    p = _Place()
    try:
        att = p.cdir / "附件"
        att.mkdir()
        (att / "1-1.png").write_bytes(b"x")
        (att / "2.png").write_bytes(b"y")
        record(p.cdir, "追加要求", src="你", summary="照图改", attachments=["附件/1-1.png", "附件/2.png"])
        out["附图提示"] = p.norm(attachment_prompt(p.cdir))
        (p.cdir / "同步冲突.json").write_text(json.dumps(["app.py", "tests/test_old.py"]), encoding="utf-8")
        (p.wt / "app.py").write_text("<<<<<<< 项目\nx = 2\n=======\nx = 3\n>>>>>>> 卡片\n", encoding="utf-8")
        (p.wt / "tests" / "test_old.py").write_text("<<<<<<< a\n=======\n>>>>>>> b\n", encoding="utf-8")
        out["同步冲突提示"] = p.norm(d._conflict_note(p.wt, p.cdir))
    finally:
        p.close()
    return out


CASES = {"复查": review_cases, "跑卡": flow_cases, "写方案等": small_cases, "行政": clerk_cases, "积木库管家": butler_cases,
         "附图和冲突": extra_cases}


def render(role: str) -> dict:
    return CASES[role]()


if __name__ == "__main__":
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    for role in (sys.argv[1:] or list(CASES)):
        path = SNAPSHOTS / f"{role}.json"
        path.write_text(json.dumps(render(role), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("已写", path)
