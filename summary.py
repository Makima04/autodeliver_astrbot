"""搜索参数解析与结果摘要（纯函数，便于单测，不依赖 AstrBot）。"""

from __future__ import annotations

import re
from typing import Any, Optional

# --------------------------------------------------------------------- 参数解析

# 允许的排序字段（后端 sort_by 直接透传给闲鱼 MTOP 的 sortField）
SORT_ALIASES = {
    "默认": "value_desc",
    "综合": "value_desc",
    "性价比": "value_desc",
    "价格升序": "price_asc",
    "价格降序": "price_desc",
}

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _to_float(value: str) -> Optional[float]:
    value = value.strip().replace("¥", "").replace("元", "")
    return float(value) if _NUM_RE.match(value) else None


def parse_search_args(raw: str) -> tuple[str, dict]:
    """解析 ``/闲鱼搜索 switch --min 100 --max 500 --sort 价格升序 --account 1``。

    返回 (关键词, 选项字典)。关键词支持空格（未以 ``--`` 开头的连续词会拼进关键词），
    因此 ``/闲鱼搜索 塞尔达 王国之泪`` 也能正常工作。
    """
    opts: dict[str, Any] = {}
    if not raw:
        return "", opts

    tokens = raw.split()
    keyword_parts: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("--min", "-min", "--max", "-max", "--account", "-a", "--sort", "-s", "--top", "-n"):
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 2
            if token in ("--min", "-min"):
                number = _to_float(value)
                if number is not None:
                    opts["min_price_yuan"] = number
            elif token in ("--max", "-max"):
                number = _to_float(value)
                if number is not None:
                    opts["max_price_yuan"] = number
            elif token in ("--account", "-a"):
                if value.isdigit():
                    opts["account_id"] = int(value)
            elif token in ("--sort", "-s"):
                opts["sort_by"] = SORT_ALIASES.get(value, value or None)
            else:  # --top
                if value.isdigit():
                    opts["top_n"] = int(value)
            continue

        # 支持 --min=100 这种等号写法
        if token.startswith("--") and "=" in token:
            key, _, value = token.partition("=")
            i += 1
            if key == "--min":
                number = _to_float(value)
                if number is not None:
                    opts["min_price_yuan"] = number
            elif key == "--max":
                number = _to_float(value)
                if number is not None:
                    opts["max_price_yuan"] = number
            elif key == "--account" and value.isdigit():
                opts["account_id"] = int(value)
            elif key == "--top" and value.isdigit():
                opts["top_n"] = int(value)
            elif key == "--sort":
                opts["sort_by"] = SORT_ALIASES.get(value, value or None)
            continue

        keyword_parts.append(token)
        i += 1

    return " ".join(keyword_parts).strip(), opts


def parse_price_range(raw: str) -> tuple[Optional[float], Optional[float]]:
    """支持 ``100-500`` / ``100~500`` 的简写价格区间。"""
    if not raw:
        return None, None
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)\s*$", raw)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


# --------------------------------------------------------------------- 格式化


def money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number >= 10000:
        return f"¥{number:,.0f}"
    if number == int(number):
        return f"¥{int(number)}"
    return f"¥{number:.2f}"


def _clip(text: Any, limit: int = 26) -> str:
    text = str(text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_accounts(accounts: list) -> str:
    """把 GET /api/xianyu/accounts 的结果渲染成 QQ 友好的列表。"""
    if not accounts:
        return "📭 还没有绑定的闲鱼账号。\n发送「/闲鱼扫码」用闲鱼 App 扫码登录一个账号。"

    lines = [f"👥 已绑定 {len(accounts)} 个闲鱼账号："]
    for acc in accounts:
        status_icon = {"verified": "✅", "connected": "✅", "expired": "⚠️", "invalid": "❌"}.get(
            str(acc.get("status") or ""), "❔"
        )
        nick = acc.get("seller_nick") or "（未解析昵称）"
        lines.append(
            f"\n{status_icon} #{acc.get('id')} {nick}\n"
            f"    模式：{acc.get('auth_mode')}｜状态：{acc.get('status')}\n"
            f"    seller_id：{acc.get('seller_id') or '—'}\n"
            f"    最近校验：{acc.get('last_verified_at') or '—'}"
        )
    lines.append("\n查看商品：/闲鱼商品 <账号ID>｜校验：/闲鱼校验 <账号ID>｜删除：/闲鱼删除 <账号ID>")
    return "\n".join(lines)


def format_status(accounts: list, stats: Optional[dict], token_ok: bool, base_url: str) -> str:
    lines = [f"🩺 AutoDeliver 状态（{base_url}）", f"管理端登录：{'✅ 已登录' if token_ok else '❌ 未登录'}"]
    if accounts:
        verified = sum(1 for a in accounts if str(a.get("status")) == "verified")
        lines.append(f"闲鱼账号：{len(accounts)} 个（正常 {verified} 个）")
        for acc in accounts[:5]:
            lines.append(
                f"  · #{acc.get('id')} {acc.get('seller_nick') or '未命名'}｜{acc.get('status')}"
            )
    else:
        lines.append("闲鱼账号：未绑定")
    if stats:
        lines.append(
            f"订单：累计 {stats.get('total', 0)}｜今日 {stats.get('today', 0)}｜"
            f"待发货 {stats.get('pending', 0)}｜已发货 {stats.get('delivered', 0)}｜失败 {stats.get('failed', 0)}"
        )
    return "\n".join(lines)


def format_scan_result(result: dict) -> str:
    if not result:
        return "⚠️ 巡检接口没有返回内容"
    fulfilled = result.get("fulfilled_orders") or []
    lines = [
        "🚚 发货巡检完成",
        f"扫描账号：{result.get('scanned_accounts', 0)} 个｜会话：{result.get('total_sessions_scanned', 0)}",
        f"发现待发货订单：{result.get('detected_orders_count', 0)} 笔｜本次成功发货：{len(fulfilled)} 笔",
    ]
    for order in fulfilled[:5]:
        lines.append(
            f"  · {order.get('platform_order_id')} → {order.get('status') or order.get('message') or '已处理'}"
        )
    if result.get("message"):
        lines.append(str(result["message"]))
    return "\n".join(lines)


ITEM_STATUS_TEXT = {"on_sale": "在售", "off_shelf": "已下架"}


def format_items(items: list, account_id: int) -> str:
    if not items:
        return f"📭 账号 #{account_id} 本地还没有商品记录，可发送「/闲鱼同步 {account_id}」从闲鱼拉取。"
    lines = [f"🛒 账号 #{account_id} 的闲鱼商品（{len(items)} 个）："]
    for item in items[:15]:
        # 注意：后端 XianyuItem.price 单位是「分」
        price_yuan = float(item.get("price") or 0) / 100
        status = ITEM_STATUS_TEXT.get(str(item.get("status") or ""), str(item.get("status") or ""))
        bound = "｜已绑卡密" if item.get("local_product_id") else ""
        lines.append(
            f"  · {_clip(item.get('title'), 24)}｜{money(price_yuan)}｜{status}{bound}"
        )
    if len(items) > 15:
        lines.append(f"  … 其余 {len(items) - 15} 个已省略")
    return "\n".join(lines)


# --------------------------------------------------------------------- 搜索摘要


def _filter_note(opts: dict) -> str:
    notes = []
    if opts.get("min_price_yuan") is not None:
        notes.append(f"≥{money(opts['min_price_yuan'])}")
    if opts.get("max_price_yuan") is not None:
        notes.append(f"≤{money(opts['max_price_yuan'])}")
    return f"（筛选：{'、'.join(notes)}）" if notes else ""


def build_item_lines(items: list, top_n: int = 5) -> str:
    """搜索结果明细（发给 QQ 的第二条消息）。"""
    if not items:
        return "没有搜到符合条件的商品，可以换个关键词或放宽价格区间。"

    lines = [f"📋 价格最优 / 性价比最高的 {min(top_n, len(items))} 个："]

    # 最便宜的几个（排除明显异常的低价：低于中位价 1% 时仍展示，但标记出来）
    cheapest = sorted(items, key=lambda x: float(x.get("price_yuan") or 0))[:top_n]
    for index, item in enumerate(cheapest, 1):
        lines.append(
            f"{index}. {_clip(item.get('title'), 24)}｜{money(item.get('price_yuan'))}"
            f"｜{item.get('condition_tag') or '—'}｜{item.get('location') or '—'}"
        )
        lines.append(f"   {item.get('item_url') or ''}")

    # 性价比评分最高的几个（后端已算好 value_grade / recommendation）
    by_value = sorted(items, key=lambda x: float(x.get("value_score") or 0), reverse=True)[:3]
    if by_value:
        lines.append("\n⭐ 性价比榜：")
        for item in by_value:
            lines.append(
                f"  · {_clip(item.get('title'), 22)}｜{money(item.get('price_yuan'))}"
                f"｜{item.get('value_grade') or ''} {item.get('diff_from_median_pct', 0):+.1f}% vs 中位价"
            )
    return "\n".join(lines)


def build_fallback_summary(keyword: str, stats: dict, items: list) -> str:
    """不依赖大模型时的确定性摘要（LLM 不可用时的兜底）。"""
    if not stats:
        return f"「{keyword}」搜索完成，但没有拿到统计数据。"

    sample = stats.get("sample_count", 0)
    median = stats.get("median_price_yuan", 0)
    lines = [
        f"📊 「{keyword}」行情小结（样本 {sample} 条）",
        f"中位价 {money(median)}｜均价 {money(stats.get('avg_price_yuan'))}",
        f"区间 {money(stats.get('min_price_yuan'))} ~ {money(stats.get('max_price_yuan'))}",
        f"四分位 {money(stats.get('p25_price_yuan'))} ~ {money(stats.get('p75_price_yuan'))}",
    ]
    if stats.get("suggested_listing_range"):
        lines.append(f"定价建议：{stats['suggested_listing_range']}")

    cheaper = [i for i in items if float(i.get("price_yuan") or 0) < float(median or 0)]
    if cheaper:
        lines.append(
            f"\n低于中位价的商品有 {len(cheaper)} 个，最低 {money(min(float(i.get('price_yuan') or 0) for i in items))}，"
            f"想淘便宜货可以优先看下面几条。"
        )
    high_value = [i for i in items if str(i.get("value_grade")) in ("S", "A")]
    if high_value:
        lines.append(f"其中性价比评级 S/A 的有 {len(high_value)} 个。")
    return "\n".join(lines)


def build_llm_prompt(keyword: str, stats: dict, items: list, opts: Optional[dict] = None) -> str:
    """给大模型的输入：结构化但足够紧凑，避免 30 条长标题把上下文撑爆。"""
    opts = opts or {}
    lines = [
        f"关键词：{keyword}{_filter_note(opts)}",
        "",
        "【价格统计】",
        f"样本数 {stats.get('sample_count', 0)}，中位价 {stats.get('median_price_yuan')} 元，"
        f"均价 {stats.get('avg_price_yuan')} 元，最低 {stats.get('min_price_yuan')} 元，"
        f"最高 {stats.get('max_price_yuan')} 元，P25 {stats.get('p25_price_yuan')} 元，"
        f"P75 {stats.get('p75_price_yuan')} 元，后端定价建议：{stats.get('suggested_listing_range')}",
        "",
        "【商品样本（最多 12 条）】",
    ]
    for index, item in enumerate(items[:12], 1):
        lines.append(
            f"{index}. {_clip(item.get('title'), 40)} | {item.get('price_yuan')}元 | "
            f"{item.get('condition_tag') or '未知成色'} | {item.get('seller_credit') or '未知信用'} | "
            f"{item.get('location') or '未知地区'} | 想要{item.get('want_count', 0)}人 | "
            f"相对中位价{item.get('diff_from_median_pct', 0):+.1f}% | 性价比{item.get('value_grade') or '-'}"
            f"({item.get('value_score', 0)})"
        )
    return "\n".join(lines)


SUMMARY_SYSTEM_PROMPT = (
    "你是闲鱼二手行情分析师，服务于一个自动发货平台的卖家。"
    "用户会给你一次闲鱼关键词搜索的结构化结果，请用简体中文写一段可以直接发到 QQ 的行情小结。"
    "要求：1) 300 字以内，不用 Markdown 标题、不用表格、不用代码块；"
    "2) 先说价格分布（中位价、区间、主流成交带），再说值得关注的便宜货或高性价比商品；"
    "3) 如果用户是卖家视角，补一句定价/挂单价位建议；4) 不要编造样本之外的数据，不要说“根据数据分析”这类空话。"
)
