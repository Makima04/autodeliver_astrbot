"""极简 AstrBot 运行时替身，只用于在没装 AstrBot 的机器上跑 plugins 的自测。

只实现插件实际用到的那一小撮 API：@filter.command、Star、StarTools、
AstrMessageEvent 的 plain_result / chain_result / get_sender_*。
"""

from __future__ import annotations

from . import api  # noqa: F401
