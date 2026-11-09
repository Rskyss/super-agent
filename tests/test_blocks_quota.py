"""第 9 项整体验收（2026-11-09）用户定：检测时只是额度不足，也算接上了，可以放进基础层，状态标黄“额度不足”。

基础层只管工具接进来、能跑起来；用哪个模型、额度不够时换哪个，是下一阶段能力层的事。
真实情况：agy 装好、登录、能连上服务，只是接法卡用的 Gemini 4 Argon 这一档周额度用完了。
"""
import shutil
import subprocess
import unittest
from pathlib import Path

from collab import blocks
from desktop.clerk.client import ModelError
from test_acceptance_55_blocks_server import KEY, BlocksCase

HERE = Path(__file__).resolve().parent


class _QuotaClient:
    def chat(self, messages, on_text=None, tools=None):
        raise ModelError("模型额度用完了，或请求太频繁。请稍后再试，或到服务商那里检查余额。")


class QuotaCountsAsConnected(BlocksCase):
    def test_cli_quota_can_be_saved(self):
        self.tool(body='echo "Error: Your AI credits balance is too low to continue." >&2\nexit 1\n')
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted()})
        _, data = self.confirm(cid, pid)
        result = self.job(data["job"])["result"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "quota")
        self.assertIn("额度", result["reason"])
        status, saved = self.bpost("/api/blocks/save", {"job": data["job"]})
        self.assertEqual(status, 200, saved)
        listed = self.bget("/api/blocks/list")[1]["blocks"]
        self.assertEqual(listed[0]["status"], "quota", "基础层里标“额度不足”")

    def test_other_failures_still_blocked(self):
        """只认很明确的“额度/余额/限流”说法；参数不够、权限不够、磁盘配额、行号 429、上下文太长都不能放进去（第四轮复查）"""
        for i, text in enumerate(("Error: something else broke", "Error: insufficient arguments",
                                  "insufficient permissions to write file", "Disk quota exceeded",
                                  "error at line 429: unexpected token", "RESOURCE_EXHAUSTED: context length exceeded")):
            with self.subTest(text=text):
                name = f"tool{i}"
                self.tool(name, body=f'echo "{text}" >&2\nexit 1\n')
                cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted(name, command=name)})
                _, data = self.confirm(cid, pid)
                self.assertEqual(self.job(data["job"])["result"]["status"], "fail")
                self.assertEqual(self.bpost("/api/blocks/save", {"job": data["job"]})[0], 409)

    def test_clear_quota_wordings(self):
        from collab import blocks_check
        for text in ("Error: Your AI credits balance is too low to continue.", "You have run out of credits",
                     "Error: quota exceeded for this month", "You exceeded your current quota",
                     "usage limit reached, try again later", "HTTP 429 Too Many Requests", "rate limited, retry later",
                     "RESOURCE_EXHAUSTED (code 429): Resource has been exhausted (e.g. check quota)."):
            with self.subTest(text=text):
                self.assertTrue(blocks_check.is_quota(text))
        for text in ("Error: insufficient arguments", "Disk quota exceeded", "line 429",
                     "usage: tool [--rate-limit N] [--usage-limit N] [--quota N]",
                     "RESOURCE_EXHAUSTED: context length exceeded", ""):
            with self.subTest(text=text):
                self.assertFalse(blocks_check.is_quota(text))

    def test_quota_job_kept_when_jobs_pile_up(self):
        """额度不足、还没放进去的任务和“检测通过”的一样留着，等用户点"""
        server_src = (HERE.parent / "desktop" / "server.py").read_text(encoding="utf-8")
        self.assertIn('_blocks_connected((v["result"] or {}))', server_src)

    def test_clerk_sees_quota(self):
        from desktop.clerk import blocks_tools
        blocks.save_card(self.api_card())
        blocks.add_record("tongyi", {"type": "check", "ok": False, "status": "quota", "reason": "额度用完或被限流"})
        text = blocks_tools.run(str(self.home), "list_blocks", {})
        self.assertIn("额度不足（能连上）", text)
        self.assertNotIn("不能用", text)

    def test_api_quota_can_be_saved(self):
        self.server.blocks_client_factory = lambda base_url, model, api_key: _QuotaClient()
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian", "model": "qwen-plus", "name": "通义"})
        _, data = self.confirm(cid, pid, api_key=KEY)
        result = self.job(data["job"])["result"]
        self.assertEqual(result["status"], "quota")
        self.assertEqual(self.bpost("/api/blocks/save", {"job": data["job"]})[0], 200)

    def test_bind_quota_still_fails(self):
        """绑定行政要真能说话：额度不足的不能当行政"""
        self.server.blocks_client_factory = lambda base_url, model, api_key: _QuotaClient()
        _, data = self.bind()
        self.assertFalse(data["ok"])
        self.assertIsNone(self.bget("/api/blocks/state")[1]["setting"])

    def test_detail_check_on_saved_card_records_quota(self):
        blocks.save_card(self.api_card())
        self.server.blocks_client_factory = lambda base_url, model, api_key: _QuotaClient()
        _, data = self.bpost("/api/blocks/check", {"id": "tongyi", "api_key": KEY})
        self.assertEqual(self.job(data["job"])["result"]["status"], "quota")
        self.assertEqual(self.bget("/api/blocks/list")[1]["blocks"][0]["status"], "quota")


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class PageShowsQuota(unittest.TestCase):
    def test_rules(self):
        script = r"""
var assert = require("assert");
var B = require(process.argv[1] + "/desktop/static/blocks.js");
var C = require(process.argv[1] + "/desktop/static/chat.js");
assert.strictEqual(B.statusText({ status: "quota" }), "额度不足");
assert.strictEqual(B.statusClass({ status: "quota" }), "warn");
var d = B.detailView({ card: { id: "agy", name: "agy", kind: "cli" }, verified: true,
  last_check: { ok: false, status: "quota", reason: "额度用完或被限流", how: "等额度恢复后再检测一次", time: "2026-11-09T19:12:22", seconds: 8.2 } }, null);
assert.strictEqual(d.status.cls, "warn");
assert.ok(d.status.text.indexOf("额度不足") === 0, d.status.text);
var v = B.checkView({ done: true, result: { ok: false, status: "quota", reason: "额度用完或被限流", how: "等额度恢复后再检测一次" } });
assert.strictEqual(v.state, "warn");
assert.ok(v.text.indexOf("能连上") >= 0, v.text);
var j = C.jobView({ kind: "check", done: true, result: { ok: false, status: "quota", reason: "额度用完或被限流", how: "等额度恢复后再检测一次", seconds: 8.2 } });
assert.strictEqual(j.state, "warn");
assert.strictEqual(j.canSave, true, "额度不足也能放进基础层");
var j2 = C.jobView({ kind: "check", done: true, saved: true, result: { ok: false, status: "quota", reason: "额度用完或被限流" } });
assert.strictEqual(j2.canSave, false);
assert.ok(j2.text.indexOf("已放进基础层") >= 0);
var f = C.jobView({ kind: "check", done: true, result: { ok: false, status: "fail", reason: "没登录" } });
assert.strictEqual(f.canSave, false, "别的失败照旧不能放");
"""
        proc = subprocess.run(["node", "-e", script, str(HERE.parent)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_css(self):
        static = HERE.parent / "desktop" / "static"
        blocks_css = (static / "blocks.css").read_text(encoding="utf-8")
        for rule in (".dot.warn", ".detail-status.warn", ".detail-result.warn"):
            self.assertIn(rule, blocks_css)
        self.assertIn(".prop-result.warn", (static / "chat.css").read_text(encoding="utf-8"))
        chat = (static / "chat.js").read_text(encoding="utf-8")
        page = (static / "blocks.js").read_text(encoding="utf-8")
        self.assertIn('rec.view.state !== "ok" && rec.view.state !== "warn") delete proposalKeys[pid]', chat,
                      "额度不足时手填的密钥要留着，放进基础层时交给 Mac 窗口存")
        self.assertIn('rec.view.state === "fail" && usedSavedKey[pid]', chat, "额度不足不劝换一把")
        self.assertIn('v.state === "fail" && usedSaved', page, "额度不足不劝换一把")
        self.assertIn('(v.state === "ok" || v.state === "warn") && currentDetail', page, "额度不足时新填的密钥也存")
        self.assertIn('replace("。可以先放进基础层", "")', chat, "放进去后不再叠着“可以先放进”")


if __name__ == "__main__":
    unittest.main()
