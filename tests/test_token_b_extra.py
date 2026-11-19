"""行政对话累计用量的补充验收。"""

import json
import threading
import unittest
import uuid
from queue import Empty, Queue
from unittest.mock import patch

from acceptance_helpers import ProjectCase, needs_node, run_node
from desktop.clerk.desk import FOLDER, ClerkDesk


class UsageClient:
    def __init__(self, replies=(), title=None):
        self.replies = iter(replies)
        self.title = {"text": "标题"} if title is None else title
        self.prompts = []

    def chat(self, messages, on_text=None, tools=None):
        if on_text is None:
            step = self.title
        else:
            self.prompts.append([dict(message) for message in messages])
            step = next(self.replies)
        result = step() if callable(step) else step
        return {"text": "已收到。", "tool_calls": [], **result}


class TrackedDesk(ClerkDesk):
    """等工作线程真正收尾后再核对事件和文件，避免把已空闲当成标题已写完。"""

    def __init__(self, client):
        super().__init__(lambda: client)
        self.replies = Queue()
        self.titles = Queue()

    def _reply(self, *args, **kwargs):
        self.replies.put(threading.current_thread())
        return super()._reply(*args, **kwargs)

    def _name(self, *args, **kwargs):
        self.titles.put(threading.current_thread())
        return super()._name(*args, **kwargs)


class HeldResult:
    def __init__(self, result):
        self.result = result
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self):
        self.entered.set()
        if not self.release.wait(12):
            raise AssertionError("假模型等不到放行")
        return self.result


class ClerkUsageExtra(ProjectCase):
    def transcript(self, cid):
        return self.collab / FOLDER.name / f"{cid}.jsonl"

    def write_history(self, rows):
        cid = uuid.uuid4().hex
        path = self.transcript(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        return cid

    def rows(self, cid):
        return [json.loads(line) for line in self.transcript(cid).read_text(encoding="utf-8").splitlines()]

    def usage_rows(self, cid):
        return [row.get("total") for row in self.rows(cid) if row["role"] == "usage"]

    def usage_events(self, desk, cid):
        return [(event.get("total"), event.get("history", False))
                for event in desk.events(self.project, cid)["events"] if event["type"] == "usage"]

    def finish(self, workers):
        try:
            worker = workers.get(timeout=5)
        except Empty:
            self.fail("行政的回答或标题线程没有启动")
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "行政的回答或标题没有收尾")

    def test_invalid_tokens_never_emit_or_write_a_usage_total(self):
        invalid = [None, True, False, 0, -2, 2.5, 4.0, "123"]
        client = UsageClient([{}] + [{"tokens": value} for value in invalid],
                             title={"text": "标题", "tokens": True})
        desk = TrackedDesk(client)
        cid = desk.send(self.project, "", "第一句")
        self.finish(desk.replies)
        self.finish(desk.titles)
        with self.subTest(tokens="回答没报用量，标题报了布尔值"):
            self.assertEqual(self.usage_events(desk, cid), [])
            self.assertEqual(self.usage_rows(cid), [])
        for value in invalid:
            with self.subTest(tokens=value):
                desk.send(self.project, cid, "接着聊")
                self.finish(desk.replies)
        self.assertFalse([event for event in desk.events(self.project, cid)["events"] if event["type"] == "error"])
        self.assertEqual(self.usage_events(desk, cid), [])
        self.assertEqual(self.usage_rows(cid), [])

    def test_old_transcript_without_usage_starts_empty_and_can_begin_counting(self):
        cid = self.write_history([{"role": "user", "text": "旧问题"}, {"role": "assistant", "text": "旧回答"}])
        desk = TrackedDesk(UsageClient([{"tokens": 31}]))
        before = self.rows(cid)
        self.assertEqual(self.usage_events(desk, cid), [])
        self.assertEqual(self.rows(cid), before, "打开旧对话不能补写 0")

        desk.send(self.project, cid, "继续")
        self.finish(desk.replies)
        self.assertEqual(self.usage_events(desk, cid), [(31, False)])
        self.assertEqual(self.usage_rows(cid), [31])

    def test_reopen_restores_only_the_last_valid_total_without_showing_it_as_a_message(self):
        cid = self.write_history([
            {"role": "user", "text": "旧问题"},
            {"role": "usage", "total": 13},
            {"role": "assistant", "text": "旧回答"},
            {"role": "usage", "total": 48},
            *[{"role": "usage", "total": value} for value in (None, True, 0, -4, 80.5, 90.0, "999")],
            {"role": "usage"},
        ])
        client = UsageClient([{"tokens": 12}])
        desk = TrackedDesk(client)
        history = desk.events(self.project, cid)["events"]
        self.assertEqual([(event["type"], event.get("text")) for event in history if event["type"] != "usage"],
                         [("user", "旧问题"), ("message", "旧回答")])
        with self.subTest(check="历史只发最后一个有效数"):
            self.assertEqual(self.usage_events(desk, cid), [(48, True)])

        desk.send(self.project, cid, "继续")
        self.finish(desk.replies)
        self.assertEqual(client.prompts[0][1:], [
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "继续"},
        ])
        self.assertEqual(self.usage_events(desk, cid), [(48, True), (60, False)])
        self.assertEqual(self.usage_rows(cid)[-1], 60)

    def test_empty_title_and_next_reply_keep_order_in_events_and_on_disk(self):
        for title_first, middle in ((True, 107), (False, 111)):
            with self.subTest(title_first=title_first):
                title = HeldResult({"text": "", "tokens": 7})
                reply = HeldResult({"text": "第二次回答", "tokens": 11})
                desk = TrackedDesk(UsageClient([{"tokens": 100}, reply], title=title))
                cid = desk.send(self.project, "", "第一句")
                try:
                    self.finish(desk.replies)
                    self.assertTrue(title.entered.wait(5), "起标题的调用没有开始")
                    desk.send(self.project, cid, "再问一句")
                    self.assertTrue(reply.entered.wait(5), "下一次回答的调用没有开始")
                    first, second = (title, reply) if title_first else (reply, title)
                    first_worker, second_worker = ((desk.titles, desk.replies) if title_first
                                                   else (desk.replies, desk.titles))
                    first.release.set()
                    self.finish(first_worker)
                    second.release.set()
                    self.finish(second_worker)
                finally:
                    title.release.set()
                    reply.release.set()

                totals = [100, middle, 118]
                with self.subTest(check="事件顺序"):
                    self.assertEqual(self.usage_events(desk, cid), [(total, False) for total in totals])
                with self.subTest(check="落盘顺序"):
                    self.assertEqual(self.usage_rows(cid), totals)
                reopened = ClerkDesk(lambda: None)
                self.assertEqual(self.usage_events(reopened, cid), [(118, True)])

    def test_next_reply_cannot_write_a_newer_total_before_the_title_total(self):
        title = HeldResult({"text": "标题", "tokens": 7})
        reply = HeldResult({"tokens": 11})
        desk = TrackedDesk(UsageClient([{"tokens": 100}, reply], title=title))
        title_write = HeldResult(None)
        next_write = threading.Event()
        record_line = desk._record_line

        def delay_title_write(conv, line):
            total = line.get("total") if line.get("role") == "usage" else None
            if total == 107:
                title_write()
            record_line(conv, line)
            if total not in (None, 100, 107):
                next_write.set()

        with patch.object(desk, "_record_line", side_effect=delay_title_write):
            cid = desk.send(self.project, "", "第一句")
            next_sent = False
            try:
                self.finish(desk.replies)
                self.assertTrue(title.entered.wait(5), "起标题的调用没有开始")
                desk.send(self.project, cid, "再问一句")
                next_sent = True
                self.assertTrue(reply.entered.wait(5), "下一次回答的调用没有开始")
                title.release.set()
                self.assertTrue(title_write.entered.wait(2), "标题的用量没有写入对话记录")
                reply.release.set()
                # 故意卡住标题这笔的落盘，看已拿到模型结果的回答会不会抢先写入。
                next_write.wait(0.2)
            finally:
                title.release.set()
                reply.release.set()
                title_write.release.set()
                if next_sent:
                    self.finish(desk.replies)
                if title.entered.is_set():
                    self.finish(desk.titles)

        self.assertEqual(self.usage_events(desk, cid), [(100, False), (107, False), (118, False)])
        self.assertEqual(self.usage_rows(cid), [100, 107, 118])

    def test_late_title_usage_does_not_revive_deleted_conversation(self):
        title = HeldResult({"text": "迟到的标题", "tokens": 7})
        desk = TrackedDesk(UsageClient([{}], title=title))
        cid = desk.send(self.project, "", "这段会删除")
        try:
            self.finish(desk.replies)
            self.assertTrue(title.entered.wait(5), "起标题的调用没有开始")
            conv = desk._conversation(self.project, cid)
            desk.delete(self.project, cid)
        finally:
            title.release.set()
        self.finish(desk.titles)

        self.assertFalse(any(event["type"] == "usage" for event in conv.events))
        self.assertFalse(self.transcript(cid).exists())
        self.assertFalse(desk.events(self.project, cid)["open"])

    def test_trailing_usage_keeps_original_reminder_id_and_allows_updating_it(self):
        old, new = "开发员已经 5 分钟没动静。", "开发员已经 10 分钟没动静。"
        item = uuid.uuid4().hex
        usage = {"role": "usage", "total": 83}
        cid = self.write_history([
            {"role": "user", "text": "帮我看一下"},
            {"role": "assistant", "text": old, "item": item},
            usage,
        ])
        desk = ClerkDesk(lambda: None)
        messages = [event for event in desk.events(self.project, cid)["events"] if event["type"] == "message"]
        with self.subTest(check="重开后认得原提醒"):
            self.assertEqual([(event["item"], event["text"]) for event in messages], [(item, old)])
        with self.subTest(check="更新原提醒时沿用编号"):
            self.assertEqual(desk.notify(self.project, cid, new, item=item), item)
        rows = self.rows(cid)
        self.assertEqual([(row.get("item"), row["text"]) for row in rows if row["role"] == "assistant"], [(item, new)])
        self.assertEqual([row for row in rows if row["role"] == "usage"], [usage])


@needs_node()
class HeaderUsageExtra(unittest.TestCase):
    def test_header_number_format_boundaries(self):
        result = run_node("""
            const assert = require('assert');
            const { headerUsage } = require('./desktop/static/chat.js');
            for (const [total, expected] of [
                [9999, '本段对话 9,999 token'],
                [10000, '本段对话 1.0 万 token'],
                [99999999, '本段对话 10000.0 万 token'],
                [100000000, '本段对话 1.0 亿 token'],
                [123456789, '本段对话 1.2 亿 token'],
                [-1, ''], [NaN, ''], [Infinity, ''], ['123', ''], [true, ''],
            ]) {
                assert.strictEqual(headerUsage(total), expected, `用量为 ${total} 时的顶部文字`);
            }
        """)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_header_renders_history_and_live_updates_and_clears_for_new_chat(self):
        result = run_node("""
            const assert = require('assert');
            const fs = require('fs');
            const vm = require('vm');
            const elements = {};
            const timers = [];
            const cid = 'a'.repeat(32);
            let total = 8512;
            const page = {
                URLSearchParams, location: { search: '?project=/p&token=t&clerk=1' },
                document: { getElementById(id) {
                    return elements[id] ??= { textContent: '', addEventListener() {}, setAttribute() {} };
                } },
                addEventListener() {}, clearTimeout() {},
                setTimeout(callback) { return timers.push(callback); },
                async fetch(url) {
                    const request = new URL(url, 'http://local.test');
                    let data = {};
                    if (request.pathname === '/api/clerk/list') data = { threads: [{ id: cid }] };
                    if (request.pathname === '/api/clerk/events') {
                        assert.strictEqual(request.searchParams.get('conversation'), cid);
                        const since = Number(request.searchParams.get('since'));
                        data = { open: true, busy: false, events: [
                            { type: 'usage', seq: since + 1, total, history: since === 0 },
                        ] };
                    }
                    return { ok: true, json: async () => data };
                },
            };
            page.window = page;
            vm.runInNewContext(fs.readFileSync('./desktop/static/chat.js', 'utf8'), page);
            const settle = () => new Promise(resolve => setImmediate(resolve));
            (async () => {
                await settle();
                assert.strictEqual(elements.usage.textContent, '本段对话 8,512 token');
                total = 12345;
                timers.shift()();
                await settle();
                assert.strictEqual(elements.usage.textContent, '本段对话 1.2 万 token');
                page.SuperAgentChat.startFresh();
                assert.strictEqual(elements.usage.textContent, '');
            })().catch(error => { console.error(error); process.exitCode = 1; });
        """)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
