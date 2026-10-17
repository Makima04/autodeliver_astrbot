"""斜杠守卫的离线测试。

背景：wake_prefix 里加了 ``/`` 之后，``/随便什么`` 会唤醒大模型并让模型乱回。
guard_unmatched_slash 负责把「没有任何指令认领的斜杠消息」拦下来。

AstrBot 的唤醒阶段会把开头的 ``/`` 从 event.message_str 里剥掉，
所以守卫必须看 event.message_obj.message_str，这里用 raw= 模拟。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "stub"))
sys.path.insert(0, str(HERE.parent))

from astrbot.api.event import AstrMessageEvent  # noqa: E402

import main as plugin_main  # noqa: E402


async def collect(agen) -> list:
    items = []
    async for item in agen:
        items.append(item)
    return items


def build_plugin():
    context = plugin_main.Context()
    return plugin_main.XianyuAutoDeliverPlugin(context, {"admin_qq": ["10001"]})


async def main() -> int:
    plugin = build_plugin()
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'✅' if cond else '❌'} {name}{(' — ' + detail) if detail and not cond else ''}")
        if not cond:
            failures.append(name)

    print("---- 未知斜杠指令：应被拦截且不落到大模型 ----")
    ev = AstrMessageEvent("随便说说", raw="/随便说说")
    out = await collect(plugin.guard_unmatched_slash(ev))
    check("被 stop_event", ev.is_stopped())
    check("不产生回复（静默）", out == [], f"实际 {out}")
    check("禁止默认 LLM（call_llm=True）", ev.call_llm is True)

    print("\n---- 未知闲鱼子指令：拦截并给提示 ----")
    ev = AstrMessageEvent("闲鱼乱写", raw="/闲鱼乱写")
    out = await collect(plugin.guard_unmatched_slash(ev))
    text = out[0].chain[0]["text"] if out else ""
    check("被 stop_event", ev.is_stopped())
    check("有「没有这个指令」提示", "没有" in text and "闲鱼" in text, text)

    print("\n---- 只发一个斜杠 ----")
    ev = AstrMessageEvent("", raw="/")
    out = await collect(plugin.guard_unmatched_slash(ev))
    check("被 stop_event", ev.is_stopped())
    check("提示 /闲鱼", bool(out) and "/闲鱼" in out[0].chain[0]["text"])

    print("\n---- 正常指令：必须放行 ----")
    for raw, msg, cmd in [
        ("/闲鱼", "闲鱼", "闲鱼"),
        ("/闲鱼扫码", "闲鱼扫码", "闲鱼扫码"),
        ("/闲鱼搜索 switch", "闲鱼搜索 switch", "闲鱼搜索"),
        ("/help", "help", "help"),
    ]:
        ev = AstrMessageEvent(msg, raw=raw, activated_commands=[cmd])
        out = await collect(plugin.guard_unmatched_slash(ev))
        check(f"{raw} 放行", (not ev.is_stopped()) and out == [], f"stopped={ev.is_stopped()}")

    print("\n---- 非斜杠消息不受影响 ----")
    ev = AstrMessageEvent("今天天气不错", raw="今天天气不错")
    out = await collect(plugin.guard_unmatched_slash(ev))
    check("不拦截", (not ev.is_stopped()) and out == [])

    print("\n---- 读不到 activated_handlers（异常）时宁可放行 ----")
    ev = AstrMessageEvent("闲鱼扫码", raw="/闲鱼扫码")

    def _boom(key, default=None):
        raise RuntimeError("旧版本没有这个 extra")

    ev.get_extra = _boom
    out = await collect(plugin.guard_unmatched_slash(ev))
    check("放行（fail-open）", (not ev.is_stopped()) and out == [])

    print("\n---- 过滤器本身：只对 / 开头的事件生效 ----")
    guard_filter = plugin_main.SlashCommandOnly(True)
    check(
        "/随便 通过过滤器",
        guard_filter.filter(AstrMessageEvent("随便", raw="/随便"), {}) is True,
    )
    check(
        "普通私聊不通过过滤器",
        guard_filter.filter(AstrMessageEvent("今天天气", raw="今天天气"), {}) is False,
    )
    check(
        "群里闲聊不通过过滤器（否则会误唤醒）",
        guard_filter.filter(AstrMessageEvent("三金", raw="三金", private=False), {}) is False,
    )

    await plugin.terminate()
    print()
    if failures:
        print(f"❌ 失败 {len(failures)} 项: {failures}")
        return 1
    print("✅ 斜杠守卫测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
