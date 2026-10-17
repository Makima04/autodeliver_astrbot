from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class _Filter:
    """替身：装饰器全部原样返回函数，不做真实的事件过滤。"""

    def command(self, name: str, alias: set[str] | None = None, priority: int = 0):
        def deco(func: Callable):
            func._command = name
            func._alias = alias or set()
            return func

        return deco

    def command_group(self, name: str):
        def deco(func: Callable):
            func._command_group = name
            return func

        return deco

    def event_message_type(self, *_args, **_kwargs):
        return lambda func: func

    def platform_adapter_type(self, *_args, **_kwargs):
        return lambda func: func

    def permission_type(self, *_args, **_kwargs):
        return lambda func: func

    def on_astrbot_loaded(self):
        return lambda func: func


filter = _Filter()


@dataclass
class _Result:
    chain: list[Any]


class AstrMessageEvent:
    """替身消息事件。"""

    def __init__(
        self,
        message_str: str = "",
        sender_id: str = "10001",
        sender_name: str = "测试用户",
        umo: str = "onebot:private_10001",
        private: bool = True,
    ) -> None:
        self.message_str = message_str
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.unified_msg_origin = umo
        self._private = private
        self.sent: list[list[Any]] = []
        self._result: _Result | None = None

    # 插件用到的取值方法
    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def is_private_chat(self) -> bool:
        return self._private

    # 构造发送内容
    def plain_result(self, text: str):
        self._result = _Result(chain=[{"type": "plain", "text": text}])
        return self._result

    def chain_result(self, chain: list[Any]):
        self._result = _Result(chain=list(chain))
        return self._result

    def image_result(self, path: str):
        self._result = _Result(chain=[{"type": "image", "path": path}])
        return self._result


class MessageChain:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def message(self, text: str) -> MessageChain:
        self.parts.append(text)
        return self

    def __repr__(self) -> str:  # pragma: no cover
        return f"MessageChain({self.parts!r})"


@dataclass
class ProviderResponse:
    completion_text: str = ""
    role: str = "assistant"


__all__ = ["filter", "AstrMessageEvent", "MessageChain", "ProviderResponse", "field"]
