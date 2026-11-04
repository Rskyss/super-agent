import tempfile
import unittest
from pathlib import Path

from collab.ledger import ledger_add


class LedgerTest(unittest.TestCase):
    def test_creates_ledger_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_add(tmp, "CARD-001 方案已确认")
            text = (Path(tmp) / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
            self.assertIn("CARD-001 方案已确认", text)

    def test_appends_without_losing_existing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "docs" / "开发进度跟踪.md"
            path.parent.mkdir(parents=True)
            path.write_text("# 开发进度跟踪\n\n- 旧记录\n", encoding="utf-8")
            ledger_add(tmp, "CARD-002 待验收")
            text = path.read_text(encoding="utf-8")
            self.assertIn("- 旧记录", text)
            self.assertIn("CARD-002 待验收", text)


if __name__ == "__main__":
    unittest.main()
