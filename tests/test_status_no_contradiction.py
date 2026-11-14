"""右栏不许自相矛盾（试跑记录 70，11-14 卡 71）：卡已经过了开发（待验收、已合并、需 Claude 介入、已放弃），
最后一次派活没交活（被停下、Claude 接手）时，那次派活当作废——不再转圈计时、顶部不再写“正在返工”，
各步照最后一次做完的结果打勾。开发中正常派活的仍然转圈。"""
import json
import tempfile
import unittest
from pathlib import Path

from desktop.view import card_view


def make_card(state, events):
    root = Path(tempfile.mkdtemp()) / "proj"
    cdir = root / ".collab" / "cards" / "CARD-001"
    cdir.mkdir(parents=True)
    (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": state, "rework": 1}), encoding="utf-8")
    (cdir / "流转.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return root


OPEN_DEV = {"seq": 1, "time": "2026-11-14T05:00:00", "kind": "派活", "src": "调度员", "dst": "开发员",
            "summary": "测试没过，带着测试输出退回修改", "tool": "agy", "step": "返工修改", "round": 2}
STOPPED = {"seq": 2, "time": "2026-11-14T05:01:00", "kind": "停下", "src": "你", "dst": "程序", "summary": "施工已停下"}
EARLIER_ROUND = [
    {"seq": 1, "time": "2026-11-14T04:50:00", "kind": "派活", "src": "调度员", "dst": "开发员",
     "summary": "按测试写代码", "tool": "agy", "step": "开发", "round": 1},
    {"seq": 2, "time": "2026-11-14T04:55:00", "kind": "回复", "src": "开发员", "dst": "调度员",
     "summary": "做完了", "ok": True, "seconds": 300},
    {"seq": 3, "time": "2026-11-14T04:57:00", "kind": "判定", "src": "程序", "dst": "",
     "summary": "第 1 轮：测试全部通过", "result": "过", "round": 1},
    {"seq": 4, "time": "2026-11-14T05:07:00", "kind": "回复", "src": "复查员", "dst": "调度员",
     "summary": "结论：通过", "ok": True, "seconds": 190},
]
AFTER_STATES = ("待验收", "已合并", "需Claude介入", "已放弃")


def view_of(state, events):
    return card_view(make_card(state, events), "CARD-001")


def stations(view):
    return {s["key"]: s for s in view["stations"]}


class NoSpinnerAfterDevelopment(unittest.TestCase):
    def test_open_dispatch_is_void_once_card_moved_on(self):
        for state in AFTER_STATES:
            with self.subTest(state=state):
                v = view_of(state, [OPEN_DEV, STOPPED])
                st = stations(v)
                self.assertEqual([k for k, s in st.items() if s["state"] == "active"], [])
                self.assertIn("停下", st["dev"]["detail"])
                self.assertIsNone(st["dev"].get("elapsed"))
                self.assertNotIn("正在", v["now"]["text"])   # 顶部不再写“正在返工”
                self.assertFalse(v.get("live"))

    def test_earlier_finished_round_keeps_its_ticks(self):
        events = EARLIER_ROUND + [dict(OPEN_DEV, seq=5), dict(STOPPED, seq=6)]
        for state in ("待验收", "已合并"):
            with self.subTest(state=state):
                v = view_of(state, events)
                st = stations(v)
                for k in ("dev", "check", "rev"):   # 已完成的卡，中间三步照最后做完的结果打勾
                    self.assertEqual(st[k]["state"], "done", k)
                    self.assertNotIn("等这一轮", st[k]["detail"])
                self.assertIn("停下", st["dev"]["detail"])
                self.assertNotIn("正在", v["now"]["text"])
        self.assertEqual(view_of("已合并", events)["now"]["text"], "已放进项目")

    def test_merged_card_says_honestly_what_was_rechecked(self):
        def verdict(summary, kind="判定"):
            return {"seq": 5, "time": "2026-11-14T05:17:00", "kind": kind, "src": "程序", "dst": "开发员",
                    "summary": summary, "result": "没过", "round": 1}
        tail = [dict(OPEN_DEV, seq=6), dict(STOPPED, seq=7)]
        claude_ok = {"seq": 8, "time": "2026-11-14T06:48:00", "kind": "验收", "src": "Claude", "dst": "你",
                     "summary": "Claude 验收通过：全部测试通过"}
        full_fail = verdict("第 1 轮：复查过了，但全部测试里有老测试被改坏了（test_x）")
        own_fail = verdict("第 2 轮：测试没过")
        page_fail = verdict("页面检查：没过", kind="页面检查")
        # 本卡测试没过：放进项目时重跑本卡测试并通过，可以打勾
        st = stations(view_of("已合并", EARLIER_ROUND + [own_fail] + tail))
        self.assertEqual(st["check"]["state"], "done")
        self.assertIn("本卡测试已通过", st["check"]["detail"])
        # 全部测试、页面检查没过：放进项目时没复验，不许装成通过
        for bad in (full_fail, page_fail):
            st = stations(view_of("已合并", EARLIER_ROUND + [bad] + tail))
            self.assertEqual(st["check"]["state"], "fail", bad["summary"])
            self.assertIn("没再复验", st["check"]["detail"])
        # Claude 之后验收过全部测试：打勾并说是 Claude 验收的
        st = stations(view_of("已合并", EARLIER_ROUND + [full_fail] + tail + [claude_ok]))
        self.assertEqual(st["check"]["state"], "done")
        self.assertIn("Claude 最后检查", st["check"]["detail"])
        # 真实的全部测试结果事件比本卡判定更新：按最后那次判断，不许打勾
        full_event = {"seq": 6, "time": "2026-11-14T05:30:00", "kind": "全量", "src": "程序", "dst": "",
                      "summary": "全部测试里这些老测试没过：test_x", "phase": "结果", "result": "没过"}
        later = [dict(OPEN_DEV, seq=7), dict(STOPPED, seq=8)]
        st = stations(view_of("已合并", EARLIER_ROUND + [own_fail, full_event] + later))
        self.assertNotEqual(st["check"]["state"], "done")
        # Claude 验收在全部测试失败之前：不算复验过（程序记全部测试没过时，紧跟一条判定）
        early_ok = dict(claude_ok, seq=5)
        full_verdict = dict(full_fail, seq=7)
        st = stations(view_of("已合并", EARLIER_ROUND + [early_ok, full_event, full_verdict]
                              + [dict(OPEN_DEV, seq=8), dict(STOPPED, seq=9)]))
        self.assertEqual(st["check"]["state"], "fail")
        self.assertIn("没再复验", st["check"]["detail"])
        # 还没放进项目：照实显示红叉
        self.assertEqual(stations(view_of("待验收", EARLIER_ROUND + [own_fail] + tail))["check"]["state"], "fail")

    def test_normal_development_still_spins(self):
        v = view_of("开发中", [OPEN_DEV])
        self.assertEqual(stations(v)["dev"]["state"], "active")
        st = stations(view_of("开发中", EARLIER_ROUND + [dict(OPEN_DEV, seq=5)]))
        self.assertIn("等这一轮", st["check"]["detail"])   # 返工中上一轮结果仍不冒回来


if __name__ == "__main__":
    unittest.main()
