"""施工单 B 补充：旧记录、返工、中断续跑的右栏取数。

只用临时项目；不修改已有验收测试，不调用真实模型。
"""
import unittest
from unittest import mock

from acceptance_helpers import ProjectCase
from collab.flow import record
from desktop.view import card_view


def usage(total):
    return dict(ok=True, total=total, input=total, cached=0, output=0,
                reasoning=0, children=0)


class HistoryEdges(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(state="测试中")

    def station(self, key):
        return next(s for s in card_view(self.project, "CARD-001")["stations"]
                    if s["key"] == key)

    def send(self, role="开发员", summary="接着处理"):
        return record(self.cdir, "派活", src="调度员", dst=role, tool="codex",
                      step="开发", summary=summary, live=True)

    def reply(self, role="开发员", seconds=None, tokens=None, summary="已处理", ok=True):
        sent = self.send(role)
        extra = {}
        if seconds is not None:
            extra["seconds"] = seconds
        if tokens is not None:
            extra["tokens"] = tokens
        record(self.cdir, "回复", src=role, dst="调度员", tool="codex", step="开发",
               reply_to=sent["seq"], summary=summary, ok=ok, **extra)

    def test_missing_time_is_not_zero_and_missing_usage_is_not_fabricated(self):
        self.reply(tokens=usage(23))
        self.reply(tokens={"ok": False, "reason": "这轮没读到"})
        dev = self.station("dev")
        self.assertEqual(dev.get("rounds"), 2)
        self.assertIsNone(dev.get("spent"))
        self.assertEqual(dev.get("history"), [
            {"seconds": None, "tokens": 23}, {"seconds": None, "tokens": None}])

    def test_partial_time_and_zero_usage_survive_in_order(self):
        self.reply(seconds=0, tokens=usage(0))
        self.reply(tokens="旧版记录")
        self.reply(seconds=61, tokens={"ok": False, "total": 999})
        dev = self.station("dev")
        self.assertEqual(dev.get("spent"), 61)
        self.assertEqual(dev.get("history"), [
            {"seconds": 0, "tokens": 0}, {"seconds": None, "tokens": None},
            {"seconds": 61, "tokens": None}])

    def test_zero_seconds_is_a_real_record(self):
        self.reply(seconds=0)
        self.assertEqual(self.station("dev").get("spent"), 0)

    def test_three_roles_keep_their_own_reply_history(self):
        self.reply("测试员", 10, usage(100), "补齐检查")
        self.reply("开发员", 20, usage(200), "第一版")
        record(self.cdir, "提醒", src="开发员", dst="你", summary="还在处理", seconds=999)
        self.reply("开发员", 40, usage(400), "汇报：补齐遗漏")
        self.reply("复查员", 30, usage(300), "结论：不通过，仍有遗漏")
        for key, rounds, spent, history, detail in (
            ("test", 1, 10, [{"seconds": 10, "tokens": 100}], "补齐检查"),
            ("dev", 2, 60, [{"seconds": 20, "tokens": 200},
                             {"seconds": 40, "tokens": 400}], "汇报：补齐遗漏"),
            ("rev", 1, 30, [{"seconds": 30, "tokens": 300}], "结论：不通过，仍有遗漏"),
        ):
            with self.subTest(key=key):
                station = self.station(key)
                self.assertEqual(station.get("rounds"), rounds)
                self.assertEqual(station.get("spent"), spent)
                self.assertEqual(station.get("history"), history)
                self.assertEqual(station["detail"], detail)

    def test_failed_worker_reply_is_still_a_completed_round(self):
        self.reply(seconds=7, tokens=usage(12), summary="连接中断", ok=False)
        dev = self.station("dev")
        self.assertEqual(dev["state"], "fail")
        self.assertEqual(dev["detail"], "连接中断")
        self.assertEqual(dev.get("rounds"), 1)
        self.assertEqual(dev.get("history"), [{"seconds": 7, "tokens": 12}])

    def test_first_active_round_has_no_made_up_finished_time(self):
        self.send(summary="正在检查刷新")
        dev = self.station("dev")
        self.assertEqual(dev["state"], "active")
        self.assertEqual(dev["detail"], "正在检查刷新")
        self.assertEqual(dev.get("rounds"), 1)
        self.assertIsNone(dev.get("spent"))
        self.assertEqual(dev.get("history"), [])

    def test_interrupted_round_is_counted_and_resume_keeps_finished_history(self):
        self.reply(seconds=11, tokens=usage(120))
        record(self.cdir, "开跑", src="程序", pid=987654321, summary="继续")
        sent = self.send()
        with mock.patch("desktop.view.process_is_alive", return_value=False):
            dev = self.station("dev")
            self.assertEqual(dev["state"], "wait")
            self.assertEqual(dev.get("rounds"), 2)
            self.assertEqual(dev.get("spent"), 11)
            self.assertEqual(dev.get("history"), [{"seconds": 11, "tokens": 120}])
        record(self.cdir, "回复", src="开发员", dst="调度员", tool="codex", step="开发",
               reply_to=sent["seq"], ok=True, summary="继续后已完成", seconds=9, tokens=usage(80))
        with mock.patch("desktop.view.process_is_alive", return_value=True):
            dev = self.station("dev")
        self.assertEqual(dev.get("rounds"), 2, "交活后不能又多算一轮")
        self.assertEqual(dev.get("spent"), 20)
        self.assertEqual(dev.get("history"), [
            {"seconds": 11, "tokens": 120}, {"seconds": 9, "tokens": 80}])
        self.assertEqual(dev["detail"], "继续后已完成")

    def test_idle_and_non_worker_stations_get_no_history_fields(self):
        self.reply(seconds=4)
        for key in ("rev", "you", "disp", "check", "accept"):
            with self.subTest(key=key):
                station = self.station(key)
                for field in ("history", "spent", "rounds"):
                    self.assertNotIn(field, station)


if __name__ == "__main__":
    unittest.main()
