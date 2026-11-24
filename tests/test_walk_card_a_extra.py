"""P1 卡 A 补充自动化测试（测试员写，施工单 docs/走查截图/施工单-卡A.md）：

补齐验收测试未覆盖的关键路径与边界：
1. 中断后接着跑：从流转重新拼返工交代（past_fails）时带“走查没过的原因：”和命令输出完整路径；
   走查跑到一半停下后再接着跑用新文件夹；同一交活里多次走查以最后一条为准；
2. 走查清单、输出文件、页面检查结果日志和大白话摘要的边界条件；
3. 与本卡测试失败、老页面检查并存、共用返工上限/原地打转、继续改重开的配合；
4. 照施工单或 AI 写方案（含缺项目说明、无施工单、多对反引号）摘取走查命令；
5. 命令行开工在无走查与含管道走查命令（pipefail）下的行为。
"""
import json
import os
import unittest
from pathlib import Path

from acceptance_helpers import GOOD_PLAN, ProjectCase
from collab.dispatch import run_card
from collab.flow import read_flow, record
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask
from test_walk_card_a_acceptance import (
    ORDER,
    PNG,
    WALK_CMD,
    WalkRunCase,
    full_text,
    shots_dir_of,
    write_walk,
)


def _path_mentioned(text: str, path: Path) -> bool:
    """兼容 macOS 上 /var 与 /private/var 软链接路径。"""
    return str(path) in text or str(path.resolve()) in text


class _SimulatedInterrupt(RuntimeError):
    """测试里用来模拟进程在指定时机被中断。"""


class WalkResumeExtraTest(WalkRunCase):
    """中断后接着跑：重拼返工交代、半截走查续跑、同一次交活以最后一条为准。"""

    def test_resume_after_walk_fail_includes_reason_and_output_path(self):
        # 施工单第八节必测：第 1 轮走查没过后中断，接着跑从流转 past_fails 重拼交代时，
        # 交给写代码的话里必须带有“走查没过的原因：”和命令输出.txt 的完整路径。
        self.walk_results = [
            ("fail", "Traceback...\nwalk_tool.WalkError: “右栏”这一步页面上没看到“确认没问题”"),
            ("ok", [("右栏", "")]),
        ]
        dev_calls = {"n": 0}
        orig_ask = self.ask

        def interrupt_on_second_dev(adapter, prompt, cwd, **kwargs):
            if adapter == "cursor":
                dev_calls["n"] += 1
                if dev_calls["n"] == 2:
                    raise _SimulatedInterrupt("模拟第 2 轮开发前中断")
            return orig_ask(adapter, prompt, cwd, **kwargs)

        with self.assertRaises(_SimulatedInterrupt):
            run_card(
                Card("CARD-001", state="待派发"),
                collab_dir=self.collab,
                worktree=str(self.worktree),
                test_command=["fake-test"],
                dev_adapter="cursor",
                test_adapter="codex",
                ask_fn=listed_ask(interrupt_on_second_dev),
                judge_fn=self.judge,
                walk_command=list(WALK_CMD),
            )

        # 中断后卡片状态已落盘：rework=1、开发中，第 1 次走查记录和命令输出文件都已留下
        saved = Card.load(self.cdir / "状态.json")
        self.assertEqual(saved.state, "开发中")
        self.assertEqual(saved.rework, 1)
        out_file = self.cdir / "走查截图" / "第1次" / "命令输出.txt"
        self.assertTrue(out_file.is_file())

        # 清空已记录的派活提示词，模拟新进程从磁盘状态接着跑
        self.prompts.clear()
        card, _ = run_card(
            saved,
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=self.judge,
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(card.state, "待验收")
        dev_prompts = [p for a, p in self.prompts if a == "cursor"]
        self.assertEqual(len(dev_prompts), 1, "接着跑必须带着上次走查失败原因重派开发员")
        retry_prompt = dev_prompts[0]
        self.assertIn("走查没过的原因：", retry_prompt)
        self.assertIn("页面上没看到“确认没问题”", retry_prompt)
        self.assertIn("命令输出在", retry_prompt)
        self.assertTrue(_path_mentioned(retry_prompt, out_file), retry_prompt)
        # 第二轮走查必须使用新的第 2 次文件夹
        self.assertEqual(
            shots_dir_of(self.walk_commands[-1]).resolve(),
            (self.cdir / "走查截图" / "第2次").resolve(),
        )

    def test_resume_after_manifest_fail_includes_reason_and_output_path(self):
        # 清单校验失败（如设计图越界）后中断，接着跑重拼的返工交代也必须带“走查没过的原因：”和命令输出路径
        self.walk_results = [
            ("ok", [("右栏", "../外面.png")]),
            ("ok", [("右栏", "")]),
        ]
        dev_calls = {"n": 0}
        orig_ask = self.ask

        def interrupt_on_second_dev(adapter, prompt, cwd, **kwargs):
            if adapter == "cursor":
                dev_calls["n"] += 1
                if dev_calls["n"] == 2:
                    raise _SimulatedInterrupt("模拟清单失败后中断")
            return orig_ask(adapter, prompt, cwd, **kwargs)

        with self.assertRaises(_SimulatedInterrupt):
            run_card(
                Card("CARD-001", state="待派发"),
                collab_dir=self.collab,
                worktree=str(self.worktree),
                test_command=["fake-test"],
                dev_adapter="cursor",
                test_adapter="codex",
                ask_fn=listed_ask(interrupt_on_second_dev),
                judge_fn=self.judge,
                walk_command=list(WALK_CMD),
            )

        saved = Card.load(self.cdir / "状态.json")
        out_file = self.cdir / "走查截图" / "第1次" / "命令输出.txt"
        self.assertTrue(out_file.is_file())
        first_reason = self.walk_events()[0].get("reason", "")
        self.assertTrue(first_reason)

        self.prompts.clear()
        card, _ = run_card(
            saved,
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=self.judge,
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(card.state, "待验收")
        dev_prompts = [p for a, p in self.prompts if a == "cursor"]
        self.assertEqual(len(dev_prompts), 1)
        self.assertIn("走查没过的原因：", dev_prompts[0])
        self.assertIn(first_reason, dev_prompts[0])
        self.assertTrue(_path_mentioned(dev_prompts[0], out_file), dev_prompts[0])

    def test_resume_after_plain_page_check_fail_has_no_walk_reason(self):
        # 老用法的页面检查失败后中断续跑：不能误拼“走查没过的原因：”
        page_results = [Verdict(FAIL, 1, "页面检查出错了"), Verdict(PASS, 0, "页面检查过")]
        dev_calls = {"n": 0}
        orig_ask = self.ask

        def judge_page(command, cwd, timeout):
            if command == ["fake-page"]:
                return page_results.pop(0)
            return Verdict(PASS, 0, "测试全过")

        def interrupt_on_second_dev(adapter, prompt, cwd, **kwargs):
            if adapter == "cursor":
                dev_calls["n"] += 1
                if dev_calls["n"] == 2:
                    raise _SimulatedInterrupt("模拟老页面检查失败后中断")
            return orig_ask(adapter, prompt, cwd, **kwargs)

        with self.assertRaises(_SimulatedInterrupt):
            run_card(
                Card("CARD-001", state="待派发"),
                collab_dir=self.collab,
                worktree=str(self.worktree),
                test_command=["fake-test"],
                dev_adapter="cursor",
                test_adapter="codex",
                ask_fn=listed_ask(interrupt_on_second_dev),
                judge_fn=judge_page,
                page_command=["fake-page"],
            )

        saved = Card.load(self.cdir / "状态.json")
        self.prompts.clear()
        card, _ = run_card(
            saved,
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=judge_page,
            page_command=["fake-page"],
        )
        self.assertEqual(card.state, "待验收")
        dev_prompts = [p for a, p in self.prompts if a == "cursor"]
        self.assertEqual(len(dev_prompts), 1)
        self.assertNotIn("走查没过的原因：", dev_prompts[0])

    def test_resume_after_interrupted_halfway_walk_uses_next_folder(self):
        # 状态表：主动停下时走查跑到一半（第 1 次文件夹有半截截图、无走查.json、无走查记录）
        # 接着跑时用新的第 2 次文件夹重跑，不删旧文件夹，复查只看到第 2 次的截图
        half = self.cdir / "走查截图" / "第1次"
        half.mkdir(parents=True)
        (half / "01-半截.png").write_bytes(PNG)
        card = Card("CARD-001", state="开发中")
        card.save(self.cdir / "状态.json")
        record(self.cdir, "回复", src="测试员", dst="调度员", step="写测试", round=1, ok=True, summary="写好了")
        record(self.cdir, "回复", src="开发员", dst="调度员", step="开发", round=1, ok=True, summary="汇报：改了")
        record(self.cdir, "判定", src="程序", result=PASS, round=1, summary="第 1 轮：测试全部通过")

        self.walk_results = [("ok", [("重跑的图", "docs/设计图.png")])]
        done, _ = run_card(
            card,
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=self.judge,
            reviewer_adapter="agy",
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(done.state, "待验收")
        # 开发员本次交活已完成，不需要重派写代码
        self.assertEqual([a for a, _ in self.prompts if a == "cursor"], [])
        self.assertEqual(
            shots_dir_of(self.walk_commands[0]).resolve(),
            (self.cdir / "走查截图" / "第2次").resolve(),
        )
        self.assertTrue((half / "01-半截.png").is_file(), "半截旧文件夹里的图不许被删")
        review = [p for _, p in self.prompts if "只读复查" in p][-1]
        self.assertIn("重跑的图", review)
        self.assertNotIn("半截", review)

    def test_resume_same_delivery_second_walk_replaces_first_in_review(self):
        # 同一次交活里，走查过了以后复查前中断，接着跑又走查了一次：复查员只拿最后一条走查记录的截图
        first_dir = self.cdir / "走查截图" / "第1次"
        write_walk(first_dir, [("第一次的图", "")])
        card = Card("CARD-001", state="开发中")
        card.save(self.cdir / "状态.json")
        record(self.cdir, "回复", src="测试员", dst="调度员", step="写测试", round=1, ok=True, summary="写好了")
        record(self.cdir, "回复", src="开发员", dst="调度员", step="开发", round=1, ok=True, summary="汇报：改了")
        record(self.cdir, "判定", src="程序", result=PASS, round=1, summary="第 1 轮：测试全部通过")
        record(
            self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
            shots=[{"步骤": "第一次的图", "截图": "走查截图/第1次/01-第一次的图.png", "设计图": ""}],
            summary="走查过了，截了 1 张图",
        )

        self.walk_results = [("ok", [("第二次的图", "")])]
        done, _ = run_card(
            card,
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=self.judge,
            reviewer_adapter="agy",
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(done.state, "待验收")
        review = [p for _, p in self.prompts if "只读复查" in p][-1]
        self.assertIn("第二次的图", review)
        self.assertNotIn("第一次的图", review)

    def test_resume_same_delivery_first_pass_then_fail_reworks_before_review(self):
        # 同一次交活里先过、接着跑再走查却没过：以最后一条为准，退回写代码的，不进复查；改完再过才进复查
        first_dir = self.cdir / "走查截图" / "第1次"
        write_walk(first_dir, [("第一次的图", "")])
        card = Card("CARD-001", state="开发中")
        card.save(self.cdir / "状态.json")
        record(self.cdir, "回复", src="测试员", dst="调度员", step="写测试", round=1, ok=True, summary="写好了")
        record(self.cdir, "回复", src="开发员", dst="调度员", step="开发", round=1, ok=True, summary="汇报：改了")
        record(self.cdir, "判定", src="程序", result=PASS, round=1, summary="第 1 轮：测试全部通过")
        record(
            self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
            shots=[{"步骤": "第一次的图", "截图": "走查截图/第1次/01-第一次的图.png", "设计图": ""}],
            summary="走查过了，截了 1 张图",
        )

        self.walk_results = [
            ("fail", "walk_tool.WalkError: “右栏”这一步页面上没看到“标题”"),
            ("ok", [("第三次的图", "")]),
        ]
        done, _ = run_card(
            card,
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=self.judge,
            reviewer_adapter="agy",
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(done.state, "待验收")
        self.assertEqual(done.rework, 1)
        reviews = [p for _, p in self.prompts if "只读复查" in p]
        self.assertEqual(len(reviews), 1)
        self.assertIn("第三次的图", reviews[0])
        self.assertNotIn("第一次的图", reviews[0])


class WalkManifestAndSummaryExtraTest(WalkRunCase):
    """文件夹编号、命令输出与归档日志隔离、清单边界校验与摘要提取。"""

    def test_folder_number_picks_max_plus_one_ignoring_noise(self):
        # 已有 第1次、第5次 以及杂文件/杂目录时，下一次必须是 第6次，不清空旧目录
        base = self.cdir / "走查截图"
        (base / "第1次").mkdir(parents=True)
        (base / "第5次").mkdir(parents=True)
        (base / "临时说明").mkdir(parents=True)
        (base / "说明.txt").write_text("不要碰我", encoding="utf-8")
        (base / "第5次" / "old.png").write_bytes(PNG)

        self.walk_results = [("ok", [("右栏", "")])]
        card = self.run_walk()
        self.assertEqual(card.state, "待验收")
        self.assertEqual(shots_dir_of(self.walk_commands[0]).resolve(), (base / "第6次").resolve())
        self.assertEqual(self.walk_events()[0].get("folder"), "走查截图/第6次")
        self.assertTrue((base / "第5次" / "old.png").is_file())
        self.assertTrue((base / "说明.txt").is_file())

    def test_output_file_and_clean_page_check_log(self):
        # 走查通过时也写 命令输出.txt（开头写“只留了末尾部分”），而会进归档的 页面检查结果.md 不放原始输出
        def judge_with_secret_path(command, cwd, timeout):
            folder = shots_dir_of(command)
            if folder is None:
                return Verdict(PASS, 0, "测试全过")
            write_walk(folder, [("右栏打开卡片", "docs/设计图.png")])
            return Verdict(PASS, 0, "调试日志：/Users/secret_user/本地路径/不应该进归档")

        card, _ = run_card(
            Card("CARD-001", state="待派发"),
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=judge_with_secret_path,
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(card.state, "待验收")
        out_file = self.cdir / "走查截图" / "第1次" / "命令输出.txt"
        self.assertTrue(out_file.is_file())
        out_text = out_file.read_text(encoding="utf-8")
        self.assertIn("只留了末尾部分", out_text.splitlines()[0])
        self.assertIn("/Users/secret_user/本地路径/不应该进归档", out_text)

        log = (self.cdir / "页面检查结果.md").read_text(encoding="utf-8")
        self.assertIn("## 第 1 次走查", log)
        self.assertIn("走查过了，截了 1 张图", log)
        self.assertIn("右栏打开卡片", log)
        self.assertIn("01-右栏打开卡片.png", log)
        self.assertIn("docs/设计图.png", log)
        self.assertNotIn("/Users/secret_user", log)

    def test_missing_design_key_is_valid_and_review_says_none(self):
        # 清单里完全省略“设计图”键是合法的，复查交代里写“设计图 无”
        raw = json.dumps([{"步骤": "右栏", "截图": "01-右栏.png"}], ensure_ascii=False)
        self.walk_results = [("raw", raw)]
        card = self.run_walk(reviewer="agy")
        self.assertEqual(card.state, "待验收")
        ev = self.walk_events()[0]
        self.assertEqual(ev["result"], PASS)
        self.assertEqual(ev["shots"][0]["设计图"], "")
        review = [p for _, p in self.prompts if "只读复查" in p][-1]
        shot = self.cdir / "走查截图" / "第1次" / "01-右栏.png"
        self.assertTrue(
            f"第 1 步 右栏：截图 {shot}；设计图 无" in review
            or f"第 1 步 右栏：截图 {shot.resolve()}；设计图 无" in review,
            review,
        )

    def test_manifest_edge_cases_fail_with_plain_summary(self):
        # 覆盖空列表、空白步骤名、非 png 截图、反斜杠截图、第 2 步缺图、绝对路径/不存在/非 png/软链接越界/非字符串设计图
        def make_case_judge(case_name):
            called = {"n": 0}

            def _judge(command, cwd, timeout):
                folder = shots_dir_of(command)
                if folder is None:
                    return Verdict(PASS, 0, "测试全过")
                called["n"] += 1
                if called["n"] > 1:
                    write_walk(folder, [("右栏", "")])
                    return Verdict(PASS, 0, "走查完了")
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "01-右栏.png").write_bytes(PNG)
                abs_inside = str((Path(cwd) / "docs" / "设计图.png").resolve())
                if case_name == "empty_list":
                    items = []
                elif case_name == "blank_step":
                    items = [{"步骤": "   ", "截图": "01-右栏.png", "设计图": ""}]
                elif case_name == "non_png_shot":
                    (folder / "01-右栏.jpg").write_bytes(PNG)
                    items = [{"步骤": "右栏", "截图": "01-右栏.jpg", "设计图": ""}]
                elif case_name == "backslash_shot":
                    items = [{"步骤": "右栏", "截图": "sub\\01-右栏.png", "设计图": ""}]
                elif case_name == "second_shot_missing":
                    items = [
                        {"步骤": "第一步", "截图": "01-右栏.png", "设计图": ""},
                        {"步骤": "第二步", "截图": "02-缺少.png", "设计图": ""},
                    ]
                elif case_name == "abs_design":
                    items = [{"步骤": "右栏", "截图": "01-右栏.png", "设计图": abs_inside}]
                elif case_name == "missing_design_file":
                    items = [{"步骤": "右栏", "截图": "01-右栏.png", "设计图": "docs/不存在.png"}]
                elif case_name == "non_png_design":
                    items = [{"步骤": "右栏", "截图": "01-右栏.png", "设计图": "docs/说明.txt"}]
                elif case_name == "symlink_design_outside":
                    items = [{"步骤": "右栏", "截图": "01-右栏.png", "设计图": "docs/越界链接.png"}]
                elif case_name == "non_str_design":
                    items = [{"步骤": "右栏", "截图": "01-右栏.png", "设计图": 123}]
                else:
                    raise AssertionError(case_name)
                (folder / "走查.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
                return Verdict(PASS, 0, "走查完了")

            return _judge

        expected = {
            "empty_list": {"走查没过：没留下截图", "走查没过：步骤清单写得不对"},
            "blank_step": {"走查没过：步骤清单写得不对"},
            "non_png_shot": {"走查没过：第 1 步的截图不对"},
            "backslash_shot": {"走查没过：第 1 步的截图不对"},
            "second_shot_missing": {"走查没过：第 2 步的截图不对"},
            "abs_design": {"走查没过：第 1 步的设计图不在项目里"},
            "missing_design_file": {"走查没过：第 1 步的设计图不在项目里"},
            "non_png_design": {"走查没过：第 1 步的设计图不在项目里", "走查没过：步骤清单写得不对"},
            "symlink_design_outside": {"走查没过：第 1 步的设计图不在项目里"},
            "non_str_design": {"走查没过：第 1 步的设计图不在项目里", "走查没过：步骤清单写得不对"},
        }
        for name, allowed_summaries in expected.items():
            with self.subTest(case=name):
                self.setUp()
                (self.worktree / "docs" / "说明.txt").write_text("不是图片", encoding="utf-8")
                (self.worktree / "docs" / "越界链接.png").symlink_to(Path(self.tmp) / "外面.png")
                card, _ = run_card(
                    Card("CARD-001", state="待派发"),
                    collab_dir=self.collab,
                    worktree=str(self.worktree),
                    test_command=["fake-test"],
                    dev_adapter="cursor",
                    test_adapter="codex",
                    ask_fn=listed_ask(self.ask),
                    judge_fn=make_case_judge(name),
                    walk_command=list(WALK_CMD),
                )
                self.assertEqual(card.rework, 1, name)
                first = self.walk_events()[0]
                self.assertEqual(first["result"], FAIL, name)
                self.assertEqual(first["shots"], [], name)
                self.assertIn(first["summary"], allowed_summaries, f"{name}: {first['summary']}")
                self.assertNotIn("/", first["summary"], name)
                self.assertNotIn("\\", first["summary"], name)

    def test_walk_error_prefix_whitespace_and_length_boundary(self):
        exact_20 = "一二三四五六七八九十一二三四五六七八九十"
        too_long_21 = exact_20 + "一"
        cases = (
            (f"日志\n   WalkError: “{exact_20}”这一步失败\n\n", f"走查没过：卡在“{exact_20}”这一步"),
            (f"walk_tool.WalkError: “{too_long_21}”这一步失败", "走查没过：命令报错（详情在交接里）"),
            ("WalkError: “右栏\\子项”这一步失败", "走查没过：命令报错（详情在交接里）"),
            ("WalkError: “点“交接””这一步失败", "走查没过：命令报错（详情在交接里）"),
        )
        for output, want in cases:
            with self.subTest(output=output):
                self.setUp()
                self.walk_results = [("fail", output), ("ok", [("右栏", "")])]
                self.run_walk()
                self.assertEqual(self.walk_events()[0]["summary"], want)

    def test_generic_command_error_caps_reason_at_200_chars(self):
        long_line = "RuntimeError: " + ("页面渲染异常" * 50)
        self.walk_results = [("fail", f"前一行\n{long_line}\n\n"), ("ok", [("右栏", "")])]
        card = self.run_walk()
        self.assertEqual(card.rework, 1)
        first = self.walk_events()[0]
        self.assertEqual(first["summary"], "走查没过：命令报错（详情在交接里）")
        reason = first.get("reason", "")
        self.assertTrue(reason)
        self.assertLessEqual(len(reason), 200)
        self.assertIn("RuntimeError:", reason)
        dev_prompts = [p for a, p in self.prompts if a == "cursor"]
        self.assertIn(f"走查没过的原因：{reason}", dev_prompts[-1])


class WalkFlowInteractionExtraTest(WalkRunCase):
    """走查与本卡测试、老页面检查、返工上限、继续改的配合。"""

    def test_walk_does_not_run_when_card_tests_fail(self):
        test_results = [Verdict(FAIL, 1, "本卡测试第 1 轮失败"), Verdict(PASS, 0, "本卡测试过")]
        self.walk_results = [("ok", [("右栏", "")])]
        orig_judge = self.judge

        def judge_test_fail_first(command, cwd, timeout):
            if shots_dir_of(command) is None:
                return test_results.pop(0)
            return orig_judge(command, cwd, timeout)

        card, _ = run_card(
            Card("CARD-001", state="待派发"),
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=judge_test_fail_first,
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 1)
        self.assertEqual(len(self.walk_commands), 1, "本卡测试没过那轮不能跑走查")
        self.assertEqual(shots_dir_of(self.walk_commands[0]).resolve(), (self.cdir / "走查截图" / "第1次").resolve())

    def test_page_command_runs_before_walk_and_blocks_walk_on_fail(self):
        order = []
        page_results = [Verdict(FAIL, 1, "老页面检查不过"), Verdict(PASS, 0, "老页面检查过")]
        self.walk_results = [("ok", [("右栏", "")])]
        orig_judge = self.judge

        def judge_both(command, cwd, timeout):
            if command == ["fake-page"]:
                order.append("page")
                return page_results.pop(0)
            if shots_dir_of(command) is not None:
                order.append("walk")
                return orig_judge(command, cwd, timeout)
            order.append("test")
            return Verdict(PASS, 0, "测试全过")

        card, _ = run_card(
            Card("CARD-001", state="待派发"),
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=judge_both,
            page_command=["fake-page"],
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(card.state, "待验收")
        self.assertEqual(card.rework, 1)
        self.assertEqual(order, ["test", "page", "test", "page", "walk"])

    def test_walk_failures_share_rework_limit_and_detect_stuck(self):
        # 1) 本卡测试失败 1 次 + 走查不同报错再失败 3 次（共用 3 次返工上限，第 4 次仍不过转需Claude介入）
        test_results = [
            Verdict(FAIL, 1, "测试失败一次"),
            Verdict(PASS, 0, "过"),
            Verdict(PASS, 0, "过"),
            Verdict(PASS, 0, "过"),
        ]
        self.walk_results = [
            ("fail", "walk_tool.WalkError: “第一步”这一步失败"),
            ("fail", "walk_tool.WalkError: “第二步”这一步失败"),
            ("fail", "walk_tool.WalkError: “第三步”这一步失败"),
        ]
        orig_judge = self.judge

        def judge_shared_limit(command, cwd, timeout):
            if shots_dir_of(command) is None:
                return test_results.pop(0) if test_results else Verdict(PASS, 0, "过")
            return orig_judge(command, cwd, timeout)

        card, _ = run_card(
            Card("CARD-001", state="待派发"),
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=judge_shared_limit,
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(card.rework, 3)

        # 2) 走查同样报错连续出现两次：判定原地打转直接转需Claude介入
        self.setUp()
        same_err = "walk_tool.WalkError: “右栏”这一步页面上没看到“确认”"
        self.walk_results = [("fail", same_err), ("fail", same_err)]
        stuck = self.run_walk()
        self.assertEqual(stuck.state, "需Claude介入")
        self.assertEqual(stuck.rework, 1)

    def test_revise_clears_old_shots_from_next_review(self):
        # 先正常做完一轮（留下第 1 次走查截图）
        self.walk_results = [("ok", [("旧一轮的图", "docs/设计图.png")])]
        first = self.run_walk(reviewer="agy")
        self.assertEqual(first.state, "待验收")

        # 模拟用户点“继续改”：追加要求，卡片回到开发中且 rework 清零，再跑一轮带走查
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="把按钮改大", text="把按钮改大")
        self.prompts.clear()
        self.walk_results = [("ok", [("新一轮的图", "")])]
        second, _ = run_card(
            Card("CARD-001", state="开发中", rework=0),
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=self.judge,
            reviewer_adapter="agy",
            walk_command=list(WALK_CMD),
        )
        self.assertEqual(second.state, "待验收")
        review = [p for _, p in self.prompts if "只读复查" in p][-1]
        self.assertIn("新一轮的图", review)
        self.assertNotIn("旧一轮的图", review)
        self.assertTrue((self.cdir / "走查截图" / "第1次" / "01-旧一轮的图.png").is_file(), "旧截图不许被删")

        # 再模拟一次继续改但不再跑走查（walk=False）：复查交代不能沿用上一次交活的旧截图
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="改文案", text="改文案")
        self.prompts.clear()
        third, _ = run_card(
            Card("CARD-001", state="开发中", rework=0),
            collab_dir=self.collab,
            worktree=str(self.worktree),
            test_command=["fake-test"],
            dev_adapter="cursor",
            test_adapter="codex",
            ask_fn=listed_ask(self.ask),
            judge_fn=self.judge,
            reviewer_adapter="agy",
        )
        self.assertEqual(third.state, "待验收")
        review_no_walk = [p for _, p in self.prompts if "只读复查" in p][-1]
        self.assertNotIn("走查步骤", review_no_walk)
        self.assertNotIn("新一轮的图", review_no_walk)


class PlannerWalkExtraTest(ProjectCase):
    """方案摘取走查命令的额外路径：缺项目说明、无施工单、多对反引号、非“八、验收”标题。"""

    def test_walk_command_kept_when_profile_needed(self):
        # 还没有 docs/项目说明.md（need_profile=True）走 AI 写方案路径，但引用了带走查的施工单
        from collab.planner import plan_card

        order = Path(self.project) / "docs" / "改右栏" / "施工单-改右栏.md"
        order.parent.mkdir(parents=True)
        order.write_text(ORDER.format(walk="- 走查命令：`python3 tests/walk_改右栏.py`"), encoding="utf-8")
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text(
            "# 按施工单做\n\n## 要做什么\n按施工单做：docs/改右栏/施工单-改右栏.md\n",
            encoding="utf-8",
        )
        plan_with_profile = dict(GOOD_PLAN, profile="这是一个测试项目说明，没有待填。")
        os.environ["FAKE_PLAN"] = json.dumps(plan_with_profile, ensure_ascii=False)
        self.assertEqual(plan_card(self.project, "CARD-001"), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("walk_command"), "python3 tests/walk_改右栏.py")

    def test_no_order_card_has_no_walk_command(self):
        from collab.planner import plan_card

        cdir = self.make_card()
        (Path(self.project) / "docs").mkdir(exist_ok=True)
        (Path(self.project) / "docs" / "项目说明.md").write_text("测试项目", encoding="utf-8")
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        self.assertEqual(plan_card(self.project, "CARD-001"), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertNotIn("walk_command", start)

    def test_rewritten_plan_ignores_walk_outside_acceptance_section(self):
        from collab.planner import plan_card

        order = Path(self.project) / "docs" / "改右栏" / "施工单-改右栏.md"
        order.parent.mkdir(parents=True)
        order.write_text(ORDER.format(walk="- 走查：不涉及"), encoding="utf-8")
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text(
            "# 按施工单做\n\n## 要做什么\n按施工单做：docs/改右栏/施工单-改右栏.md\n\n## 追加要求\n改短一点\n",
            encoding="utf-8",
        )
        (Path(self.project) / "docs" / "项目说明.md").write_text("测试项目", encoding="utf-8")
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        self.assertEqual(plan_card(self.project, "CARD-001"), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertNotIn("walk_command", start)

    def test_takes_first_backtick_pair_and_only_bullet_line_in_acceptance_section(self):
        from collab.planner import plan_card

        custom_order = """# 施工单 改右栏

## 一、要做什么（一句话）

右栏加一行字。

## 二、允许改哪些文件

- `src/app.py`

## 四、验收标准

- 验收命令：`true`
- 说明：正文里提到 - 走查命令：`python3 tests/walk_不是这一行.py` 不算
- 走查命令：`python3 tests/walk_第一对.py`（截图放在 `走查截图/` 下）
"""
        order = Path(self.project) / "docs" / "改右栏" / "施工单-改右栏.md"
        order.parent.mkdir(parents=True)
        order.write_text(custom_order, encoding="utf-8")
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text(
            "# 按施工单做\n\n## 要做什么\n按施工单做：docs/改右栏/施工单-改右栏.md\n",
            encoding="utf-8",
        )
        (Path(self.project) / "docs" / "项目说明.md").write_text("测试项目", encoding="utf-8")
        self.assertEqual(plan_card(self.project, "CARD-001"), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("walk_command"), "python3 tests/walk_第一对.py")


class CliWalkExtraTest(ProjectCase):
    """命令行开工读走查命令的边界：无走查保持老日志；走查命令走 bash -o pipefail。"""

    def test_run_without_walk_or_page_check_keeps_old_start_log(self):
        for start_data in ({"test_command": "true"}, {"test_command": "true", "walk_command": "   "}):
            with self.subTest(start_data=start_data):
                self.setUp()
                cdir = self.make_card(state="待派发", plan=True)
                (cdir / "开工.json").write_text(json.dumps(start_data, ensure_ascii=False), encoding="utf-8")
                code, out = self.cli(
                    "run", self.project, "CARD-001", "--test-command", "true",
                    "--test-tool", "codex", "--dev-tool", "cursor", "--review-tool", "none",
                )
                self.assertEqual(code, 0, out)
                self.assertEqual(self.state(), "待验收", out)
                flow = read_flow(cdir)
                self.assertEqual([e for e in flow if e.get("walk")], [])
                start = next(e for e in flow if e.get("kind") == "开跑")
                self.assertIn("页面检查：无", full_text(cdir, start))

    def test_run_walk_command_uses_bash_pipefail(self):
        # 管道前半段先写出合法截图和步骤清单再报错退出，后半段 cat 返回 0：
        # 只有通过 /bin/bash -o pipefail -c 跑，才会因前半段报错而判成没过；
        # 若漏掉 pipefail，后半段返回 0 且截图清单齐全，会直接通过。
        tests = Path(self.project) / "tests"
        tests.mkdir(exist_ok=True)
        walk_script = (
            "import json, os, pathlib, sys\n"
            'd = pathlib.Path(os.environ["WALK_SHOTS_DIR"])\n'
            "d.mkdir(parents=True, exist_ok=True)\n"
            f'(d / "01-看卡.png").write_bytes(bytes.fromhex({PNG.hex()!r}))\n'
            '(d / "走查.json").write_text(\n'
            '    json.dumps([{"步骤": "看卡", "截图": "01-看卡.png", "设计图": ""}], ensure_ascii=False),\n'
            '    encoding="utf-8",\n'
            ")\n"
            'print("管道前半段出错", flush=True)\n'
            "sys.exit(1)\n"
        )
        (tests / "walk_pipefail.py").write_text(walk_script, encoding="utf-8")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "管道走查脚本")
        cdir = self.make_card(state="待派发", plan=True)
        walk_cmd = "python3 tests/walk_pipefail.py | cat"
        (cdir / "开工.json").write_text(
            json.dumps({"test_command": "true", "walk_command": walk_cmd}, ensure_ascii=False),
            encoding="utf-8",
        )
        code, out = self.cli(
            "run", self.project, "CARD-001", "--test-command", "true",
            "--test-tool", "codex", "--dev-tool", "cursor", "--review-tool", "none",
        )
        self.assertNotEqual(code, 0, f"pipefail 生效时不应通过：{out}")
        self.assertEqual(self.state(), "需Claude介入", out)
        events = [e for e in read_flow(cdir) if e.get("kind") == "页面检查" and e.get("walk")]
        self.assertGreaterEqual(len(events), 1, out)
        self.assertEqual(events[0]["result"], FAIL)
        self.assertTrue((cdir / "走查截图" / "第1次" / "01-看卡.png").is_file())
        self.assertTrue((cdir / "走查截图" / "第1次" / "走查.json").is_file())
        self.assertEqual(events[0].get("reason"), "管道前半段出错")
        self.assertEqual(events[0].get("summary"), "走查没过：命令报错（详情在交接里）")


if __name__ == "__main__":
    unittest.main()
