"""卡片只记运行时间、不记钟点（用户 2026-11-19 截图“已干 787:27”后定：“开发助手不记录时区时间，只记录运行时间”）。

后台跑卡的终端是纽约时间、开发助手窗口是北京时间，记录里写“几点几分”，两边一减差 13 小时。
改成每条记录只记一个跟时区无关的秒数（ts），用了多久都用它相减；交接列表不再显示钟点，改显示开工后第几分钟。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_in(tz, code):
    env = dict(os.environ, TZ=tz, PYTHONPATH=str(ROOT))
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


class RunTimeOnly(unittest.TestCase):
    def test_record_keeps_seconds_not_clock(self):
        from collab.flow import read_flow, record
        with tempfile.TemporaryDirectory() as tmp:
            record(Path(tmp), "派活", src="程序", dst="开发员", summary="写代码")
            event = read_flow(Path(tmp))[-1]
        self.assertNotIn("time", event, "不记几点几分")
        self.assertIsInstance(event.get("ts"), (int, float))
        self.assertLess(abs(event["ts"] - time.time()), 60)

    def test_written_in_new_york_read_in_shanghai(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_in("America/New_York",
                   f"from pathlib import Path; from collab.flow import record; "
                   f"record(Path({tmp!r}), '派活', src='程序', dst='开发员', summary='写代码')")
            minutes = run_in("Asia/Shanghai",
                             f"import json; from datetime import datetime; from pathlib import Path; "
                             f"from desktop.view import _when; "
                             f"e = json.loads(Path({tmp!r}, '流转.jsonl').read_text(encoding='utf-8').splitlines()[-1]); "
                             f"print(int((datetime.now() - _when(e)).total_seconds() // 60))")
        self.assertLess(abs(int(minutes)), 2, f"刚写的记录换个时区读也是刚刚，不能差出 13 小时：{minutes} 分钟")

    def test_old_records_with_clock_still_read(self):
        from desktop.view import _when
        self.assertEqual(_when({"time": "2026-11-18T20:30:23"}), datetime(2026, 11, 18, 20, 30, 23))

    def test_handoff_list_shows_minutes_since_start_not_clock(self):
        from desktop.view import _handoffs
        t0 = 1_800_000_000
        rows = _handoffs([
            {"seq": 1, "ts": t0, "kind": "派活", "src": "程序", "dst": "测试员", "summary": "写测试"},
            {"seq": 2, "ts": t0 + 754, "kind": "回复", "src": "测试员", "dst": "程序", "summary": "写好了"},
        ])
        self.assertEqual([r["time"] for r in rows], ["+0:00", "+12:34"])

    def test_handoff_list_long_card_counts_hours(self):
        from desktop.view import _handoffs
        t0 = 1_800_000_000
        rows = _handoffs([
            {"seq": 1, "ts": t0, "kind": "派活", "src": "程序", "dst": "测试员", "summary": "写测试"},
            {"seq": 2, "ts": t0 + 3 * 3600 + 5 * 60 + 9, "kind": "回复", "src": "测试员", "dst": "程序", "summary": "好"},
        ])
        self.assertEqual(rows[-1]["time"], "+3:05:09")

    def test_handoff_list_counts_from_start_of_work(self):
        from desktop.view import _handoffs
        t0 = 1_800_000_000
        rows = _handoffs([
            {"seq": 1, "ts": t0, "kind": "需求", "src": "你", "dst": "程序", "summary": "提需求"},
            {"seq": 2, "ts": t0 + 900, "kind": "开跑", "src": "程序", "dst": "程序", "summary": "开始施工"},
            {"seq": 3, "ts": t0 + 960, "kind": "派活", "src": "程序", "dst": "测试员", "summary": "写测试"},
        ])
        self.assertEqual([r["time"] for r in rows], ["", "+0:00", "+1:00"], "开工前的对齐、等批准不算进开工后的时间")


if __name__ == "__main__":
    unittest.main()
