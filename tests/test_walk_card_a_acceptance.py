"""P1 卡 A 验收测试（写施工单的角色写，施工方不许改）：跑卡流程接上走查。

施工单：docs/走查截图/施工单-卡A.md。
- 施工单“验收”一节写了“- 走查命令：`...`”，照施工单生成方案时记进开工资料（别的节里写了不算）；
- 开工时程序自己从开工资料读走查命令（命令行没给 --page-check 时）；
- 本卡测试过了就跑走查：每次给一个新的截图文件夹（卡片文件夹 走查截图/第K次），跑完读步骤清单；
  命令没过、没留下截图、清单里哪条不对，都像测试没过一样退回写代码的；
- 复查员的交代里列出这次交活的每一步截图和设计图；
- 本单不改右栏界面：走查刚过或没过时右栏照现有逻辑显示走查结果，全部测试跑完后照旧显示全部测试结果（不末尾拼接）；
- 没写走查命令的卡，和现在一样。
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import ProjectCase
from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow, record
from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, Verdict
from collab.state import Card
from collab.workspace import init_collab
from review_fmt import listed_ask

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
WALK_CMD = ["/bin/bash", "-o", "pipefail", "-c", "python3 tests/walk_x.py"]


def shots_dir_of(command):
    """从程序跑走查的命令里取出它交给走查脚本的截图文件夹（WALK_SHOTS_DIR=...）。"""
    for part in command:
        if isinstance(part, str) and part.startswith("WALK_SHOTS_DIR="):
            return Path(part.split("=", 1)[1])
    return None


def full_text(cdir, ev):
    """流转记录里 text 存的是全文文件的位置（流转/xxx.md），读出全文。"""
    text = str(ev.get("text") or "")
    path = Path(cdir) / text
    return path.read_text(encoding="utf-8") if text and path.is_file() else text


def write_walk(folder, steps):
    """假走查脚本做的事：往截图文件夹里放 png 和步骤清单。steps=[(步骤名, 设计图相对路径)]"""
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for i, (name, design) in enumerate(steps, 1):
        shot = f"{i:02d}-{name}.png"
        (folder / shot).write_bytes(PNG)
        items.append({"步骤": name, "截图": shot, "设计图": design})
    (folder / "走查.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


class FakeFullRunner:
    """假的“全部测试”：照真的 FullRunner 接口（run、base），直接给结果。"""

    def __init__(self, result=PASS):
        self.result = result

    def run(self, worktree, stop=None):
        out = "Ran 10 tests\n\nOK\n" if self.result == PASS else \
            "FAIL: test_a (test_old.T)\n----\nTraceback\nRan 10 tests in 1s\n\nFAILED (failures=1)\n"
        return Verdict(self.result, 0 if self.result == PASS else 1, out)

    def base(self, worktree, files, stop=None):
        return {f: Verdict(PASS, 0, "OK") for f in files}


class WalkRunCase(unittest.TestCase):
    """直接跑 run_card：假的写测试、写代码、复查，假的测试和走查判定。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp) / "项目"
        self.root.mkdir()
        self.collab = init_collab(self.root)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("改右栏", encoding="utf-8")
        self.worktree = self.root
        (self.worktree / "docs").mkdir()
        (self.worktree / "docs" / "设计图.png").write_bytes(PNG)
        (self.worktree / "tests").mkdir()
        (self.worktree / "tests" / "test_old.py").write_text("# 老测试（有它程序才会跑全部测试）\n", encoding="utf-8")
        (Path(self.tmp) / "外面.png").write_bytes(PNG)   # 副本外面真有一张图：越界的设计图不能因为文件存在就放过
        self.prompts = []
        self.walk_commands = []
        self.walk_results = []   # 每次走查的结果：("ok", steps) / ("fail", 输出) / ("nothing", None) / ("raw", 清单内容)
        self.reviews = []        # 复查员依次的回复，没有了就“结论：通过”

    def ask(self, adapter, prompt, cwd, **kwargs):
        self.prompts.append((adapter, prompt))
        if "只读复查" in prompt:
            return AgentResult(ok=True, output="", attempts=1,
                               final=self.reviews.pop(0) if self.reviews else "结论：通过")
        return AgentResult(ok=True, output="写好了", attempts=1, final="汇报：改了")

    def judge(self, command, cwd, timeout):
        folder = shots_dir_of(command)
        if folder is None:   # 本卡测试
            return Verdict(PASS, 0, "测试全过")
        self.walk_commands.append(list(command))
        kind, data = self.walk_results.pop(0) if self.walk_results else ("ok", [("右栏", "")])
        if kind == "ok":
            write_walk(folder, data)
            return Verdict(PASS, 0, "走查完了")
        if kind == "fail":
            return Verdict(FAIL, 1, data)
        if kind == "nothing":   # 命令过了，什么都没留下
            folder.mkdir(parents=True, exist_ok=True)
            return Verdict(PASS, 0, "走查完了")
        if kind == "raw":   # 清单原样写进去（可能是坏的）
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "01-右栏.png").write_bytes(PNG)
            (folder / "走查.json").write_text(data, encoding="utf-8")
            return Verdict(PASS, 0, "走查完了")
        if kind == "symlink":   # 截图名字合法，其实是指到文件夹外面那张图的链接
            write_walk(folder, data)
            for png in folder.glob("*.png"):
                png.unlink()
                png.symlink_to(Path(self.tmp) / "外面.png")
            return Verdict(PASS, 0, "走查完了")
        if kind in ("timeout", "env"):
            folder.mkdir(parents=True, exist_ok=True)
            return Verdict(TIMEOUT if kind == "timeout" else ENV_ERROR, None, "走查工具：打不开本机 Chrome")
        if kind == "missing_shot":   # 清单里写了截图，文件却不在
            write_walk(folder, data)
            for png in folder.glob("*.png"):
                png.unlink()
            return Verdict(PASS, 0, "走查完了")
        raise AssertionError(kind)

    def run_walk(self, walk=True, reviewer=None, full_runner=None):
        extra = {"walk_command": list(WALK_CMD)} if walk else {}   # 没走查的卡照老样子调用，不传新参数
        card, _ = run_card(Card("CARD-001", state="待派发"), collab_dir=self.collab, worktree=str(self.worktree),
                           test_command=["fake-test"], dev_adapter="cursor", test_adapter="codex",
                           ask_fn=listed_ask(self.ask), judge_fn=self.judge, reviewer_adapter=reviewer,
                           full_runner=full_runner, **extra)
        return card

    def walk_events(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "页面检查" and e.get("walk")]

    def check_station(self):
        from desktop.view import card_view
        view = card_view(str(self.root), "CARD-001")
        return next(s for s in view["stations"] if s["key"] == "check")


class WalkPassTest(WalkRunCase):
    def test_pass_records_shots_and_goes_to_review(self):
        self.walk_results = [("ok", [("右栏打开卡片", "docs/设计图.png"), ("点开交接", "")])]
        card = self.run_walk()
        self.assertEqual(card.state, "待验收")
        events = self.walk_events()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.get("result"), PASS)
        self.assertEqual(ev.get("summary"), "走查过了，截了 2 张图")
        self.assertEqual(ev.get("folder"), "走查截图/第1次")
        self.assertEqual([s["步骤"] for s in ev["shots"]], ["右栏打开卡片", "点开交接"])
        self.assertEqual(ev["shots"][0]["截图"], "走查截图/第1次/01-右栏打开卡片.png")
        self.assertEqual(ev["shots"][0]["设计图"], "docs/设计图.png")
        self.assertEqual(ev["shots"][1]["设计图"], "")
        for shot in ev["shots"]:
            self.assertTrue((self.cdir / shot["截图"]).is_file())
        # 页面检查结果里写了步骤清单（归档时带着）
        log = (self.cdir / "页面检查结果.md").read_text(encoding="utf-8")
        self.assertIn("右栏打开卡片", log)
        self.assertIn("01-右栏打开卡片.png", log)

    def test_shots_folder_is_new_each_time_in_card_folder(self):
        (self.cdir / "走查截图" / "第1次").mkdir(parents=True)   # 之前被停下的那次留下的半截文件夹
        (self.cdir / "走查截图" / "第1次" / "半截.png").write_bytes(PNG)
        self.walk_results = [("ok", [("右栏", "")])]
        self.run_walk()
        self.assertEqual(len(self.walk_commands), 1)
        self.assertEqual(shots_dir_of(self.walk_commands[0]).resolve(), (self.cdir / "走查截图" / "第2次").resolve())
        self.assertTrue((self.cdir / "走查截图" / "第1次" / "半截.png").is_file(), "旧文件夹不许清空")
        # 走查命令本身原样跑，程序只在前面加上截图文件夹
        self.assertEqual(self.walk_commands[0][-len(WALK_CMD):], WALK_CMD)

    def test_card_without_walk_is_unchanged(self):
        card = self.run_walk(walk=False)
        self.assertEqual(card.state, "待验收")
        self.assertEqual(self.walk_commands, [])
        self.assertEqual(self.walk_events(), [])
        self.assertFalse((self.cdir / "走查截图").exists())


class WalkFailTest(WalkRunCase):
    def test_failed_walk_is_a_rework_with_plain_reason(self):
        self.walk_results = [("fail", "Traceback...\nwalk_tool.WalkError: “右栏”这一步页面上没看到“确认没问题”"),
                             ("ok", [("右栏", "")])]
        card = self.run_walk()
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 1)
        events = self.walk_events()
        self.assertEqual([e.get("result") for e in events], [FAIL, PASS])
        self.assertEqual(events[0]["summary"], "走查没过：卡在“右栏”这一步")
        self.assertEqual(events[0]["shots"], [])
        self.assertEqual(events[1]["shots"][0]["截图"], "走查截图/第2次/01-右栏.png")
        self.assertIn("页面上没看到“确认没问题”", events[0].get("reason", ""))
        # 写代码的第二轮拿到了走查没过的原话和完整输出的位置
        dev_prompts = [p for a, p in self.prompts if a == "cursor"]
        self.assertIn("走查没过的原因：", dev_prompts[-1])
        self.assertIn("页面上没看到“确认没问题”", dev_prompts[-1])
        output_file = self.cdir / "走查截图" / "第1次" / "命令输出.txt"
        self.assertTrue(output_file.is_file())
        self.assertIn("Traceback", output_file.read_text(encoding="utf-8"))
        self.assertIn("命令输出.txt", dev_prompts[-1])
        # 会进归档的页面检查结果里不放原始输出
        log = (self.cdir / "页面检查结果.md").read_text(encoding="utf-8")
        self.assertNotIn("Traceback", log)
        self.assertIn("走查没过：卡在“右栏”这一步", log)

    def test_failed_walk_from_step_click_shows_step_name(self):
        # 走查脚本用 with w.step(...) 包住点击：点不到按钮抛单行 WalkError，右栏和退回原因都要写明卡在哪一步
        self.walk_results = [
            ("fail", "Traceback...\nwalk_tool.WalkError: “点开交接”这一步失败：Locator.click: Timeout 5000ms exceeded."),
            ("ok", [("右栏", "")]),
        ]
        card = self.run_walk()
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 1)
        first = self.walk_events()[0]
        self.assertEqual(first["summary"], "走查没过：卡在“点开交接”这一步")
        self.assertIn("点开交接", first.get("reason", ""))
        self.assertIn("Timeout 5000ms", first.get("reason", ""))
        dev_prompts = [p for a, p in self.prompts if a == "cursor"]
        self.assertIn("走查没过的原因：", dev_prompts[-1])
        self.assertIn("点开交接", dev_prompts[-1])

    def test_step_name_with_path_or_space_or_non_prefix_is_not_shown(self):
        # 含路径、含空格（如 python3 -m unittest）、纯英文词（如 pytest）、或不是行首 WalkError: 都不能当成步骤名露出来
        cases = (
            "walk_tool.WalkError: “/Users/某人/项目/右栏”这一步页面上没看到“确认”",
            "walk_tool.WalkError: “python3 -m unittest”这一步失败",
            "walk_tool.WalkError: “pytest”这一步失败",
            "AssertionError: 提到 walk_tool.WalkError: “不是报错行首”这一步",
        )
        for output in cases:
            with self.subTest(output=output):
                self.setUp()
                self.walk_results = [("fail", output), ("ok", [("右栏", "")])]
                self.run_walk()
                first = self.walk_events()[0]
                self.assertEqual(first["summary"], "走查没过：命令报错（详情在交接里）")

    def test_timeout_and_env_error_stop_like_page_check(self):
        for kind, result in (("timeout", TIMEOUT), ("env", ENV_ERROR)):
            with self.subTest(kind=kind):
                self.setUp()
                self.walk_results = [(kind, None)]
                card = self.run_walk()
                self.assertEqual(card.state, "需Claude介入")
                self.assertEqual(card.rework, 0, "超时、环境出错不算写代码的返工")
                first = self.walk_events()[0]
                self.assertEqual(first.get("result"), result)
                self.assertEqual(first["summary"], "走查没过：超时没跑完" if kind == "timeout"
                                 else "走查没跑起来：环境出错（不是代码的问题）")

    def test_no_shots_counts_as_failed(self):
        self.walk_results = [("nothing", None), ("ok", [("右栏", "")])]
        card = self.run_walk()
        self.assertEqual(card.rework, 1)
        first = self.walk_events()[0]
        self.assertEqual(first.get("result"), FAIL)
        self.assertEqual(first["summary"], "走查没过：没留下截图")

    def test_missing_shot_file_counts_as_failed(self):
        self.walk_results = [("missing_shot", [("右栏", "")]), ("ok", [("右栏", "")])]
        card = self.run_walk()
        self.assertEqual(card.rework, 1)
        first = self.walk_events()[0]
        self.assertEqual(first.get("result"), FAIL)
        self.assertEqual(first["summary"], "走查没过：第 1 步的截图不对")

    def test_design_outside_project_counts_as_failed_without_path(self):
        self.walk_results = [("ok", [("右栏", "../外面.png")]), ("ok", [("右栏", "")])]
        card = self.run_walk()
        self.assertEqual(card.rework, 1)
        first = self.walk_events()[0]
        self.assertEqual(first.get("result"), FAIL)
        self.assertEqual(first["summary"], "走查没过：第 1 步的设计图不在项目里")
        self.assertNotIn("/", first["summary"])

    def test_shot_link_pointing_outside_counts_as_failed(self):
        self.walk_results = [("symlink", [("右栏", "")]), ("ok", [("右栏", "")])]
        card = self.run_walk()
        self.assertEqual(card.rework, 1)
        first = self.walk_events()[0]
        self.assertEqual(first.get("result"), FAIL)
        self.assertEqual(first["summary"], "走查没过：第 1 步的截图不对")

    def test_broken_manifest_items_count_as_failed(self):
        for raw in ('["不是对象"]', '[{"截图": "01-右栏.png"}]', '[{"步骤": "右栏", "截图": "子目录/01-右栏.png"}]',
                    '[{"步骤": "右栏", "截图": 5}]', '{"步骤": "右栏"}', '不是 JSON'):
            with self.subTest(raw=raw):
                self.setUp()
                self.walk_results = [("raw", raw), ("ok", [("右栏", "")])]
                card = self.run_walk()
                self.assertEqual(card.rework, 1, raw)
                self.assertEqual(self.walk_events()[0].get("result"), FAIL, raw)


class ReviewerSeesShotsTest(WalkRunCase):
    def test_reviewer_prompt_lists_each_shot_and_design(self):
        self.walk_results = [("ok", [("右栏打开卡片", "docs/设计图.png"), ("点开交接", "")])]
        card = self.run_walk(reviewer="agy")
        self.assertEqual(card.state, "待验收")
        review = [p for a, p in self.prompts if "只读复查" in p][-1]
        self.assertIn("程序按施工单的“走查步骤”", review)
        shot1 = self.cdir / "走查截图" / "第1次" / "01-右栏打开卡片.png"
        design = self.worktree / "docs" / "设计图.png"
        self.assertTrue(str(shot1) in review or str(shot1.resolve()) in review, review)
        self.assertTrue(str(design) in review or str(design.resolve()) in review, review)
        self.assertIn("第 2 步 点开交接", review)
        self.assertIn("设计图 无", review)

    def test_after_review_return_only_new_shots_are_listed(self):
        self.reviews = ["结论：不通过\n1. 【中等】【这次改的】右栏少一行字。"]
        self.walk_results = [("ok", [("第一次的图", "")]), ("ok", [("第二次的图", "")])]
        card = self.run_walk(reviewer="agy")
        self.assertEqual(card.state, "待验收")
        reviews = [p for a, p in self.prompts if "只读复查" in p]
        self.assertGreaterEqual(len(reviews), 2)
        self.assertIn("第一次的图", reviews[0])
        self.assertIn("第二次的图", reviews[-1])
        self.assertNotIn("第一次的图", reviews[-1])
        self.assertTrue((self.cdir / "走查截图" / "第1次" / "01-第一次的图.png").is_file(), "上一次的截图不许被删")

    def test_reviewer_prompt_without_walk_has_no_walk_section(self):
        card = self.run_walk(walk=False, reviewer="agy")
        self.assertEqual(card.state, "待验收")
        review = [p for a, p in self.prompts if "只读复查" in p][-1]
        self.assertNotIn("走查步骤", review)


class PanelShowsWalkTest(WalkRunCase):
    def test_check_step_says_walk_result(self):
        self.walk_results = [("ok", [("右栏", ""), ("交接", "")])]
        self.run_walk()
        self.assertIn("走查过了，截了 2 张图", self.check_station().get("detail", ""))

    def test_full_tests_pass_shows_full_result_without_appending_walk(self):
        # 复查时全部测试跑完：本单不改右栏，右栏照现有逻辑显示全部测试通过，不在末尾拼接走查那句
        self.walk_results = [("ok", [("右栏", "")])]
        card = self.run_walk(reviewer="agy", full_runner=FakeFullRunner(PASS))
        self.assertEqual(card.state, "待验收")
        self.assertIn("全量", [e.get("kind") for e in read_flow(self.cdir)])
        detail = self.check_station().get("detail", "")
        self.assertIn("全部测试", detail)
        self.assertNotIn("走查过了", detail)

    def test_full_tests_break_old_keeps_fail_state_without_appending_walk(self):
        # 全部测试改坏老功能：右栏保持失败状态和老测试失败说明，不拼接走查那句
        self.walk_results = [("ok", [("右栏", "")])]
        self.run_walk(reviewer="agy", full_runner=FakeFullRunner(FAIL))
        st = self.check_station()
        detail = st.get("detail", "")
        self.assertIn("老测试被改坏", detail)
        self.assertEqual(st.get("state"), "fail")
        self.assertNotIn("走查过了", detail)

    def test_last_walk_of_delivery_wins(self):
        # 同一次交活里先过、后来又没过：以最后一次为准，不再说“走查过了”
        Card("CARD-001", state="开发中").save(self.cdir / "状态.json")
        record(self.cdir, "回复", src="开发员", dst="调度员", summary="汇报：改了")
        record(self.cdir, "判定", src="程序", result=PASS, round=1, summary="第 1 轮：测试全部通过")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
               shots=[{"步骤": "右栏", "截图": "走查截图/第1次/01-右栏.png", "设计图": ""}], summary="走查过了，截了 1 张图")
        record(self.cdir, "页面检查", src="程序", result=FAIL, walk=True, folder="走查截图/第2次", shots=[],
               reason="“右栏”这一步页面上没看到“确认”", summary="走查没过：卡在“右栏”这一步")
        detail = self.check_station().get("detail", "")
        self.assertIn("走查没过：卡在“右栏”这一步", detail)
        self.assertNotIn("走查过了", detail)

    def test_panel_without_walk_unchanged(self):
        self.run_walk(walk=False)
        detail = self.check_station().get("detail", "")
        self.assertNotIn("走查", detail)


ORDER = """# 施工单 改右栏：右栏加一行

> 依据 方案。

## 一、要做什么（一句话）

右栏加一行字。

## 二、允许改哪些文件

- `src/app.py`

**不许改**：上面没列的任何文件。

## 三、具体做法

### P1 加一行

照现有写法加。下面这行是举例，不在验收一节里，不算：
- 走查命令：`python3 tests/walk_不该被认.py`

## 六、不做什么

不改别的。

## 八、验收

- 验收命令：`true`
{walk}
"""


class PlannerWalkTest(ProjectCase):
    def plan_with(self, walk_line):
        from collab.planner import plan_card
        order = Path(self.project) / "docs" / "改右栏" / "施工单-改右栏.md"
        order.parent.mkdir(parents=True)
        order.write_text(ORDER.format(walk=walk_line), encoding="utf-8")
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text("# 按施工单做\n\n## 要做什么\n按施工单做：docs/改右栏/施工单-改右栏.md\n",
                                       encoding="utf-8")
        (Path(self.project) / "docs" / "项目说明.md").write_text("测试项目", encoding="utf-8")
        self.assertEqual(plan_card(self.project, "CARD-001"), 0)
        return json.loads((cdir / "开工.json").read_text(encoding="utf-8"))

    def test_walk_command_goes_into_start_info(self):
        start = self.plan_with("- 走查命令：`python3 tests/walk_改右栏.py`")
        self.assertEqual(start.get("test_command"), "true")
        self.assertEqual(start.get("walk_command"), "python3 tests/walk_改右栏.py")

    def test_walk_line_outside_acceptance_section_is_ignored(self):
        # 第三节里有一整行合法格式的“- 走查命令：`...`”，验收一节写“不涉及”：不算有走查
        start = self.plan_with("- 走查：不涉及")
        self.assertNotIn("walk_command", start)

    def test_walk_command_kept_when_plan_is_rewritten(self):
        # 批准前点过“继续改”（需求卡里有追加要求）：AI 重写方案，施工单的走查命令照样记进开工资料
        import os
        from acceptance_helpers import GOOD_PLAN
        from collab.planner import plan_card
        order = Path(self.project) / "docs" / "改右栏" / "施工单-改右栏.md"
        order.parent.mkdir(parents=True)
        order.write_text(ORDER.format(walk="- 走查命令：`python3 tests/walk_改右栏.py`"), encoding="utf-8")
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text("# 按施工单做\n\n## 要做什么\n按施工单做：docs/改右栏/施工单-改右栏.md\n\n"
                                       "## 追加要求\n右栏那行字改短一点\n", encoding="utf-8")
        (Path(self.project) / "docs" / "项目说明.md").write_text("测试项目", encoding="utf-8")
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        self.assertEqual(plan_card(self.project, "CARD-001"), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("walk_command"), "python3 tests/walk_改右栏.py")

    def test_no_walk_line_means_no_walk(self):
        start = self.plan_with("")
        self.assertEqual(start.get("test_command"), "true")
        self.assertNotIn("walk_command", start)


WALK_FAKE = """import json, os, pathlib
d = pathlib.Path(os.environ["WALK_SHOTS_DIR"])
d.mkdir(parents=True, exist_ok=True)
(d / "01-看卡.png").write_bytes(bytes.fromhex(%r))
(d / "走查.json").write_text(json.dumps([{"步骤": "看卡", "截图": "01-看卡.png", "设计图": ""}], ensure_ascii=False), encoding="utf-8")
""" % PNG.hex()


class CliWalkTest(ProjectCase):
    def test_run_reads_walk_command_from_start_info(self):
        tests = Path(self.project) / "tests"
        tests.mkdir()
        (tests / "walk_fake.py").write_text(WALK_FAKE, encoding="utf-8")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "走查脚本")
        cdir = self.make_card(state="待派发", plan=True)
        (cdir / "开工.json").write_text(json.dumps({"test_command": "true", "walk_command": "python3 tests/walk_fake.py"}),
                                       encoding="utf-8")
        code, out = self.cli("run", self.project, "CARD-001", "--test-command", "true",
                             "--test-tool", "codex", "--dev-tool", "cursor", "--review-tool", "none")
        self.assertEqual(self.state(), "待验收", out)
        events = [e for e in read_flow(cdir) if e.get("kind") == "页面检查" and e.get("walk")]
        self.assertEqual(len(events), 1, out)
        self.assertEqual(events[0]["result"], PASS)
        self.assertTrue((cdir / "走查截图" / "第1次" / "01-看卡.png").is_file())
        start = next(e for e in read_flow(cdir) if e.get("kind") == "开跑")
        self.assertIn("走查：python3 tests/walk_fake.py", full_text(cdir, start))

    def test_explicit_page_check_keeps_old_behavior(self):
        cdir = self.make_card(state="待派发", plan=True)
        (cdir / "开工.json").write_text(json.dumps({"test_command": "true", "walk_command": "false"}), encoding="utf-8")
        code, out = self.cli("run", self.project, "CARD-001", "--test-command", "true", "--page-check", "true",
                             "--test-tool", "codex", "--dev-tool", "cursor", "--review-tool", "none")
        self.assertEqual(self.state(), "待验收", out)
        flow = read_flow(cdir)
        self.assertEqual([e for e in flow if e.get("walk")], [])
        self.assertTrue([e for e in flow if e.get("kind") == "页面检查"], "老用法的页面检查要真的跑")
        start = next(e for e in flow if e.get("kind") == "开跑")
        self.assertIn("页面检查：true", full_text(cdir, start))


if __name__ == "__main__":
    unittest.main()
