"""不装 AstrBot 也能跑：用 tests/stub 里的替身把插件的指令链路跑一遍。

只调用只读接口 + 需要二次确认的删除「预演」，不会真的改任何数据。

    python tests/test_plugin_local.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "stub"))  # 假的 astrbot 包
sys.path.insert(0, str(HERE.parent))  # 真插件

import main as plugin_main  # noqa: E402
from astrbot.api.event import AstrMessageEvent, ProviderResponse  # noqa: E402
from astrbot.api.star import Context  # noqa: E402


class FakeProvider:
    """假的大模型 provider：固定返回一段内容，用来验证 LLM 摘要链路。"""

    async def text_chat(self, prompt=None, system_prompt=None, **kwargs):
        assert prompt and "价格统计" in prompt, "送给模型的 Prompt 结构不对"
        assert system_prompt, "缺少 system_prompt"
        return ProviderResponse(
            completion_text="**switch 行情**：中位价偏高，150~330 区间的卡带性价比最好。\n"
            "如果想挂单，建议贴着 ¥900 上下，别跟最低价硬刚。"
        )


def build_plugin(enable_llm: bool = True):
    context = Context(provider=FakeProvider() if enable_llm else None)
    config = {
        "base_url": "http://127.0.0.1:8080",
        "admin_username": "admin",
        "admin_password": "admin123",
        "admin_qq": ["10001"],
        "enable_llm_summary": enable_llm,
        "search_cooldown": 0,
        "search_top_n": 3,
        "summary_timeout": 20,
    }
    return plugin_main.XianyuAutoDeliverPlugin(context, config), context


async def call(handler, event: AstrMessageEvent) -> list[str]:
    texts: list[str] = []
    async for result in handler(event):
        chain = getattr(result, "chain", []) or []
        for part in chain:
            if isinstance(part, dict):
                texts.append(part.get("text") or f"[{part.get('type')}] {part.get('path', '')}")
            else:
                texts.append(str(part))
    return texts


def show(title: str, texts: list[str]) -> None:
    print(f"\n{'=' * 12} {title} {'=' * 12}")
    for text in texts:
        print(text)


async def main() -> int:
    plugin, context = build_plugin()
    private = "onebot:private_10001"

    show("/闲鱼", await call(plugin.cmd_help, AstrMessageEvent("闲鱼", umo=private)))

    show(
        "/闲鱼登录（用配置里的账号密码）",
        await call(plugin.cmd_login, AstrMessageEvent("闲鱼登录", umo=private)),
    )

    show("/闲鱼状态", await call(plugin.cmd_status, AstrMessageEvent("闲鱼状态", umo=private)))
    show("/闲鱼账号", await call(plugin.cmd_accounts, AstrMessageEvent("闲鱼账号", umo=private)))
    show("/闲鱼商品 1", await call(plugin.cmd_items, AstrMessageEvent("闲鱼商品 1", umo=private)))
    show("/闲鱼订单", await call(plugin.cmd_orders, AstrMessageEvent("闲鱼订单", umo=private)))

    show(
        "/闲鱼推送 绑定（当前私聊会话）",
        await call(plugin.cmd_push, AstrMessageEvent("闲鱼推送 绑定", umo=private)),
    )

    show(
        "/闲鱼搜索 switch（走大模型，来自群会话 → 摘要应推回私聊）",
        await call(
            plugin.cmd_search,
            AstrMessageEvent("闲鱼搜索 switch", umo="onebot:group_555", private=False),
        ),
    )
    show(
        "/闲鱼搜索 switch --max 300 --top 2（带筛选）",
        await call(
            plugin.cmd_search,
            AstrMessageEvent("闲鱼搜索 switch --max 300 --top 2", umo=private),
        ),
    )

    # 触发降级路径：没有 provider 时应该走模板摘要
    plugin_no_llm, _ = build_plugin(enable_llm=False)
    await call(plugin_no_llm.cmd_login, AstrMessageEvent("闲鱼登录", umo=private))
    show(
        "/闲鱼搜索 switch（无大模型 → 模板摘要）",
        await call(plugin_no_llm.cmd_search, AstrMessageEvent("闲鱼搜索 switch", umo=private)),
    )

    show(
        "/闲鱼删除 1（只预演，不确认）",
        await call(plugin.cmd_delete, AstrMessageEvent("闲鱼删除 1", umo=private)),
    )

    show(
        "群聊 + 非白名单用户 → 应被拒绝",
        await call(
            plugin.cmd_accounts,
            AstrMessageEvent("闲鱼账号", sender_id="99999", umo="onebot:group_555", private=False),
        ),
    )

    show(
        "群聊里发 Cookie → 应被拒绝",
        await call(
            plugin.cmd_cookie,
            AstrMessageEvent(
                "闲鱼cookie abcdefghijklmnopqrstuvwxyz",
                umo="onebot:group_555",
                private=False,
            ),
        ),
    )

    show(
        "参数缺失 → 用法提示",
        await call(plugin.cmd_search, AstrMessageEvent("闲鱼搜索", umo=private)),
    )

    print(f"\n主动推送记录：{len(context.pushed)} 条")
    for umo, text in context.pushed:
        head = text.splitlines()[0] if text else ""
        print(f"  → {umo}: {head}")

    await plugin.terminate()
    await plugin_no_llm.terminate()
    assert context.pushed, "跨会话推送没有生效"
    print("✅ 插件本地链路自测完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
