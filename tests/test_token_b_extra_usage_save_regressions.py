"""行政对话用量保存失败时，页面和重开后的回归测试。"""

import errno
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from acceptance_helpers import ProjectCase, needs_node, run_node, wait_until
from desktop.clerk.desk import FOLDER, ClerkDesk
from test_token_b_acceptance import CountingClient, usage_total


class ClerkUsageSaveRegressions(ProjectCase):
    cid = "a" * 32

    def setUp(self):
        super().setUp()
        self.transcript = Path(self.project) / FOLDER / f"{self.cid}.jsonl"
        self.transcript.parent.mkdir(parents=True)
        history = [
            {"role": "user", "text": "旧问题"},
            {"role": "assistant", "text": "旧回答"},
            {"role": "usage", "total": 100},
        ]
        self.transcript.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in history),
            encoding="utf-8",
        )

    def desk_with_replies(self, *replies):
        client = CountingClient(replies)
        return ClerkDesk(lambda: client)

    def finish_turn(self, desk, question):
        before = desk.events(self.project, self.cid)["events"]
        desk.send(self.project, self.cid, question)

        def completed_events():
            events = desk.events(self.project, self.cid, since=before[-1]["seq"])["events"]
            return events if any(event["type"] == "turn_done" for event in events) else None

        return wait_until(completed_events, message="行政没有回完这句话")

    @contextmanager
    def fail_usage_appends(self, *, leave_partial=False, once=False):
        real_open = Path.open
        failed = []

        @contextmanager
        def open_with_fault(path, mode, args, kwargs):
            with real_open(path, mode, *args, **kwargs) as handle:
                real_write = handle.write

                def write(text):
                    try:
                        row = json.loads(text)
                    except ValueError:
                        return real_write(text)
                    if row.get("role") != "usage" or (once and failed):
                        return real_write(text)
                    if leave_partial:
                        real_write(text[:len(text) // 2])
                        handle.flush()
                    failed.append(row["total"])
                    raise OSError(errno.ENOSPC, "模拟对话用量写入失败")

                with patch.object(handle, "write", side_effect=write):
                    yield handle

        def open_path(path, mode="r", *args, **kwargs):
            if path == self.transcript and mode == "a":
                return open_with_fault(path, mode, args, kwargs)
            return real_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", new=open_path):
            yield failed

    def test_01_reopens_with_the_usage_just_shown_after_a_failed_save(self):
        desk = self.desk_with_replies({"text": "继续回答。", "tokens": 50})
        with self.fail_usage_appends(leave_partial=True, once=True) as failed:
            self.finish_turn(desk, "继续聊")
        self.assertEqual(failed, [150], "应在保存新增用量时触发一次写入失败")

        # 没有再问模型，立刻用新实例读同一段对话。
        live = desk.events(self.project, self.cid)["events"]
        reopened = ClerkDesk(lambda: None).events(self.project, self.cid)["events"]
        self.assertEqual(
            (usage_total(live), usage_total(reopened)), (150, 150),
            "写入恢复后，页面收到的累计用量和立即重开读到的用量都应是 150",
        )

    @needs_node()
    def test_02_page_shows_the_streamed_reply_once_when_saving_usage_fails(self):
        reply = "这是一段正常的回答。"
        desk = self.desk_with_replies({"text": reply, "tokens": 50})
        with self.fail_usage_appends() as failed:
            events = self.finish_turn(desk, "请回答")
        self.assertTrue(failed, "应触发保存用量失败")
        self.assertTrue(any(event["type"] == "delta" for event in events), "应先流式显示回答")

        result = run_node(f"""
            const {{ Model }} = require('./desktop/static/chat.js');
            const events = {json.dumps(events)};
            const model = new Model();
            events.forEach(event => model.apply(event));
            const replies = model.blocks.filter(block => block.kind === 'ai')
                .flatMap(block => block.parts.filter(part => part.type === 'text')
                    .map(part => part.text));
            console.log(JSON.stringify(replies));
        """)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout), [reply], "保存用量报错后，页面仍只应显示一份回答")

    def test_03_partial_usage_writes_do_not_swallow_the_next_chat_record(self):
        desk = self.desk_with_replies(
            {"text": "第一次回答。", "tokens": 50}, {"text": "第二次回答。"},
        )
        with self.fail_usage_appends(leave_partial=True) as failed:
            self.finish_turn(desk, "第一次提问")
        self.assertTrue(failed, "应触发用量只写到一半的错误")
        # 再成功保存一轮普通聊天，确认上一轮留下的半行不会吞掉后面的消息。
        self.finish_turn(desk, "第二次提问")

        reopened = ClerkDesk(lambda: None).events(self.project, self.cid)["events"]
        self.assertEqual(
            [(event["type"], event["text"]) for event in reopened
             if event["type"] in ("user", "message")],
            [("user", "旧问题"), ("message", "旧回答"),
             ("user", "第一次提问"), ("message", "第一次回答。"),
             ("user", "第二次提问"), ("message", "第二次回答。")],
            "用量只写到一半时，重开后仍应看到全部聊天记录",
        )
