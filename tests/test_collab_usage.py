import json
import tempfile
import unittest
from pathlib import Path

from collab.usage import count_lines, record_card_usage, usage_since


def _line(message_id, usage):
    return json.dumps({"type": "assistant",
                       "message": {"id": message_id, "usage": usage}}) + "\n"


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jsonl = Path(self.tmp.name) / "session.jsonl"
        self.jsonl.write_text(
            _line("m1", {"input_tokens": 2, "cache_creation_input_tokens": 100,
                         "cache_read_input_tokens": 50, "output_tokens": 10})
            + json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n"
            + _line("m2", {"input_tokens": 1, "cache_creation_input_tokens": 0,
                           "cache_read_input_tokens": 200, "output_tokens": 30}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sums_all_four_fields(self):
        u = usage_since(self.jsonl, 0)
        self.assertEqual(u["input"], 3)
        self.assertEqual(u["cache_creation"], 100)
        self.assertEqual(u["cache_read"], 250)
        self.assertEqual(u["output"], 40)
        self.assertEqual(u["total"], 393)

    def test_same_message_id_counted_once(self):
        # 流式输出时同一条消息会重复写多行，只能计一次
        dup = self.jsonl.read_text(encoding="utf-8") + _line("m2", {"output_tokens": 30})
        self.jsonl.write_text(dup, encoding="utf-8")
        self.assertEqual(usage_since(self.jsonl, 0)["output"], 40)

    def test_since_line_counts_only_the_window(self):
        before = count_lines(self.jsonl)
        with self.jsonl.open("a", encoding="utf-8") as fh:
            fh.write(_line("m3", {"input_tokens": 5, "output_tokens": 7}))
        self.assertEqual(usage_since(self.jsonl, before)["total"], 12)

    def test_missing_file_is_zero(self):
        self.assertEqual(usage_since(Path(self.tmp.name) / "nope.jsonl", 0)["total"], 0)

    def test_record_appends_row_to_card_ledger(self):
        collab = Path(self.tmp.name) / ".collab"
        collab.mkdir()
        record_card_usage(collab, "CARD-001", {"input": 1, "cache_creation": 2,
                                               "cache_read": 3, "output": 4, "total": 10})
        record_card_usage(collab, "CARD-002", {"input": 0, "cache_creation": 0,
                                               "cache_read": 0, "output": 1, "total": 1})
        text = (collab / "额度记录.md").read_text(encoding="utf-8")
        self.assertIn("| CARD-001 | 1 | 2 | 3 | 4 | 10 |", text)
        self.assertIn("| CARD-002 | 0 | 0 | 0 | 1 | 1 |", text)


if __name__ == "__main__":
    unittest.main()
