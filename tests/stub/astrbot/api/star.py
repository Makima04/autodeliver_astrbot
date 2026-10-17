from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

_TMP_ROOT = Path("/tmp/astrbot_stub_data")


class Star:
    def __init__(self, context: Any = None) -> None:
        self.context = context

    def get_tools(self):
        return StarTools


class StarTools:
    @staticmethod
    def get_data_dir(name: Optional[str] = None) -> Path:
        path = _TMP_ROOT / (name or "unknown")
        path.mkdir(parents=True, exist_ok=True)
        return path


class Context:
    """替身 Context：记录主动推送的消息。"""

    def __init__(self, provider: Any = None) -> None:
        self.provider = provider
        self.pushed: list[tuple[str, str]] = []

    async def get_using_provider_async(self, umo: str | None = None):
        return self.provider

    async def send_message(self, umo: str, chain: Any) -> None:
        text = "".join(getattr(chain, "parts", []) or [])
        self.pushed.append((umo, text))


__all__ = ["Star", "StarTools", "Context"]
