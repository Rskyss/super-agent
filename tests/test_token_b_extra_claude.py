"""B 卡 Claude 接手后补的测试（Codex 复查第 1 轮）。"""
import json
from pathlib import Path
from unittest.mock import patch

from acceptance_helpers import ProjectCase, wait_until
from desktop.clerk.desk import FOLDER, ClerkDesk
from test_token_b_acceptance import CountingClient


class ClerkUsageClaude(ProjectCase):
    cid = "b" * 32

    def setUp(self):
        super().setUp()
        self.path = Path(self.project) / FOLDER / f"{self.cid}.jsonl"
        self.path.parent.mkdir(parents=True)

    def write(self, rows, tail=""):
        self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + tail, encoding="utf-8")

    def test_half_written_usage_line_does_not_break_the_last_reminder(self):
        """末尾留着写坏的半行用量：重开仍认得最后一条行政提醒，改写它而不是另起一条（中等 4）。"""
        self.write([{"role": "user", "text": "问"}, {"role": "assistant", "text": "旧提醒", "item": "r1"},
                    {"role": "usage", "total": 30}], tail='{"role": "usage", "tot\n')
        desk = ClerkDesk(lambda: None)
        events = desk.events(self.project, self.cid)["events"]
        self.assertTrue(any(e.get("item") == "r1" for e in events), "最后一条提醒保留原编号")
        conv = desk._conversation(self.project, self.cid)
        self.assertTrue(desk._rewrite_last_locked(conv, self.path, "r1", "新提醒"))
        texts = [json.loads(l).get("text") for l in self.path.read_text(encoding="utf-8").splitlines()
                 if l.strip().startswith("{") and l.strip().endswith("}")]
        self.assertIn("新提醒", texts)
        self.assertNotIn("旧提醒", texts)

    def test_failed_usage_save_puts_no_notice_inside_a_reply(self):
        """用量写不进去时不插提醒（提醒插在回答中间会让页面把回答显示两遍，严重 2）。"""
        self.write([{"role": "user", "text": "旧问题"}, {"role": "assistant", "text": "旧回答"}])
        client = CountingClient([{"text": "回答。", "tokens": 50}], title_tokens=None)
        desk = ClerkDesk(lambda: client)
        with patch.object(desk, "_record_line", side_effect=OSError("写不进")):
            desk.send(self.project, self.cid, "再问")
            wait_until(lambda: not desk.events(self.project, self.cid)["busy"], 5)
        kinds = [e["type"] for e in desk.events(self.project, self.cid)["events"]]
        self.assertNotIn("notice", kinds)
        self.assertIn("usage", kinds, "页面照样显示这次的用量")


class ClerkUsageClaudeRound2(ProjectCase):
    """Codex 复查第 2 轮补的测试。"""
    cid = "c" * 32

    def setUp(self):
        super().setUp()
        self.path = Path(self.project) / FOLDER / f"{self.cid}.jsonl"
        self.path.parent.mkdir(parents=True)

    def rewrite_with_tail(self, tail: bytes):
        rows = [{"role": "user", "text": "问"}, {"role": "assistant", "text": "旧提醒", "item": "r1"},
                {"role": "usage", "total": 30}]
        self.path.write_bytes(b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n" for r in rows) + tail)
        desk = ClerkDesk(lambda: None)
        events = desk.events(self.project, self.cid)["events"]
        self.assertTrue(any(e.get("item") == "r1" for e in events), "最后一条提醒保留原编号")
        conv = desk._conversation(self.project, self.cid)
        self.assertTrue(desk._rewrite_last_locked(conv, self.path, "r1", "新提醒"))
        data = self.path.read_bytes()
        self.assertIn("新提醒".encode(), data)
        self.assertNotIn("旧提醒".encode(), data)
        self.assertTrue(data.endswith(tail.rstrip(b"\n") + b"\n") or tail.strip() == b"", "坏行原样留着")

    def test_bad_bytes_at_the_end_do_not_block_rewriting_the_reminder(self):
        self.rewrite_with_tail(b"\xff\xfe broken\n")

    def test_null_line_at_the_end_does_not_block_rewriting_the_reminder(self):
        self.rewrite_with_tail(b"null\n")

    def test_failed_write_ends_its_half_line_before_letting_go(self):
        """写一行失败时，趁还拿着锁补换行：之后写的下一条不会粘到半行上（严重 1）。"""
        self.path.write_text(json.dumps({"role": "user", "text": "问"}, ensure_ascii=False) + "\n", encoding="utf-8")
        desk = ClerkDesk(lambda: None)
        conv = desk._conversation(self.project, self.cid)
        real_open = Path.open
        state = {"broke": False}

        def flaky(path, mode="r", *a, **k):
            handle = real_open(path, mode, *a, **k)
            if path == self.path and mode == "a" and not state["broke"]:
                state["broke"] = True
                real_write = handle.write

                def half(text):
                    real_write(text[: len(text) // 2])
                    handle.flush()
                    raise OSError("写到一半")
                handle.write = half
            return handle

        with patch.object(Path, "open", new=flaky):
            with self.assertRaises(OSError):
                desk._record_line(conv, {"role": "usage", "total": 99})
        desk._record_line(conv, {"role": "action", "label": "读 x", "text": "看了"})
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        self.assertTrue(any(r.get("role") == "action" for r in rows if isinstance(r, dict)), "下一条完整留下")


class ClerkUsageClaudeRound3(ProjectCase):
    """Codex 复查第 3 轮补的测试。"""
    cid = "d" * 32

    def setUp(self):
        super().setUp()
        self.path = Path(self.project) / FOLDER / f"{self.cid}.jsonl"
        self.path.parent.mkdir(parents=True)

    def test_line_separator_in_a_message_survives_rewriting_the_reminder(self):
        rows = [{"role": "user", "text": "问"}, {"role": "assistant", "text": "第一段\u2028第二段"},
                {"role": "assistant", "text": "旧提醒", "item": "r1"}]
        self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        desk = ClerkDesk(lambda: None)
        conv = desk._conversation(self.project, self.cid)
        self.assertTrue(desk._rewrite_last_locked(conv, self.path, "r1", "新提醒"))
        texts = [e.get("text") for e in ClerkDesk(lambda: None).events(self.project, self.cid)["events"]
                 if e.get("type") == "message"]
        self.assertIn("第一段\u2028第二段", texts)
        self.assertIn("新提醒", texts)

    def test_bad_bytes_inside_a_record_count_as_a_bad_line(self):
        rows = [{"role": "user", "text": "问"}, {"role": "assistant", "text": "旧提醒", "item": "r1"}]
        self.path.write_bytes(b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n" for r in rows)
                              + b'{"role":"assistant","text":"\xff"}\n')
        desk = ClerkDesk(lambda: None)
        conv = desk._conversation(self.project, self.cid)
        self.assertTrue(desk._rewrite_last_locked(conv, self.path, "r1", "新提醒"))

    def test_next_message_after_a_failed_newline_starts_on_its_own_line(self):
        self.path.write_text(json.dumps({"role": "user", "text": "问"}, ensure_ascii=False) + "\n", encoding="utf-8")
        desk = ClerkDesk(lambda: None)
        conv = desk._conversation(self.project, self.cid)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"role": "usage", "tot')    # 写坏的半行，连换行都没补上
        conv.tail_dirty = True
        desk._record(conv, "assistant", "恢复后的回答")
        texts = [e.get("text") for e in ClerkDesk(lambda: None).events(self.project, self.cid)["events"]
                 if e.get("type") == "message"]
        self.assertIn("恢复后的回答", texts)


class ClerkUsageClaudeConfirm(ProjectCase):
    """确认复查补的测试（修补未经独立复查）。"""
    cid = "e" * 32

    def test_dirty_tail_survives_a_message_write_that_fails_on_close(self):
        path = Path(self.project) / FOLDER / f"{self.cid}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"role": "user", "text": "问"}, ensure_ascii=False) + "\n", encoding="utf-8")
        desk = ClerkDesk(lambda: None)
        conv = desk._conversation(self.project, self.cid)
        conv.tail_dirty = True
        real_open = Path.open

        class FailOnClose:
            def __init__(self, inner): self.inner = inner
            def write(self, text): return len(text)          # 缓着没落盘
            def __enter__(self): return self
            def __exit__(self, *exc):
                self.inner.close()
                raise OSError("关文件时写不进")

        with patch.object(Path, "open", new=lambda p, mode="r", *a, **k:
                          FailOnClose(real_open(p, mode, *a, **k)) if (p == path and mode == "a") else real_open(p, mode, *a, **k)):
            with self.assertRaises(OSError):
                desk._record(conv, "assistant", "没写进去的")
        self.assertTrue(conv.tail_dirty, "没真写进去，标记得留着")
