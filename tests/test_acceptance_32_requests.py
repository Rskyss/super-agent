"""32 号单：按服务商发请求；模型在思考时不算卡住。用假模型，不花额度。"""
from acceptance_helpers import ServerCase
from desktop.clerk.client import ModelClient
from fake_model_server import FakeModel


class RequestTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeModel(slow_seconds=1)
        self.addCleanup(self.fake.close)

    def send(self, **kw):
        ModelClient(self.fake.base, "m", "good-key", timeout=5, **kw).chat([{"role": "user", "content": "你好"}])
        return self.fake.requests[-1]

    def test_thinking_stays_off_by_default_exactly_as_today(self):
        self.assertEqual(self.send()["thinking"], {"type": "disabled"})

    def test_other_providers_get_no_extra_field(self):
        self.assertNotIn("thinking", self.send(thinking_off=False))

    def test_a_strict_service_works_for_other_providers(self):
        self.fake.server.strict = True
        answer = ModelClient(self.fake.base, "m", "good-key", timeout=5, thinking_off=False).chat([{"role": "user", "content": "你好"}])
        self.assertIn("你好", answer["text"])

    def test_thinking_is_progress_and_never_shown(self):
        pieces = []
        answer = ModelClient(self.fake.base, "m", "good-key", timeout=0.4, thinking_off=False).chat(
            [{"role": "user", "content": "思考"}], on_text=pieces.append)
        self.assertEqual(answer["text"], "想好了")
        self.assertEqual("".join(pieces), "想好了")

    def test_service_picks_the_writing_by_provider(self):
        for provider, expected in (("tencent", True), ("bailian", True), ("other", False)):
            form = {"project": self.project, "provider": provider, "base_url": self.fake.base, "model": "m", "api_key": "good-key"}
            self.assertEqual(self.post("/api/clerk/test", form), (200, {"ok": True}))
            self.assertEqual("thinking" in self.fake.requests[-1], expected, provider)

    def test_old_form_without_provider_behaves_as_today(self):
        form = {"base_url": self.fake.base, "model": "m", "api_key": "good-key"}
        self.assertEqual(self.post("/api/clerk/test", form), (200, {"ok": True}))
        self.assertIn("thinking", self.fake.requests[-1])
