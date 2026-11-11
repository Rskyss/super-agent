"""行政人员可用的服务商清单。加一家只改这里，桌面窗口和本机服务都读这一份。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

TENCENT_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
TENCENT_MODEL = "deepseek/deepseek-flash"
BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": "tencent",
        "name": "腾讯云",
        "base_url": TENCENT_BASE_URL,
        "models": [TENCENT_MODEL],
        "own": ["hunyuan", "hy"],  # 自家模型的名字开头：检测时先试这些（代卖的 DeepSeek 等常要单独开通）
        "thinking_off": True,
    },
    {
        "id": "bailian",
        "name": "阿里百炼",
        "base_url": BAILIAN_BASE_URL,
        "models": ["qwen-flash", "qwen-plus", "deepseek-v4.1-flash", "deepseek-v4-pro"],
        "own": ["qwen", "qwq"],
        "thinking_off": True,
    },
    {
        "id": "other",
        "name": "其他",
        "base_url": "",
        "models": [],
        "thinking_off": False,
    },
]


def find(provider_id: str) -> Optional[Dict[str, Any]]:
    for item in PROVIDERS:
        if item["id"] == provider_id:
            return item
    return None


def account_for(provider: str, base_url: str) -> str:
    if find(provider) is None:
        raise ValueError("没有这家服务商")
    if provider == "tencent":
        return "tencent"
    if provider == "bailian":
        return "bailian"
    parsed = urlparse((base_url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError("地址不对")
    return "other:" + host
