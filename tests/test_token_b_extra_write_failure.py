"""行政对话的用量写入失败一次后，重开仍能读到完整累计数。"""

import json
from unittest.mock import patch

from acceptance_helpers import ProjectCase, wait_until
from desktop.clerk.desk import FOLDER, ClerkDesk
from test_token_b_acceptance import CountingClient, usage_total


class ClerkUsageWriteFailure(ProjectCase):
    def test_usage_survives_reopening_after_one_failed_write(self):
        cid = "a" * 32
        folder = self.collab / FOLDER.name
        folder.mkdir(parents=True)
        history = [
            {"role": "user", "text": "之前的问题"},
            {"role": "assistant", "text": "之前的回答"},
            {"role": "usage", "total": 100},
        ]
        (folder / f"{cid}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in history), encoding="utf-8"
        )

        client = CountingClient([{"text": "接着回答。", "tokens": 50}])
        desk = ClerkDesk(lambda: client)
        initial_events = desk.events(self.project, cid)["events"]
        self.assertEqual(usage_total(initial_events), 100)
        record_line = desk._record_line
        write_failed = False

        def fail_first_usage_write(conv, line):
            nonlocal write_failed
            if line.get("role") == "usage" and not write_failed:
                write_failed = True
                raise OSError("模拟用量第一次写入失败")
            return record_line(conv, line)

        def finished_events():
            events = desk.events(self.project, cid, since=initial_events[-1]["seq"])["events"]
            return events if any(event["type"] == "turn_done" for event in events) else None

        with patch.object(desk, "_record_line", side_effect=fail_first_usage_write):
            desk.send(self.project, cid, "接着聊")
            events = wait_until(finished_events, message="行政没有回完这句话")

        self.assertTrue(write_failed, "测试没有触发用量写入失败")
        self.assertIn("接着回答。", [event.get("text") for event in events if event["type"] == "message"])
        # 立即用新实例读记录，中间没有下一次模型调用补写累计数。
        reopened = ClerkDesk(lambda: None)
        restored_events = reopened.events(self.project, cid)["events"]
        self.assertEqual(
            (usage_total(events), usage_total(restored_events)),
            (150, 150),
            "本轮用量写入失败一次后，页面收到的累计数和重开后读到的累计数都应是 150",
        )
