"""千问那条路被临时限流（429 请求太多）时多重试几次，不让整轮报错停下（11-19 C2 两次被限流，额度其实还在）。"""
import unittest

from collab.agents import qwen_command


class QwenRetry(unittest.TestCase):
    def test_request_retries_raised_for_rate_limits(self):
        cmd = " ".join(qwen_command("写代码", "deepseek-v4.1-flash"))
        self.assertIn("model_providers.qwtp.request_max_retries=8", cmd)
        self.assertIn("model_providers.qwtp.stream_max_retries=3", cmd, "断流重发的设置不变")


if __name__ == "__main__":
    unittest.main()
