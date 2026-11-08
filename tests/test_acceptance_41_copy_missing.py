"""41 号单：改好的版本不在了，就不给确认按钮，也不让用户白点。"""
from pathlib import Path

from acceptance_helpers import ProjectCase, ServerCase
from collab.flow import record
from collab.phase import card_phase


class Phase(ProjectCase):
    def ran(self, **kw):
        cdir = self.make_card(state="待验收", plan=True, **kw)
        record(cdir, "开跑", src="程序", dst="程序", summary="开工", pid=1, started="x")
        return cdir

    def copy(self):
        folder = Path(self.project).parent / (Path(self.project).name + "-worktrees") / "CARD-001"
        folder.mkdir(parents=True)
        return folder

    def test_missing_copy(self):
        self.ran()
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["code"], "copy_missing")
        self.assertEqual(phase["label"], "改好的版本不在了")
        self.assertEqual(phase["actions"], ["discard"])
        self.assertTrue(phase["needs_you"])
        self.assertEqual(phase["tone"], "bad")
        self.assertIn("重新提", phase["detail"])

    def test_copy_there_is_a_normal_review(self):
        self.ran()
        self.copy()
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["code"], "review")
        self.assertIn("accept", phase["actions"])

    def test_retry_after_failure_is_not_offered_when_copy_is_gone(self):
        cdir = self.ran()
        record(cdir, "合并失败", src="程序", dst="你", summary="改好的版本不在了，没法放进项目")
        self.assertEqual(card_phase(self.project, "CARD-001")["actions"], ["discard"])


class Server(ServerCase):
    def test_accept_is_refused(self):
        cdir = self.make_card(state="待验收", plan=True)
        record(cdir, "开跑", src="程序", dst="程序", summary="开工", pid=1, started="x")
        status, _ = self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": "accept"})
        self.assertEqual(status, 409)
