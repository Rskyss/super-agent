"""C1.7 验收测试（Claude 写，施工方不许改）。依据 docs/跑卡流程调整/方案-C1.7.md。

1. 复查清单里的级别、来源、类型带不带【】都认；兜底读法同样认。
2. 写方案的交代先读规则，并交代不要让施工方跑全部测试。
3. 看不了后台进程的环境里，那项清理测试跳过并写明原因；正常环境照常检查。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _problem(level="中等", source="这次改的", kind="功能问题"):
    return {"级别": level, "来源": source, "类型": kind, "现象": "按钮点了没反应",
            "位置": "collab/dispatch.py:12",
            "证据": {"方式": "推演", "对照": "collab/dispatch.py:12 没有接上点击"}}


def _reply(problems, verdict="不通过"):
    body = json.dumps({"结论": verdict, "问题": problems}, ensure_ascii=False, indent=2)
    return f"复查完了。\n\n```json\n{body}\n```\n"


class BracketLevels(unittest.TestCase):
    """第 2 条：照交代原样写（带【】）的清单必须合格，并且认对级别、来源、类型。"""

    def test_bracketed_list_is_valid(self):
        from collab.dispatch import read_review_list
        data, errors = read_review_list(_reply([
            _problem("【严重】", "【这次改的】", "【功能问题】"),
            _problem("【轻微】", "【原来就有的】", "【测试写错】"),
        ]))
        self.assertEqual(errors, [])
        first, second = data["问题"]
        self.assertEqual((first["级别"], first["来源"], first["类型"]), ("严重", "这次改的", "功能问题"))
        self.assertEqual((second["级别"], second["来源"], second["类型"]), ("轻微", "原来就有的", "测试写错"))

    def test_bracket_with_spaces(self):
        from collab.dispatch import read_review_list
        data, errors = read_review_list(_reply([_problem(" 【中等】 ", "【 原来就有的 】")]))
        self.assertEqual(errors, [])
        self.assertEqual(data["问题"][0]["级别"], "中等")
        self.assertEqual(data["问题"][0]["来源"], "原来就有的")

    def test_plain_list_still_valid(self):
        from collab.dispatch import read_review_list
        data, errors = read_review_list(_reply([_problem("严重", "原来就有的")]))
        self.assertEqual(errors, [])
        self.assertEqual(data["问题"][0]["级别"], "严重")

    def test_wrong_value_still_rejected(self):
        from collab.dispatch import read_review_list
        _, errors = read_review_list(_reply([_problem("【很严重】")]))
        self.assertTrue(any("级别" in e for e in errors), errors)

    def test_fallback_reads_bracketed_severe(self):
        """格式不合格时的兜底读法：带括号的严重问题必须仍判成严重（不能被当成不严重放过去）。"""
        from collab.dispatch import _raw_list_problems
        broken = _problem("【严重】")
        del broken["位置"]   # 缺字段 → 正式读法不合格，走兜底
        problems, _ = _raw_list_problems(_reply([broken]))
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["级别"], "严重")


class PlannerReadsRules(unittest.TestCase):
    """第 3 条：写方案的交代先读规则，并交代不要让施工方跑全部测试。"""

    def test_prompt_reads_rules(self):
        from collab.dispatch import RULES
        from collab.planner import _build_prompt
        with tempfile.TemporaryDirectory() as d:
            prompt = _build_prompt(Path(d), "# 改按钮\n## 要做什么\n改颜色\n", False)
        self.assertIn(str(RULES), prompt)
        self.assertIn("先读规则", prompt)

    def test_prompt_forbids_full_suite(self):
        from collab.planner import _build_prompt
        with tempfile.TemporaryDirectory() as d:
            prompt = _build_prompt(Path(d), "# 改按钮\n## 要做什么\n改颜色\n", False)
        self.assertIn("不要让施工方跑全部测试", prompt)


class CleanupTestInLockedEnv(unittest.TestCase):
    """第 1 条：看不了后台进程（复查员的受限环境）时跳过并写明原因；正常环境照常跑、照常过。"""

    TARGET = "test_new_session_grandchild_cleaned"

    def _run(self, env):
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_flowfix4_round3.py",
               "-k", self.TARGET, "-v"]
        return subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)

    def test_skipped_with_reason_when_ps_forbidden(self):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = Path(d) / "bin"
            bin_dir.mkdir()
            os.symlink("/bin/sleep", bin_dir / "sleep")
            ps = bin_dir / "ps"
            ps.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ps.chmod(0o644)   # 没有执行权限：和受限环境一样，一启动就被拒
            env = dict(os.environ, PATH=str(bin_dir))
            out = self._run(env)
        text = out.stdout + out.stderr
        self.assertEqual(out.returncode, 0, text)
        self.assertIn(self.TARGET, text)
        self.assertIn("skipped", text)
        self.assertIn("后台进程", text)

    def test_runs_normally(self):
        try:   # 本身就跑在看不了后台进程的环境（复查员那边）时，这一条没法检验“正常环境照常跑”（C1.7 验收补修）
            subprocess.run(["ps", "-A", "-o", "pid="], capture_output=True, timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("此环境不许查看后台进程，正常环境那一半由程序自己跑测试时检验")
        out = self._run(dict(os.environ))
        text = out.stdout + out.stderr
        self.assertEqual(out.returncode, 0, text)
        self.assertIn(self.TARGET, text)
        self.assertNotIn("skipped", text)


if __name__ == "__main__":
    unittest.main()
