from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class CustomFilter:
    """替身：AstrBot 的自定义过滤器基类。"""

    def __init__(self, raise_error: bool = True, **kwargs) -> None:
        self.raise_error = raise_error

    def filter(self, event, cfg) -> bool:  # pragma: no cover - 由子类实现
        raise NotImplementedError


class _Filter:
    """替身：装饰器全部原样返回函数，不做真实的事件过滤。"""

    CustomFilter = CustomFilter

    def command(self, name: str, alias: set[str] | None = None, priority: int = 0):
        def deco(func: Callable):
            func._command = name
            func._alias = alias or set()
            return func

        return deco

    def custom_filter(self, custom_type_filter, *args, **kwargs):
        def deco(func: Callable):
            func._custom_filter = custom_type_filter
            func._custom_filter_kwargs = kwargs
            return func

        return deco

    def command_group(self, name: str):
        def deco(func: Callable):
            func._command_group = name
            return func

        return deco

    def regex(self, pattern: str, **kwargs):
        def deco(func: Callable):
            func._regex = pattern
            func._regex_kwargs = kwargs
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


class _StubHandler:
    """替身 handler：只为模拟 event.activated_handlers 里的指令名。"""

    def __init__(self, command_name: str) -> None:
        self.handler_name = f"cmd_{command_name}"
        self.event_filters = [_StubCommandFilter(command_name)]


class _StubCommandFilter:
    def __init__(self, command_name: str) -> None:
        self.command_name = command_name

    def get_complete_command_names(self) -> list[str]:
        return [self.command_name]


class AstrMessageEvent:
    """替身消息事件。"""

    def __init__(
        self,
        message_str: str = "",
        sender_id: str = "10001",
        sender_name: str = "测试用户",
        umo: str = "onebot:private_10001",
        private: bool = True,
        raw: str | None = None,
        activated_commands: list[str] | None = None,
    ) -> None:
        self.message_str = message_str
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.unified_msg_origin = umo
        self._private = private
        self.sent: list[list[Any]] = []
        self._result: _Result | None = None
        self._stopped = False
        self.call_llm = False
        self._extra: dict[str, Any] = {}
        # message_obj.message_str 保存唤醒前缀被剥掉之前的原文（真实 AstrBot 行为）
        self.message_obj = type("_MsgObj", (), {"message_str": raw if raw is not None else message_str})()
        if activated_commands is not None:
            self._extra["activated_handlers"] = [_StubHandler(name) for name in activated_commands]

    # 管道相关
    def get_extra(self, key: str, default: Any = None) -> Any:
        return self._extra.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        self._extra[key] = value

    def stop_event(self) -> None:
        self._stopped = True

    def should_call_llm(self, call_llm: bool) -> None:
        self.call_llm = call_llm

    def is_stopped(self) -> bool:
        return self._stopped

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
