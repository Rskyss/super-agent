"""转出协作包里的服务商清单，桌面窗口和本机服务都从这里取。"""
from collab.providers import PROVIDERS, account_for, find

__all__ = ["PROVIDERS", "account_for", "find"]
