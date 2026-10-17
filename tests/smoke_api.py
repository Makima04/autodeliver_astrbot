"""脱离 AstrBot 的端到端冒烟测试：直接打本地 AutoDeliver 后端。

用法：
    python tests/smoke_api.py                 # 默认搜 switch
    python tests/smoke_api.py 塞尔达
    AD_BASE=http://127.0.0.1:8080 AD_USER=admin AD_PASS=admin123 python tests/smoke_api.py

校验内容：管理员登录 → 账号列表 → 订单统计 → 关键词搜索 → 格式化输出，
不发消息、不落盘 token（除内存外），不会修改任何业务数据。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import ApiError, AutoDeliverClient  # noqa: E402
from summary import (  # noqa: E402
    build_fallback_summary,
    build_item_lines,
    build_llm_prompt,
    format_accounts,
    format_items,
    format_status,
    parse_search_args,
)


async def main() -> int:
    keyword, opts = parse_search_args(" ".join(sys.argv[1:]) or "switch")
    client = AutoDeliverClient(
        base_url=os.getenv("AD_BASE", "http://127.0.0.1:8080"),
        username=os.getenv("AD_USER", "admin"),
        password=os.getenv("AD_PASS", "admin123"),
    )
    try:
        print("=== 1. 管理员登录 ===")
        login = await client.login()
        print(f"OK 用户={login.get('username')} 角色={login.get('role')} 过期={login.get('expires_at')}")

        print("\n=== 2. 闲鱼账号列表 ===")
        accounts = await client.list_accounts()
        print(format_accounts(accounts))

        print("\n=== 3. 订单统计 ===")
        stats = await client.order_stats()
        print(format_status(accounts, stats, True, client.base_url))

        if accounts:
            first_id = accounts[0]["id"]
            print(f"\n=== 4. 账号 #{first_id} 商品 ===")
            items = await client.list_items(first_id)
            print(format_items(items, first_id))

        print(f"\n=== 5. 搜索「{keyword}」 ===")
        data = await client.search(
            keyword,
            min_price_yuan=opts.get("min_price_yuan"),
            max_price_yuan=opts.get("max_price_yuan"),
            sort_by=opts.get("sort_by"),
        )
        market_stats = data.get("stats") or {}
        market_items = data.get("items") or []
        print(f"数据源：{data.get('data_source')}")
        print(f"样本 {len(market_items)} 条，中位价 {market_stats.get('median_price_yuan')} 元")
        print("\n--- 模板摘要（LLM 不可用时的兜底）---")
        print(build_fallback_summary(keyword, market_stats, market_items))
        print("\n--- 明细 ---")
        print(build_item_lines(market_items, int(opts.get("top_n") or 5)))
        print("\n--- 送给大模型的 Prompt 预览（前 800 字）---")
        print(build_llm_prompt(keyword, market_stats, market_items, opts)[:800])
        print("\n✅ 冒烟测试全部通过")
        return 0
    except ApiError as exc:
        print(f"\n❌ 接口错误：{exc}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
