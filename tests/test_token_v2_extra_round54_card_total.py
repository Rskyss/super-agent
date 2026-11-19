"""第四轮 54 复查退回：本卡合计要算上交接和起草漏掉的轮次。"""

from pathlib import Path
from unittest.mock import patch

from acceptance_helpers import ProjectCase
from collab.flow import record
from desktop.view import card_view
from test_token_v2_acceptance import CODEX_TOTAL, HEADER, put_codex


class CardTotalReviewReturn(ProjectCase):
    def test_total_backfills_old_draft_and_reports_missing_handover_and_draft_rounds(self):
        cdir = self.make_card(state="需Claude介入")
        home = Path(self.tmp) / "codex"
        put_codex(home)

        # 老版给 agy 或 Cursor 备的交接资料只记完成情况，没有用量字段或实时文件。
        record(cdir, "交接资料", src="调度员", dst="开发员", ok=True, seconds=5)
        record(cdir, "交接资料", src="调度员", dst="开发员", ok=False, seconds=5,
               tokens={"ok": False, "reason": "这次交接没有返回结果，读不到用量"})

        old_draft = record(cdir, "派活", src="调度员", dst="调度员", tool="codex",
                           step="起草", round=1, live=True)
        (cdir / old_draft["live"]).write_text(HEADER, encoding="utf-8")
        record(cdir, "回复", src="调度员", dst="调度员", tool="codex", step="起草",
               round=1, reply_to=old_draft["seq"], ok=True)

        failed_draft = record(cdir, "派活", src="调度员", dst="调度员", tool="codex",
                              step="起草", round=2, live=True)
        record(cdir, "回复", src="调度员", dst="调度员", tool="codex", step="起草",
               round=2, reply_to=failed_draft["seq"], ok=False,
               tokens={"ok": False, "reason": "输出里没有会话编号"})

        with patch.dict("os.environ", {"CODEX_HOME": str(home)}):
            tokens = card_view(self.project, "CARD-001")["tokens"]

        self.assertEqual(tokens["total"], CODEX_TOTAL, "老卡的起草用量应补进本卡合计一次")
        self.assertEqual(tokens.get("missing", 0), 3, "两次交接、一次起草读不到用量")
